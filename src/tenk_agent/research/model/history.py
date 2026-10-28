"""S3 model, history only (V4 design doc §9, V4.3): data pack → model → layer-2 checks.

  taskset -c 16-31 uv run python -m tenk_agent.research.model.history UBER 2026-09-01

Writes <out>/<TICKER>/<as_of>/datapack.json, model.json, checks.json and workbook.xlsx (the
Model tab), then has LibreOffice recalculate the workbook: every formula cell must equal
Python's value (the parity check). Exit 1 on any failed check or parity difference. Forecast
columns (V4.4) and the workbook's other tabs (V4.6) build on this.
"""

import argparse
import json
from pathlib import Path

from tenk_agent.asof import AsOf
from tenk_agent.research import datapack
from tenk_agent.research.export import workbook
from tenk_agent.research.model import checks as layer2
from tenk_agent.research.model.graph import Model, build, load_template
from tenk_agent.store import Store

TEMPLATE_OF = {  # design doc §5.3
    "UBER": "segments",
    "BKNG": "marketplace", "EXPE": "marketplace", "ABNB": "marketplace",
    "DASH": "orders", "LYFT": "orders", "CART": "orders",
    "TRIP": "trip",
}
FIRST_QUARTER = "2023Q1"  # history from the first quarter every pilot series covers


def quarters_before(as_of: str, first: str = FIRST_QUARTER) -> list[str]:
    """Quarters from `first` through the last one ended before the as-of."""
    year, q = int(first[:4]), int(first[5])
    out = []
    while True:
        end_month = 3 * q
        if f"{year}-{end_month:02d}-28" >= as_of:
            return out
        out.append(f"{year}Q{q}")
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)


def history(store: Store, ticker: str, quarters: list[str] | None = None):
    """(model, data pack, checks, exceptions) for one company at the store's as-of."""
    as_of = store.asof.date.isoformat()
    template = TEMPLATE_OF[ticker]
    lines, _ = load_template(template, ticker)
    pack = datapack.build(store, ticker, {s for x in lines for s in (x.source, x.fallback) if s})
    quarters = quarters or _reported(pack, quarters_before(as_of))
    model = build(ticker, template, as_of, quarters, pack)
    results = layer2.run(model, pack, store)
    return model, pack, results, layer2.exceptions(model, results)


def _reported(pack, quarters: list[str]) -> list[str]:
    """Drop quarters no filing reports: trailing ones (ended, not yet released) and leading
    ones (before the company was public: CART from 2023Q3)."""
    reported = {p for series in pack.values() for p in series}
    while quarters and quarters[-1] not in reported:
        quarters = quarters[:-1]
    while quarters and quarters[0] not in reported:
        quarters = quarters[1:]
    return quarters


def write(out: Path, model: Model, pack, results, exceptions) -> Path:
    folder = out / model.ticker / model.as_of
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "datapack.json").write_text(json.dumps(datapack.to_json(pack), indent=1))
    (folder / "model.json").write_text(json.dumps(model.to_json(), indent=1))
    (folder / "checks.json").write_text(json.dumps(
        {"checks": layer2.to_json(results), "exceptions": exceptions}, indent=1))
    return folder


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("as_of")
    ap.add_argument("--db", type=Path, default=Path("data/v4/build/parent_child.db"))
    ap.add_argument("--out", type=Path, default=Path("output/research"))
    args = ap.parse_args()
    store = Store(args.db, AsOf.of(args.as_of))
    failed = False
    for ticker in args.tickers:
        model, pack, results, exceptions = history(store, ticker)
        folder = write(args.out, model, pack, results, exceptions)
        checked, differ = workbook.parity(model, workbook.write(model, folder / "workbook.xlsx"))
        print(f"{ticker} {model.template} {model.quarters[0]}–{model.quarters[-1]} → {folder}")
        for c in results:
            print(f"  {c.name:<16} {c.status:<4} tested {c.tested:>4}"
                  f"{f'  skipped {len(c.skipped)}' if c.skipped else ''}"
                  f"{f'  {c.note}' if c.note else ''}")
            for f in c.failures[:10]:
                print(f"      FAIL {f}")
        print(f"  {'parity':<16} {'fail' if differ else 'pass':<4} tested {checked:>4}")
        for d in differ[:10]:
            print(f"      DIFF {d}")
        failed |= any(c.status == "fail" for c in results) or bool(differ)
        print(f"  exceptions {len(exceptions)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
