#!/usr/bin/env python3
"""Stage 4 playground, part 1: call the agent's tools by hand, no model. See exactly what the agent sees.
Guide: docs/STAGE4_AGENT.md.

  python playground/tools.py --list                                                   # schemas the model gets
  python playground/tools.py search_filings query="R&D expense" companies=Apple fiscal_years=2024
  python playground/tools.py get_filing_section company=Google fiscal_year=2025 item=7
  python playground/tools.py get_filing_section company=AAPL fiscal_year=2025 item=8 start=10
  python playground/tools.py calculator op=pct_change old=29915 new=31370
  python playground/tools.py calculator expression="a / b * 100" a=109158 b=416161
  python playground/tools.py verify_citation chunk_id=AAPL-FY2024-8-002 value=31.37 unit="USD billions"
  python playground/tools.py search_filings query=revenue companies=Oracle            # an error the agent sees

Arguments are name=value; lists take commas (companies=Apple,MSFT). Writes output/tools/<tag>.md.
"""

import argparse
import json

from _common import settings, write_report

from tenk_agent.agent import result_summary, step_label
from tenk_agent.retrieval import Retriever
from tenk_agent.store import Store
from tenk_agent.tools import CALCULATOR, Toolbox

LISTS = {"companies", "fiscal_years", "items"}
INTS = {"fiscal_year", "fiscal_years", "k", "start"}
CALC_FIELDS = {"op", "expression", "input_unit"}


def parse_args(tool: str, pairs: list[str]) -> dict:
    """name=value pairs -> the JSON arguments a model would send."""
    args: dict = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if name in LISTS:
            items = [v.strip() for v in value.split(",") if v.strip()]
            args[name] = [int(v) for v in items] if name in INTS else items
        elif name in INTS:
            args[name] = int(value)
        elif name == "value":
            args[name] = float(value)
        elif tool == CALCULATOR.name and name not in CALC_FIELDS:
            args.setdefault("inputs", {})[name] = float(value)  # calculator inputs: a=1 b=2
        else:
            args[name] = value
    return args


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("tool", nargs="?")
    p.add_argument("args", nargs="*", help="name=value")
    p.add_argument("--list", action="store_true", help="print the tool schemas")
    p.add_argument("--structured-data", action="store_true", help="include the V5 XBRL tool")
    p.add_argument("--device", default=s.device)
    p.add_argument("--tag", default="play")
    a = p.parse_args()

    s.device = a.device
    s.structured_data = a.structured_data or s.structured_data
    store = Store(s.db_path)
    lazy_retriever = a.tool == "search_filings"  # only searching needs the embedding models
    retriever = (
        Retriever.from_settings(store, s) if lazy_retriever else Retriever(store, mode="keyword")
    )
    toolbox = Toolbox.from_settings(store, retriever, s)

    if a.list or not a.tool:
        for spec in toolbox.specs:
            print(f"## {spec.name}\n{spec.description}\n{json.dumps(spec.parameters, indent=2)}\n")
        return

    arguments = parse_args(a.tool, a.args)
    result, is_error = toolbox.run(a.tool, arguments)
    print(f"label:   {step_label(a.tool, arguments)}")
    print(f"call:    {a.tool}({json.dumps(arguments)})")
    print(f"error:   {is_error}")
    print(f"summary: {result_summary(a.tool, result, is_error)}")
    pretty = json.dumps(json.loads(result), indent=2)
    print(f"result:  {len(result):,} chars (what the model reads)\n")
    print(pretty[:3000] + ("\n…" if len(pretty) > 3000 else ""))
    write_report(
        "tools",
        a.tag,
        f"# Tool call\n\n`{a.tool}({json.dumps(arguments)})` · error: {is_error} · "
        f"{len(result):,} chars\n\n```json\n{pretty}\n```\n",
    )


if __name__ == "__main__":
    main()
