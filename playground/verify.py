#!/usr/bin/env python3
"""Stage 5 playground: is this number really in that chunk? Same check as the verification pass.
Guide: docs/STAGE5_VERIFY.md. No GPU, no model.

  python playground/verify.py --chunk AAPL-FY2024-8-002 --value 31370 --unit "USD millions"
  python playground/verify.py --chunk AAPL-FY2024-8-002 --value 31.37 --unit "USD billions"   # unit normalization
  python playground/verify.py --chunk AAPL-FY2024-8-002 --value 31500 --unit "USD millions"   # not there
  python playground/verify.py --chunk AAPL-FY2025-8-002 --calc pct_change old=31370 new=34550 --value 10.1
  python playground/verify.py --trace <trace id>                                             # re-check a stored answer

Writes output/verify/<tag>.md.
"""

import argparse

from _common import preview, settings, table, write_report

from tenk_agent.store import Store
from tenk_agent.verify import chunk_scales, find_value, numbers_in, verify_claim


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--chunk", nargs="*", default=[], help="chunk ID(s) the claim cites")
    p.add_argument("--value", type=float)
    p.add_argument("--unit", help="USD millions, USD billions, USD, percent, count, ...")
    p.add_argument("--calc", nargs="+", metavar="OP name=value", help="a calculated claim")
    p.add_argument("--trace", help="re-verify every claim of a stored answer")
    p.add_argument("--tag", default="play")
    a = p.parse_args()
    store = Store(s.db_path)

    if a.trace:
        trace = store.get_trace(a.trace)
        claims = trace["output"]["claims"]
    else:
        claim = {"text": "", "value": a.value, "unit": a.unit, "chunk_ids": a.chunk}
        if a.calc:
            op, *pairs = a.calc
            inputs = {k: float(v) for k, _, v in (pair.partition("=") for pair in pairs)}
            claim["calculation"] = {"op": op, "inputs": inputs, "input_unit": "USD millions"}
        claims = [claim]

    out = []
    for claim in claims:
        result = verify_claim(dict(claim), store)
        print(
            f"claim: {claim.get('text') or claim['value']} {claim.get('unit') or ''} -> {result['status']}"
        )
        print(f"  why: {result['status_reason'] or '-'}")
        out.append(
            f"- **{result['status']}** · {claim.get('text') or claim['value']} · {result['status_reason']}"
        )
        for chunk_id in claim["chunk_ids"]:
            chunk = store.get_chunk(chunk_id)
            if chunk is None:
                print(f"  {chunk_id}: no such chunk")
                continue
            numbers = numbers_in(chunk.text)
            print(
                f"  {chunk_id}: {len(numbers)} numbers · bare numbers scaled by {chunk_scales(chunk)} "
                f"(units {chunk.units})"
            )
            if claim.get("value") is not None:
                span = find_value(claim["value"], claim.get("unit"), chunk)
                print(f"  match: {span!r}")
            rows = [[n.span, n.value, n.scale or "", n.percent] for n in numbers[:40]]
            out.append(
                f"\n`{chunk_id}` ({chunk.units}) · {preview(chunk.text, 200)}\n\n"
                + table(["Span", "Value", "Word scale", "Percent"], rows)
            )
    write_report("verify", a.tag, "# Verify\n\n" + "\n".join(out) + "\n")


if __name__ == "__main__":
    main()
