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
from tenk_agent.chunking import ChunkSizes
from tenk_agent.config import Settings
from tenk_agent.corpus import COMPANIES, FISCAL_YEARS, company_by_ticker
from tenk_agent.edgar import EdgarClient, EdgarError, fetch_company
from tenk_agent.ingest import coverage_problems, db_path, ingest
from tenk_agent.store import Store

EXPECTED_FILINGS = len(COMPANIES) * len(FISCAL_YEARS)
V0_GOLD_TARGET = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tenk")
    parser.add_argument("--data-dir", type=Path, help="default: config.yaml data_dir")
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

    commands.add_parser("embed", help="embed chunks for dense retrieval (TENK_EMBEDDER)")
    search = commands.add_parser("search", help="search the filings")
    search.add_argument("query")
    search.add_argument("--tickers", nargs="*")
    search.add_argument("--years", nargs="*", type=int)
    search.add_argument("--items", nargs="*")
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--mode", choices=("keyword", "dense", "hybrid"))
    search.add_argument("--reranker", help="reranker spec, or 'none'")

    for name, help_text in (
        ("ask", "answer a question from retrieved passages (Ask mode)"),
        ("research", "answer with the research agent (Research mode)"),
    ):
        mode = commands.add_parser(name, help=help_text)
        mode.add_argument("question")
        mode.add_argument("--model", help="model spec, e.g. anthropic:claude-sonnet-5")
        mode.add_argument("--json", action="store_true", help="print the full answer JSON")
        mode.add_argument("--no-verify", action="store_true", help="skip the verification pass")

    serve = commands.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    eval_cmd = commands.add_parser("eval", help="run and report evaluations")
    eval_actions = eval_cmd.add_subparsers(dest="eval_action", required=True)
    run = eval_actions.add_parser("run", help="run a system over the gold set")
    run.add_argument("system", choices=("retrieval", "ask", "research"))
    run.add_argument("--label", help="run name (default: <system>-<timestamp>)")
    run.add_argument("--split", default="dev", help="dev, test or all (V7 questions: heldout)")
    run.add_argument("--gold", type=Path, help="question file (default: the gold set)")
    run.add_argument("--ids", nargs="*", help="only these question IDs")
    run.add_argument("--limit", type=int)
    run.add_argument(
        "--include-unverified",
        action="store_true",
        help="also run draft (unverified) questions; results are marked as such",
    )
    run.add_argument("--no-judge", action="store_true", help="skip LLM-judge metrics")
    run.add_argument("--no-verify", action="store_true", help="skip the verification pass")
    run.add_argument(
        "--no-repair", action="store_true", help="don't let the agent fix failed claims"
    )
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--resume", action="store_true", help="continue an interrupted run")
    run.add_argument("--model", help="model spec (default: TENK_MODEL)")
    run.add_argument("--mode", choices=("keyword", "dense", "hybrid"), help="retrieval mode")
    run.add_argument("--reranker", help="reranker spec, or 'none'")
    run.add_argument(
        "--no-slot-search",
        action="store_true",
        help="one search, not one per company and filing named in the question",
    )
    run.add_argument("--structured-data", action="store_true", help="V5: add the XBRL tool")
    rescore = eval_actions.add_parser("rescore", help="recompute deterministic metrics of a run")
    rescore.add_argument("label")
    report = eval_actions.add_parser("report", help="results table from runs")
    report.add_argument("columns", nargs="+", help="NAME=run-label, e.g. V1=v1-retrieval")
    report.add_argument("--out", type=Path, help="also write the table to this file")
    compare = eval_actions.add_parser(
        "compare", help="baseline vs other runs: intervals, paired tests, error analysis, charts"
    )
    compare.add_argument("columns", nargs="+", help="NAME=run-label; the first is the baseline")
    compare.add_argument("--out", type=Path, help="report path without extension")
    calibrate = eval_actions.add_parser("calibrate", help="LLM-judge calibration")
    calibrate.add_argument("action", choices=("export", "score"))
    calibrate.add_argument("label", help="run label")
    calibrate.add_argument("--answers", type=int, default=30)

    args = parser.parse_args(argv)
    args.data_dir = args.data_dir or Settings.load().data_dir
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "sentence_transformers", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    handlers = {
        "fetch": _fetch,
        "ingest": _ingest,
        "gold": _gold,
        "check": _check,
        "embed": _embed,
        "search": _search,
        "ask": _answer,
        "research": _answer,
        "serve": _serve,
        "eval": _eval,
    }
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
    s = _settings(args)
    store = ingest(
        args.data_dir, tickers, ChunkSizes(s.chunk_chars, s.table_chars, s.overlap_chars)
    )
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


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.load()
    settings.data_dir = args.data_dir
    return settings


def _embed(args: argparse.Namespace) -> int:
    from tenk_agent.embeddings import get_embedder
    from tenk_agent.retrieval import embed_corpus

    settings = _settings(args)
    store = Store(settings.db_path)
    embedded = embed_corpus(store, get_embedder(settings.embedder, settings.device))
    print(f"Embedded {embedded} chunks; {store.embedding_count()} total ({settings.embedder})")
    return 0


def _search(args: argparse.Namespace) -> int:
    from tenk_agent.retrieval import Retriever

    settings = _settings(args)
    overrides = {k: v for k, v in (("mode", args.mode), ("reranker", args.reranker)) if v}
    retriever = Retriever.from_settings(Store(settings.db_path), settings, **overrides)
    hits = retriever.search(args.query, args.tickers, args.years, args.items, k=args.k)
    for rank, chunk in enumerate(hits, 1):
        preview = " ".join(chunk.text.split())[:300]
        print(f"{rank}. {chunk.id} [{chunk.kind}]\n   {preview}\n")
    return 0


def _answer(args: argparse.Namespace) -> int:
    from tenk_agent.agent import research
    from tenk_agent.ask import ask
    from tenk_agent.models import load_model
    from tenk_agent.retrieval import Retriever
    from tenk_agent.tools import Toolbox

    settings = _settings(args)
    store = Store(settings.db_path)
    retriever = Retriever.from_settings(store, settings)
    model = load_model(settings, args.model)
    verify = settings.verify and not args.no_verify
    if args.command == "ask":
        answer = ask(args.question, store, retriever, model, verify=verify, k=settings.ask_k)
    else:
        toolbox = Toolbox.from_settings(store, retriever, settings)
        answer = None
        for event in research(
            args.question,
            store,
            toolbox,
            model,
            settings.max_tool_calls,
            verify=verify,
            repair=settings.repair,
            repair_calls=settings.repair_calls,
        ):
            if event["type"] == "step":
                mark = "✗" if event["is_error"] else "✓"
                print(f"{mark} {event['label']}\n    {event['summary']}", file=sys.stderr)
            elif event["type"] == "error":
                print(f"Error: {event['message']}", file=sys.stderr)
                return 1
            elif event["type"] == "answer":
                answer = event["answer"]
    if args.json:
        print(json.dumps(answer, indent=2))
    else:
        print_answer(answer)
    return 0


def print_answer(answer: dict) -> None:
    print(answer["answer"])
    if answer.get("scope_notice"):
        print(f"\nScope: {answer['scope_notice']}")
    for claim in answer["claims"]:
        cites = ", ".join(claim["chunk_ids"]) or "no citation"
        print(f"  [{claim['id']}] {claim['status']:10} {claim['text']}  ({cites})")
    usage = answer["usage"]
    print(
        f"\n{answer['model']} · {usage['latency_s']:.1f}s · {usage['input_tokens']:,} in / "
        f"{usage['output_tokens']:,} out · ${usage['cost_usd']:.4f} · trace {answer['trace_id']}"
    )


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("tenk_agent.api:app", host=args.host, port=args.port)
    return 0


def _eval(args: argparse.Namespace) -> int:
    from tenk_agent.evaluation import calibrate, report, runner

    if args.eval_action == "report":
        columns = [tuple(c.split("=", 1)) if "=" in c else (c, c) for c in args.columns]
        text = report.write(columns, args.out) if args.out else report.table(columns)
        print(text)
        return 0
    if args.eval_action == "rescore":
        summary = runner.rescore(args.label, _settings(args))
        print(json.dumps({k: v for k, v in summary.items() if k != "config"}, indent=2))
        return 0
    if args.eval_action == "compare":
        from tenk_agent.evaluation import compare

        columns = [tuple(c.split("=", 1)) if "=" in c else (c, c) for c in args.columns]
        if len(columns) < 2:
            raise SystemExit("compare needs a baseline and at least one other run")
        md, page = compare.write(columns, args.out)
        print(md.read_text())
        print(f"Report: {md} · {page}")
        return 0
    if args.eval_action == "calibrate":
        if args.action == "export":
            print(f"Wrote {calibrate.export(args.label, args.answers)}; fill in 'human'.")
        else:
            print(json.dumps(calibrate.score(args.label), indent=2))
        return 0

    settings = _settings(args)
    if args.model:
        settings.model = args.model
    if args.structured_data:
        settings.structured_data = True
    overrides = {k: v for k, v in (("mode", args.mode), ("reranker", args.reranker)) if v}
    if args.no_slot_search:
        overrides["slot_search"] = False
    label = args.label or f"{args.system}-{date.today().isoformat()}"
    options = runner.RunOptions(
        system=args.system,
        label=label,
        split=None if args.split == "all" else args.split,
        ids=args.ids,
        include_unverified=args.include_unverified,
        judge=not args.no_judge,
        verify=settings.verify and not args.no_verify,
        workers=args.workers,
        limit=args.limit,
        retrieval_overrides=overrides,
        max_tool_calls=settings.max_tool_calls,
        resume=args.resume,
        repair=settings.repair and not args.no_repair,
        repair_calls=settings.repair_calls,
        ask_k=settings.ask_k,
        gold_path=args.gold or gold.GOLD_PATH,
    )
    summary = runner.run(options, settings)
    print(json.dumps({k: v for k, v in summary.items() if k != "config"}, indent=2))
    print(f"Results: {runner.RUNS_DIR / label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
