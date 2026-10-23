"""tenk: the V0–V2 pipeline from the command line. Settings: config.yaml (--config, TENK_CONFIG).

tenk fetch                     download the corpus 10-Ks from EDGAR
tenk build                     V0 preprocess + chunk every filing, then V1's database + vectors
tenk search "question"         V1: sub-questions and the top k chunks
tenk ask "question"            V2: one cited answer
tenk chat                      V2: type questions, get answers (models loaded once)
tenk gold [show|render|verify] review a gold question set
tenk report LABEL              write a finished V2 run's end-to-end report to evaluation/results
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from tenk_agent import config as config_file
from tenk_agent.corpus import COMPANIES, FISCAL_YEARS, company_by_ticker
from tenk_agent.store import ChunkRecord, Store

GOLD = Path("evaluation/gold/generation-gold-fy2025.jsonl")
RUNS = Path("output/generation")  # where experiments/v2/run.py writes a run
RESULTS = Path("evaluation/results")  # the reports kept with the gold sets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tenk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, help="default: $TENK_CONFIG or config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="download the corpus 10-Ks from EDGAR")
    fetch.add_argument("--tickers", nargs="*", help="subset of corpus tickers (default: all)")
    fetch.add_argument("--refresh", action="store_true", help="re-download cached files")

    build = commands.add_parser("build", help="V0 chunks of every filing → V1 database")
    stages = ("chunk", "load", "embed", "pages")  # build.STAGES
    build.add_argument(
        "--stage", action="append", choices=stages, help="default: chunk, load, embed"
    )
    build.add_argument("--filing", action="append", help="TICKER/FYyear (default: all fetched)")
    build.add_argument("--method", action="append", help="default: build.methods")
    build.add_argument("--jobs", type=int, default=8, help="chunk: filings at once")
    build.add_argument("--out", type=Path, help="default: build.out")

    search = commands.add_parser("search", help="V1: the chunks that answer a question")
    search.add_argument("question")
    search.add_argument("-k", type=int, help="default: retrieval.k")

    for name, text in (("ask", "V2: answer one question"), ("chat", "V2: answer typed questions")):
        mode = commands.add_parser(name, help=text)
        if name == "ask":
            mode.add_argument("question")
            mode.add_argument("--json", action="store_true", help="print the answer's JSON")
        mode.add_argument("--model", help="default: generation.model")

    gold = commands.add_parser("gold", help="review a gold question set (default: list it)")
    gold.add_argument("--gold", type=Path, default=GOLD, help="default: %(default)s")
    gold_actions = gold.add_subparsers(dest="gold_action")
    show = gold_actions.add_parser("show", help="print a question with its source text")
    show.add_argument("id")
    render = gold_actions.add_parser("render", help="the set as a full PDF plus a summary")
    render.add_argument("--out", type=Path, help="default: next to the set")
    verify = gold_actions.add_parser("verify", help="record your sign-off on questions")
    verify.add_argument("ids", nargs="+")
    verify.add_argument("--by", required=True, help="reviewer initials")

    report = commands.add_parser("report", help="a V2 run's end-to-end report")
    report.add_argument("label", help=f"a run in {RUNS}/")
    report.add_argument("--gold", type=Path, default=GOLD, help="default: %(default)s")
    report.add_argument("--context", default="retrieved", help="default: %(default)s")
    report.add_argument("--out", type=Path, help=f"default: {RESULTS}/<label>")

    args = parser.parse_args(argv)
    settings = config_file.load(args.config)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "sentence_transformers", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    handlers = {
        "fetch": _fetch,
        "build": _build,
        "search": _search,
        "ask": _ask,
        "chat": _chat,
        "gold": _gold,
        "report": _report,
    }
    return handlers[args.command](args, settings)


# --- fetch, build ----------------------------------------------------------------------------


def _fetch(args: argparse.Namespace, settings: dict) -> int:
    from tenk_agent.acquisition import fetch

    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        print("Set SEC_USER_AGENT, e.g. 'Name you@example.com'", file=sys.stderr)
        return 2
    companies = [company_by_ticker(t) for t in args.tickers] if args.tickers else list(COMPANIES)
    ok = fetch(companies, FISCAL_YEARS, Path(settings["data_dir"]), user_agent, args.refresh)
    return 0 if ok else 1


def _build(args: argparse.Namespace, settings: dict) -> int:
    from tenk_agent.build import build, filing_dirs

    filings = filing_dirs(Path(settings["data_dir"]), args.filing)
    out = args.out or Path(settings["build"]["out"])
    methods = args.method or settings["build"]["methods"]
    build(filings, out, settings, methods, args.stage or ["chunk", "load", "embed"], args.jobs)
    return 0


# --- search, ask, chat -------------------------------------------------------------------------


def _retriever(settings: dict):
    from tenk_agent.retrieval.search import SubQuestionRetriever

    store = Store(Path(settings["db"]))
    return store, SubQuestionRetriever.from_settings(store, settings["retrieval"])


def _search(args: argparse.Namespace, settings: dict) -> int:
    store, retriever = _retriever(settings)
    result = retriever.search(args.question, args.k or settings["retrieval"]["k"])
    for s in result.searches:
        where = f"{s.ticker or 'all companies'} {s.fiscal_years or 'all years'}"
        print(f"sub-question ({where}): {s.query}")
    print()
    scores = {cid: score for s in result.searches for cid, score in s.reranked}
    for rank, c in enumerate(result.chunks, 1):
        preview = " ".join(c.text.split())[:300]
        print(f"{rank}. {c.id} [{c.kind}] rerank {scores.get(c.id, 0):.2f}\n   {preview}\n")
    return 0


def _ask(args: argparse.Namespace, settings: dict) -> int:
    answer = _answerer(args, settings)
    result, chunks = answer(args.question)
    if args.json:
        print(json.dumps(result.answer | {"chunks": [c.id for c in chunks]}, indent=2))
    else:
        show(result, chunks)
    return 0


def _chat(args: argparse.Namespace, settings: dict) -> int:
    import readline  # noqa: F401  (line editing and history for input())

    print("loading retrieval and model ...", flush=True)
    answer = _answerer(args, settings)
    generation = settings["generation"]
    print(
        f"{generation['model']} · k {settings['retrieval']['k']} · empty line or Ctrl-D to quit\n"
    )
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question in ("", "exit", "quit"):
            return 0
        try:
            result, chunks = answer(question)
        except KeyboardInterrupt:
            print("\n(interrupted)\n")
            continue
        except Exception as e:  # a dropped server shouldn't end the session
            hint = f" (is {generation['model']} served at {generation['base_url']}?)"
            hint = hint if "Connection" in type(e).__name__ else ""
            print(f"\nerror: {type(e).__name__}: {e}{hint}\n")
            continue
        show(result, chunks)


def _answerer(args: argparse.Namespace, settings: dict):
    """question → (Generation, chunks shown): V1's top k, then one V2 model call."""
    from tenk_agent.generation.generate import build_prompt, generate
    from tenk_agent.models import get_model

    generation = settings["generation"]
    store, retriever = _retriever(settings)
    model = get_model(
        args.model or generation["model"],
        base_url=generation["base_url"],
        thinking=generation["thinking"],
    )
    prompt = Path(generation["prompt"])

    def answer(question: str):
        chunks = retriever.search(question, settings["retrieval"]["k"]).chunks
        system, user = build_prompt(question, chunks, store, prompt)
        return generate(model, system, user, store, generation["max_tokens"]), chunks

    return answer


def show(result, chunks: list[ChunkRecord]) -> None:
    """The chunks shown, the answer (with its table and scope notice), each claim's status and
    citations, and the call's time and tokens."""
    answer = result.answer
    print(f"\nChunks ({len(chunks)})")
    for i, c in enumerate(chunks, 1):
        span = list(dict.fromkeys(map(str, c.printed_pages or c.pages or [])))  # 36-36 → 36
        pages = f" p.{'-'.join(span)}" if span else ""
        print(f"  {i:>2}. {c.id}  {c.ticker} FY{c.fiscal_year} item {c.item} {c.kind}{pages}")

    flags = [
        f
        for f, on in (("DECLINED", answer["abstained"]), ("FORMAT ERROR", answer["format_error"]))
        if on
    ]
    print(f"\nAnswer{' [' + ', '.join(flags) + ']' if flags else ''}\n")
    print(answer["answer"])
    if answer.get("table"):
        print("\n" + _table(answer["table"]))
    if answer.get("scope_notice"):
        print(f"\nScope: {answer['scope_notice']}")

    if answer["claims"]:
        print(f"\nClaims ({len(answer['claims'])})")
        for c in answer["claims"]:
            unit = f" {c['unit']}" if c["unit"] else ""
            value = f" = {c['value']}{unit}" if c["value"] is not None else ""
            print(f"  [{c['id']}] {c['status']:<10} {c['text']}{value}")
            reason = f" · {c['status_reason']}" if c["status_reason"] else ""
            print(f"       cites {', '.join(c['chunk_ids']) or '–'}{reason}")

    retry = " · JSON retry" if len(result.replies) > 1 else ""
    tokens = f"{result.input_tokens} in / {result.output_tokens} out"
    print(f"\n{result.seconds:.1f}s · {tokens}{retry}\n")


def _table(table: dict) -> str:
    """The answer's table ({columns, rows}) as aligned text; anything else as-is."""
    columns, rows = table.get("columns"), table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return str(table)
    cells = [[str(c) for c in columns]] + [[str(v) for v in r] for r in rows if isinstance(r, list)]
    widths = [max(len(r[i]) for r in cells if i < len(r)) for i in range(len(columns))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(r, widths, strict=False)) for r in cells]
    return "\n".join([lines[0], "  ".join("-" * w for w in widths), *lines[1:]])


# --- gold --------------------------------------------------------------------------------------


def _gold(args: argparse.Namespace, settings: dict) -> int:
    handlers = {"show": _gold_show, "render": _gold_render, "verify": _gold_verify}
    return handlers.get(args.gold_action, _gold_list)(args, settings)


def _gold_list(args: argparse.Namespace, settings: dict) -> int:
    """One line per question: verification, evidence resolution and XBRL cross-check."""
    from tenk_agent.evaluation import gold

    store = Store(Path(settings["db"]))
    for q in gold.load(args.gold):
        resolved = gold.resolve_evidence(store, q)
        checks = gold.xbrl_checks(q, Path(settings["data_dir"]))
        status = "verified  " if gold.is_verified(q) else "UNVERIFIED"
        evidence = f"evidence {sum(map(bool, resolved))}/{len(resolved)}" if resolved else ""
        matched = sum(c.matched_concept is not None for c in checks)
        xbrl = f"xbrl {matched}/{len(checks)}" if checks else ""
        print(f"{status} {q['split']:4} {q['id']:40} {evidence:14} {xbrl}")
    return 0


def _gold_show(args: argparse.Namespace, settings: dict) -> int:
    """Everything a reviewer needs to verify one question against the filing."""
    from tenk_agent.evaluation import gold

    q = next((q for q in gold.load(args.gold) if q["id"] == args.id), None)
    if q is None:
        print(f"No gold question {args.id!r}", file=sys.stderr)
        return 1
    store = Store(Path(settings["db"]))
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

    for check in gold.xbrl_checks(q, Path(settings["data_dir"])):
        match = check.matched_concept or "no matching fact"
        print(f"XBRL: {check.value:,} {check.unit} -> {match}")
    print(f"\nVerified: {q.get('verified_by') or 'no'} {q.get('verified_on') or ''}".rstrip())
    return 0


def _gold_render(args: argparse.Namespace, settings: dict) -> int:
    from tenk_agent.evaluation.render import render_pdf, render_summary_pdf

    out = args.out or args.gold.with_suffix(".pdf")
    store = Store(Path(settings["db"]))
    count = render_pdf(args.gold, store, Path(settings["data_dir"]), out)
    summary = out.with_name(f"{out.stem}-summary.pdf")
    render_summary_pdf(args.gold, summary)
    print(f"Wrote {count} questions to {out} and the summary to {summary}")
    return 0


def _gold_verify(args: argparse.Namespace, settings: dict) -> int:
    from tenk_agent.evaluation import gold

    questions = gold.load(args.gold)
    unknown = gold.mark_verified(questions, args.ids, args.by, date.today())
    if unknown:
        print(f"Unknown question IDs (nothing saved): {', '.join(unknown)}", file=sys.stderr)
        return 1
    gold.save(questions, args.gold)
    print(f"Marked {len(args.ids)} question(s) verified by {args.by}")
    return 0


# --- report ------------------------------------------------------------------------------------


def _report(args: argparse.Namespace, settings: dict) -> int:
    import shutil

    import yaml

    from tenk_agent.evaluation import gold
    from tenk_agent.evaluation.report import build_report

    run_dir = RUNS / args.label
    if not (run_dir / "results.json").exists():
        print(f"No run {args.label!r} in {RUNS}/", file=sys.stderr)
        return 1
    used = yaml.safe_load((run_dir / "config.yaml").read_text())  # the settings the run used
    questions = gold.load(args.gold)
    if not used["score"]["restatements"]:
        questions = [q | {"acceptable_sources": []} for q in questions]
    rows = json.loads((run_dir / "results.json").read_text())
    text = build_report(args.label, rows, questions, Store(Path(used["run"]["db"])), args.context)
    out = args.out or RESULTS / args.label
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(text)
    shutil.copy(run_dir / "scores.json", out / "scores.json")
    shutil.copy(run_dir / "config.yaml", out / "config.yaml")
    print(f"Wrote {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
