import argparse
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from tenk_agent import gold
from tenk_agent.corpus import COMPANIES, FISCAL_YEARS, company_by_ticker
from tenk_agent.edgar import EdgarClient, EdgarError, fetch_company
from tenk_agent.ingest import coverage_problems, db_path, ingest
from tenk_agent.store import Store

EXPECTED_FILINGS = len(COMPANIES) * len(FISCAL_YEARS)
V0_GOLD_TARGET = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tenk")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="download the corpus 10-Ks from EDGAR")
    fetch.add_argument("--tickers", nargs="*", help="subset of corpus tickers (default: all)")
    fetch.add_argument("--refresh", action="store_true", help="re-download cached files")

    ingest_cmd = commands.add_parser("ingest", help="parse and chunk filings into the database")
    ingest_cmd.add_argument("--tickers", nargs="*", help="subset of corpus tickers (default: all)")

    gold_cmd = commands.add_parser("gold", help="review gold questions (default: list all)")
    gold_actions = gold_cmd.add_subparsers(dest="gold_action")
    show = gold_actions.add_parser("show", help="print a question with its source text")
    show.add_argument("id")
    verify = gold_actions.add_parser("verify", help="record your sign-off on questions")
    verify.add_argument("ids", nargs="+")
    verify.add_argument("--by", required=True, help="reviewer initials")
    commands.add_parser("check", help="check the V0 exit criteria")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    handlers = {"fetch": _fetch, "ingest": _ingest, "gold": _gold, "check": _check}
    return handlers[args.command](args)


def _fetch(args: argparse.Namespace) -> int:
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        print("Set SEC_USER_AGENT, e.g. 'Name you@example.com'", file=sys.stderr)
        return 2

    companies = [company_by_ticker(t) for t in args.tickers] if args.tickers else list(COMPANIES)
    client = EdgarClient(user_agent)
    manifest_path = args.data_dir / "raw" / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    failed = False
    for company in companies:
        try:
            filings = fetch_company(client, company, FISCAL_YEARS, args.data_dir, args.refresh)
        except EdgarError as error:
            logging.error("%s", error)
            failed = True
            continue
        manifest[company.ticker] = [asdict(f) for f in filings]

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return 1 if failed else 0


def _ingest(args: argparse.Namespace) -> int:
    tickers = [t.upper() for t in args.tickers] if args.tickers else None
    store = ingest(args.data_dir, tickers)
    for problem in coverage_problems(store, EXPECTED_FILINGS):
        logging.warning("%s", problem)
    return 0


def _gold(args: argparse.Namespace) -> int:
    if args.gold_action == "show":
        return _gold_show(args)
    if args.gold_action == "verify":
        return _gold_verify(args)
    return _gold_list(args)


def _gold_list(args: argparse.Namespace) -> int:
    """One line per question: verification, evidence resolution and XBRL cross-check."""
    store = Store(db_path(args.data_dir))
    for q in gold.load():
        resolved = gold.resolve_evidence(store, q)
        checks = gold.xbrl_checks(q, args.data_dir)
        status = "verified  " if gold.is_verified(q) else "UNVERIFIED"
        evidence = f"evidence {sum(map(bool, resolved))}/{len(resolved)}" if resolved else ""
        matched = sum(c.matched_concept is not None for c in checks)
        xbrl = f"xbrl {matched}/{len(checks)}" if checks else ""
        print(f"{status} {q['split']:4} {q['id']:36} {evidence:14} {xbrl}")
    return 0


def _gold_show(args: argparse.Namespace) -> int:
    """Everything a reviewer needs to verify one question against the filing."""
    q = next((q for q in gold.load() if q["id"] == args.id), None)
    if q is None:
        print(f"No gold question {args.id!r}", file=sys.stderr)
        return 1
    store = Store(db_path(args.data_dir))
    kind = q["category"] + (f" / {q['failure_mode']}" if q.get("failure_mode") else "")
    print(f"{q['id']}  [{q['split']}, {kind}, {q['origin']}]")
    print(f"Q: {q['question']}")
    for value in gold.expected_values(q):
        who = " ".join(str(value[k]) for k in ("company", "fiscal_year") if k in value)
        label = f"expected {who}".strip()
        print(f"  {label}: {value['value']:,} {value['unit']}")
    for point in q.get("required_points", []):
        print(f"  must say: {point}")
    if q.get("should_abstain"):
        print("  expected: abstain")
    if q.get("notes"):
        print(f"Notes: {q['notes']}")

    sources = q.get("sources", []) + q.get("acceptable_sources", [])
    for source, chunk_ids in zip(sources, gold.resolve_evidence(store, q), strict=True):
        print(
            f"\nSource: {source['company']} FY{source['fiscal_year']} Item {source['item']}"
            f" — {source['section']}"
        )
        print(f"  filing:   {store.filing_url(source['company'], source['fiscal_year'])}")
        print(f"  evidence: {source['evidence']}")
        print(f"  found in: {', '.join(chunk_ids) or 'NOT FOUND'}")

    for check in gold.xbrl_checks(q, args.data_dir):
        match = check.matched_concept or "no matching fact"
        print(f"XBRL: {check.value:,} {check.unit} -> {match}")
    print(f"\nVerified: {q.get('verified_by') or 'no'} {q.get('verified_on') or ''}".rstrip())
    return 0


def _gold_verify(args: argparse.Namespace) -> int:
    questions = gold.load()
    unknown = gold.mark_verified(questions, args.ids, args.by, date.today())
    if unknown:
        print(f"Unknown question IDs (nothing saved): {', '.join(unknown)}", file=sys.stderr)
        return 1
    gold.save(questions)
    print(f"Marked {len(args.ids)} question(s) verified by {args.by}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """V0 exit criteria: all filings ingested with Items 1A/7/8, and 30 verified gold questions."""
    store = Store(db_path(args.data_dir))
    questions = gold.load()
    problems = coverage_problems(store, EXPECTED_FILINGS) + gold.validate(questions)

    for q in questions:
        # resolve_evidence covers primary then acceptable sources; only primaries must resolve.
        primary = zip(q.get("sources", []), gold.resolve_evidence(store, q), strict=False)
        for source, chunk_ids in primary:
            if not chunk_ids:
                problems.append(
                    f"{q['id']}: evidence not found in "
                    f"{source['company']} FY{source['fiscal_year']}"
                )

    verified = sum(map(gold.is_verified, questions))
    if verified < V0_GOLD_TARGET:
        problems.append(f"{verified}/{V0_GOLD_TARGET} gold questions verified")

    categories = Counter(q["category"] for q in questions)
    print(f"Filings ingested: {len(store.coverage())}/{EXPECTED_FILINGS}")
    print(
        f"Gold questions: {len(questions)} ({verified} verified) — "
        + ", ".join(f"{name} {count}" for name, count in sorted(categories.items()))
    )
    for problem in problems:
        print(f"  - {problem}")
    print("V0 complete" if not problems else f"V0 incomplete: {len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
