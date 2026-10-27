"""Draft the V4.1 extraction gold set (design doc §13.1) from built release documents.

Each label names the table row (block id + row label) or the sentence that shows a value, and
takes the value and its verbatim quote from there, so a reviewer checks one quote per label.
Labels are drafts until a human verifies them (`verified_by`, as the V0–V3 gold sets).

  taskset -c 16-31 uv run python experiments/v4/draft_extraction_gold.py
"""

import json
import re
from pathlib import Path

BUILD = Path("data/v4/build")
OUT = Path("evaluation/gold/extraction-gold.jsonl")


def number(cell: str) -> float:
    neg = cell.strip().startswith("(")
    v = float(re.sub(r"[^\d.]", "", cell))
    return -v if neg else v


def table_label(ticker, period, metric, unit, block_id, row_label, nth=0, column=1, segment=None):
    """A value from the `column`-th numeric cell (0-based) of the nth row labeled `row_label`."""
    doc = json.loads((BUILD / f"{ticker}-{period}-ER" / "document.json").read_text())
    block = next(b for s in doc["sections"] for b in s["blocks"] if b["id"] == block_id)
    rows = [r for r in block["rows"] if r and r[0].startswith(row_label)]
    row = rows[nth]
    cells = [c for c in row[1:] if re.search(r"\d", c)]
    quote = "| " + " | ".join(row) + " |"
    assert quote in block["text"], (ticker, period, metric)
    return {"ticker": ticker, "period": period, "metric": metric, "segment": segment,
            "value": number(cells[column]), "unit": unit, "source": f"{ticker}-{period}-ER",
            "block": block_id, "quote": quote}


def text_label(
    ticker, period, metric, unit, pattern, group=1, scale=1.0, segment=None, target=None
):
    """A value from the first sentence matching `pattern` (group = the number)."""
    doc = json.loads((BUILD / f"{ticker}-{period}-ER" / "document.json").read_text())
    for s in doc["sections"]:
        for b in s["blocks"]:
            if b.get("kind") != "table" and (m := re.search(pattern, b["text"])):
                return {"ticker": ticker, "period": period, "metric": metric, "segment": segment,
                        "value": round(number(m.group(group)) * scale, 6), "unit": unit,
                        "target_period": target, "source": f"{ticker}-{period}-ER",
                        "block": b["id"], "quote": m.group(0)}
    raise LookupError((ticker, period, metric, pattern))


def series_label(ticker, period, metric, unit, pattern, index, scale=1.0, segment=None):
    """A value from a run of numbers after `pattern` (a quarterly series, oldest first), at
    `index` (Q4 releases append full-year columns, so the quarter is not always last)."""
    doc = json.loads((BUILD / f"{ticker}-{period}-ER" / "document.json").read_text())
    for s in doc["sections"]:
        for b in s["blocks"]:
            if m := re.search(pattern + r"((?: \$? ?[\d,]+(?:\.\d+)?)+)", b["text"]):
                values = re.findall(r"[\d,]+(?:\.\d+)?", m.group(1))
                return {"ticker": ticker, "period": period, "metric": metric, "segment": segment,
                        "value": round(number(values[index]) * scale, 6), "unit": unit,
                        "source": f"{ticker}-{period}-ER", "block": b["id"],
                        "quote": m.group(0).strip()}
    raise LookupError((ticker, period, metric, pattern))


UBER_BLOCKS = {  # period: (highlights table, segment Gross Bookings table, income statement)
    "2025Q3": ("x1-b0030", "x1-b0035", "x1-b0071"),
    "2025Q4": ("x1-b0034", "x1-b0044", "x1-b0085"),
    "2026Q1": ("x1-b0033", "x1-b0038", "x1-b0074"),
    "2026Q2": ("x1-b0033", "x1-b0038", "x1-b0074"),
}
NEXT = {"2025Q3": "2025Q4", "2025Q4": "2026Q1", "2026Q1": "2026Q2", "2026Q2": "2026Q3"}


def uber() -> list[dict]:
    out = []
    for period, (hl, seg, inc) in UBER_BLOCKS.items():
        def t(*a, period=period, **k):
            return table_label("UBER", period, *a, **k)

        out += [
            t("gross_bookings", "USD millions", hl, "Gross Bookings", segment="total"),
            t("gross_bookings", "USD millions", seg, "Mobility", segment="mobility"),
            t("gross_bookings", "USD millions", seg, "Delivery", segment="delivery"),
            t("gross_bookings", "USD millions", seg, "Freight", segment="freight"),
            t("trips", "count millions", hl, "Trips"),
            t("mapcs", "count millions", hl, "Monthly Active Platform Consumers"),
            t("revenue", "USD millions", hl, "Revenue"),
            t("adj_ebitda", "USD millions", hl, "Adjusted EBITDA"),
            t("free_cash_flow", "USD millions", hl, "Free cash flow"),
            t("eps_diluted", "USD per share", inc, "Diluted"),
        ]
        gb = r"Gross Bookings of \$([\d.]+) billion to \$([\d.]+) billion"
        out += [
            text_label("UBER", period, "guide.gross_bookings.low", "USD millions", gb, 1, 1000,
                       target=NEXT[period]),
            text_label("UBER", period, "guide.gross_bookings.high", "USD millions", gb, 2, 1000,
                       target=NEXT[period]),
        ]
    return out


BKNG_CURRENT = {"2025Q3": -1, "2025Q4": 7, "2026Q1": -1, "2026Q2": -1}  # in the units tables


def bkng() -> list[dict]:
    out = []
    for period, i in BKNG_CURRENT.items():
        def x(*a, period=period, **k):
            return text_label("BKNG", period, *a, **k)

        out += [
            series_label("BKNG", period, "room_nights", "count millions",
                         r"Units Sold Room Nights", i),
            series_label("BKNG", period, "gross_bookings", "USD millions",
                         r"Total Gross Bookings", i, 1000, segment="total"),
            x("revenue", "USD millions", r"Total revenues ([\d,]+) [\d,]+"),
            x("adj_ebitda", "USD millions", r"Adjusted EBITDA \$ ([\d,]+) \$ [\d,]+"),
            x("net_income", "USD millions", r"Net income \$ ([\d,]+) \$ [\d,]+"),
            x("eps_diluted", "USD per share",
              r"applicable to common stockholders per diluted common share \$ ([\d.]+)"),
            x("adj_eps", "USD per share", r"\(Adjusted EPS\) \$ ([\d.]+)"),
            x("free_cash_flow", "USD millions", r"Free cash flow \$ ([\d,]+)"),
        ]
        rn = r"Room Nights Growth (\d+)% - (\d+)%"
        rev = r"Revenue Growth \(1\) (\d+)% - (\d+)%"
        out += [
            x("guide.room_nights_growth.low", "percent", rn, 1, target=NEXT[period]),
            x("guide.room_nights_growth.high", "percent", rn, 2, target=NEXT[period]),
            x("guide.revenue_growth.low", "percent", rev, 1, target=NEXT[period]),
            x("guide.revenue_growth.high", "percent", rev, 2, target=NEXT[period]),
        ]
    return out


if __name__ == "__main__":
    rows = uber() + bkng()
    for r in rows:
        segment = f".{r['segment']}" if r["segment"] else ""
        r["id"] = f"{r['ticker']}-{r['period']}-{r['metric']}{segment}"
        r["verified_by"] = None
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} labels → {OUT}")
    for r in rows[48:]:
        print(f"  {r['id']:45s} {r['value']:>12} {r['unit']:15s} {r['quote'][:70]}")
