"""V4.7: the spec's third backtest baseline, a plain LLM with no tools (spec §6).

  taskset -c 16-31 uv run python experiments/v4/llm_baseline.py --from all-defaults

For each forecast in a backtest run's forecasts.csv, the model is told the date and asked for
the next quarter's value with no data and no tools. Its MAPE is compared with the model's and
seasonal naive's on the same forecasts. It is also a leakage probe: a no-tools model that
forecasts well suggests the actuals are in its training data (the LLM's cutoff is not
published, so no quarter can be called held out).
"""

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tenk_agent.models import get_model, parse_json

ROOT = Path(__file__).resolve().parents[2]
NAMES = {"ABNB": "Airbnb", "BKNG": "Booking Holdings", "CART": "Instacart (Maplebear)",
         "DASH": "DoorDash", "EXPE": "Expedia Group", "LYFT": "Lyft", "TRIP": "Tripadvisor",
         "UBER": "Uber"}
WORDS = {"revenue": ("revenue", "USD millions"), "gross_bookings": ("gross bookings (for "
         "DoorDash: Marketplace GOV; Instacart: GTV)", "USD millions"),
         "volume": ("room nights (Airbnb: nights and experiences booked)", "millions")}


def ask(llm, row: dict) -> float | None:
    what, unit = WORDS[row["metric"]]
    quarter = f"Q{row['target'][-1]} {row['target'][:4]}"
    prompt = (f"Today is {row['as_of']}. Without looking anything up, give your best estimate of "
              f"{NAMES[row['ticker']]}'s {what} for {quarter}, in {unit}. Reply with only a JSON "
              'object: {"value": number}')
    reply = llm.complete([{"role": "user", "content": prompt}], max_tokens=200)
    try:
        return float(parse_json(reply.text)["value"])
    except (ValueError, KeyError, TypeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", default="all-defaults")
    ap.add_argument("--model", default="openai:Qwen/Qwen3.6-27B-FP8")
    ap.add_argument("--base-url", default="http://localhost:8001/v1")
    args = ap.parse_args()
    folder = ROOT / "evaluation" / "results" / f"backtest-{args.source}"
    rows = list(csv.DictReader((folder / "forecasts.csv").open()))
    llm = get_model(args.model, base_url=args.base_url, thinking=False)
    with ThreadPoolExecutor(4) as pool:
        values = list(pool.map(lambda r: ask(llm, r), rows))
    out = [r | {"llm_no_tools": v} for r, v in zip(rows, values, strict=True)]
    scored = [r for r in out if r["llm_no_tools"] not in (None, "") and r["forecast"]
              and r["seasonal_naive"]]

    def mape(key):
        return sum(abs(float(r[key]) / float(r["actual"]) - 1) for r in scored) / len(scored)

    result = {"forecasts": len(rows), "answered": len(scored),
              "llm_no_tools_mape": mape("llm_no_tools"), "model_mape": mape("forecast"),
              "naive_mape": mape("seasonal_naive"),
              "llm_within_2pct": sum(abs(float(r["llm_no_tools"]) / float(r["actual"]) - 1)
                                     <= 0.02 for r in scored) / len(scored)}
    (folder / "llm_baseline.json").write_text(json.dumps({"summary": result, "rows": out},
                                                          indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
