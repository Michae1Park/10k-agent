#!/usr/bin/env python3
"""V2 chat: type a question, get the V2 answer. The same pipeline as run.py's `retrieved`
context (V1's top k ─► one model call ─► claims checked against their cited chunks), loaded
once and kept for every question.

  taskset -c 16-31 python experiments/v2/v2_src/chat.py
  taskset -c 16-31 python experiments/v2/v2_src/chat.py --model openai:Qwen/Qwen3.5-9B

Needs the model served (scripts/serve_vllm.sh qwen3.6-27b-fp8) and the GPU for V1's embedder
and reranker. Empty line, `exit` or Ctrl-D quits.
"""

import argparse
import readline  # noqa: F401  (line editing and history for input())
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate import build_prompt, generate  # noqa: E402
from run import CONFIG, ROOT, v1_pipeline  # noqa: E402

from tenk_agent.models import get_model  # noqa: E402
from tenk_agent.store import ChunkRecord, Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, default=CONFIG, help="default: %(default)s")
    ap.add_argument("--model", help="override generation.model")
    args = ap.parse_args()
    config = yaml.safe_load(args.config.read_text())
    run, generation = config["run"], config["generation"]
    if args.model:
        generation["model"] = args.model
    retrieval = yaml.safe_load((ROOT / config["retrieval"]).read_text())["retrieval"]

    print("loading retrieval and model ...", flush=True)
    store = Store(ROOT / run["db"])
    pipeline = v1_pipeline(store, retrieval)
    model = get_model(
        generation["model"], base_url=generation["base_url"], thinking=generation["thinking"]
    )
    prompt = ROOT / generation["prompt"]
    print(f"{generation['model']} · k {retrieval['k']} · empty line or Ctrl-D to quit\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question in ("", "exit", "quit"):
            return
        try:
            chunks = pipeline.search(question, retrieval["k"]).chunks
            system, user = build_prompt(prompt, question, chunks, store)
            result = generate(model, system, user, store, generation["max_tokens"])
        except KeyboardInterrupt:
            print("\n(interrupted)\n")
            continue
        except Exception as e:  # a dropped server shouldn't end the session
            hint = f" (is {generation['model']} served at {generation['base_url']}?)"
            print(f"\nerror: {type(e).__name__}: {e}{hint if 'Connection' in type(e).__name__ else ''}\n")
            continue
        show(result, chunks)


def show(result, chunks: list[ChunkRecord]) -> None:
    answer = result.answer
    print(f"\nChunks ({len(chunks)})")
    for i, c in enumerate(chunks, 1):
        span = list(dict.fromkeys(map(str, c.printed_pages or c.pages or [])))  # 36-36 → 36
        pages = f" p.{'-'.join(span)}" if span else ""
        print(f"  {i:>2}. {c.id}  {c.ticker} FY{c.fiscal_year} item {c.item} {c.kind}{pages}")

    flags = [f for f, on in (("DECLINED", answer["abstained"]),
                             ("FORMAT ERROR", answer["format_error"])) if on]
    print(f"\nAnswer{' [' + ', '.join(flags) + ']' if flags else ''}\n")
    print(answer["answer"])
    if answer.get("table"):
        print("\n" + _table(answer["table"]))
    if answer.get("scope_notice"):
        print(f"\nScope: {answer['scope_notice']}")

    if answer["claims"]:
        print(f"\nClaims ({len(answer['claims'])})")
        for c in answer["claims"]:
            value = f" = {c['value']}{' ' + c['unit'] if c['unit'] else ''}" if c["value"] is not None else ""
            print(f"  [{c['id']}] {c['status']:<10} {c['text']}{value}")
            print(f"       cites {', '.join(c['chunk_ids']) or '–'}"
                  f"{' · ' + c['status_reason'] if c['status_reason'] else ''}")

    retry = " · JSON retry" if len(result.replies) > 1 else ""
    print(f"\n{result.seconds:.1f}s · {result.input_tokens} in / {result.output_tokens} out{retry}\n")


def _table(table: dict) -> str:
    """The answer's table ({columns, rows}) as aligned text; anything else as-is."""
    columns, rows = table.get("columns"), table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return str(table)
    cells = [[str(c) for c in columns]] + [[str(v) for v in r] for r in rows if isinstance(r, list)]
    widths = [max(len(r[i]) for r in cells if i < len(r)) for i in range(len(columns))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(r, widths)) for r in cells]
    return "\n".join([lines[0], "  ".join("-" * w for w in widths), *lines[1:]])


if __name__ == "__main__":
    main()
