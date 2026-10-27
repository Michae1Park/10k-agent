"""Layer 1, extraction (V4 design doc §13.1): extracted KPIs and guidance against hand labels.

A gold label (evaluation/gold/extraction-gold.jsonl) matches when the extraction has a row for
its (ticker, period, metric, segment) — guidance labels `guide.<metric>.low|high` match the
guidance row for that metric and target period — with the same value to the label's precision
(USD millions to 0.5; per share to half a cent; percent and counts exactly as printed). Pass
bars: 98% overall, 100% on revenue, EPS and the company's main KPI.
"""

import json
from pathlib import Path

MAIN_KPI = {
    "UBER": "gross_bookings",
    "BKNG": "room_nights",
    "EXPE": "gross_bookings",
    "ABNB": "nights_and_experiences",
    "TRIP": "revenue",
    "LYFT": "gross_bookings",
    "DASH": "marketplace_gov",
    "CART": "gtv",
}
CRITICAL = ("revenue", "eps_diluted")


def _quarter(target: str | None) -> str | None:
    """'Q3 2026' or '2026Q3' → '2026Q3'."""
    if not target:
        return None
    t = target.replace(" ", "")
    return f"{t[2:]}{t[:2]}" if t.upper().startswith("Q") else t


def _close(a: float, b: float, unit: str) -> bool:
    tolerance = {"USD millions": 0.5, "USD per share": 0.005}.get(unit, 1e-9)
    return abs(a - b) <= tolerance


def score(gold: list[dict], extracted: dict[tuple[str, str], dict]) -> dict:
    """`extracted`: (ticker, period) → Extraction as JSON (facts/extract.to_json)."""
    rows = []
    for label in gold:
        got = extracted.get((label["ticker"], label["period"]))
        value = None
        if got is not None and label["metric"].startswith("guide."):
            _, metric, side = label["metric"].split(".")
            target = label.get("target_period")
            for g in got["guidance"]:
                if g["metric"] == metric and _quarter(g.get("target_period")) == target:
                    value = g.get(side)
        elif got is not None:
            segment = label.get("segment") or "total"
            for k in got["kpis"]:
                if k["metric"] == label["metric"] and (k.get("segment") or "total") == segment:
                    value = k["value"]
        ok = value is not None and _close(float(value), label["value"], label["unit"])
        critical = label["metric"] in CRITICAL or (
            label["metric"] == MAIN_KPI.get(label["ticker"])
            and label.get("segment") in (None, "total")
        )
        rows.append(
            {
                "id": label["id"],
                "expected": label["value"],
                "got": value,
                "ok": ok,
                "critical": critical,
            }
        )
    n, crit = len(rows), [r for r in rows if r["critical"]]
    return {
        "labels": n,
        "exact": sum(r["ok"] for r in rows) / n if n else 0.0,
        "critical": sum(r["ok"] for r in crit) / len(crit) if crit else 0.0,
        "pass": (sum(r["ok"] for r in rows) / n >= 0.98 if n else False)
        and all(r["ok"] for r in crit),
        "misses": [r for r in rows if not r["ok"]],
    }


def load_gold(path: Path, tickers: list[str] | None = None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if not tickers or r["ticker"] in tickers]
