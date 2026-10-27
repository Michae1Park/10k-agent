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
    neg = "(" in cell
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
    ticker, period, metric, unit, pattern, group=1, scale=1.0, segment=None, target=None,
    tables=False,
):
    """A value from the first sentence matching `pattern` (group = the number)."""
    doc = json.loads((BUILD / f"{ticker}-{period}-ER" / "document.json").read_text())
    for s in doc["sections"]:
        for b in s["blocks"]:
            if (tables or b.get("kind") != "table") and (m := re.search(pattern, b["text"])):
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


def row_label(ticker, period, metric, unit, row_label, column, nth=0, segment=None, scale=1.0):
    """A value from the `column`-th numeric cell of the nth table row (in any table of the
    release) whose label starts with `row_label`; the column order is per company."""
    doc = json.loads((BUILD / f"{ticker}-{period}-ER" / "document.json").read_text())
    rows = [
        (b, r)
        for s in doc["sections"]
        for b in s["blocks"]
        if b.get("kind") == "table"
        for r in b.get("rows") or []
        if r and r[0].lower().startswith(row_label.lower())
        and any(re.search(r"\d", c) and "%" not in c for c in r[1:])
    ]
    block, row = rows[nth]
    cells = [c for c in row[1:] if re.search(r"\d", c) and "%" not in c]
    quote = "| " + " | ".join(row) + " |"
    assert quote in block["text"], (ticker, period, metric)
    return {"ticker": ticker, "period": period, "metric": metric, "segment": segment,
            "value": round(number(cells[column]) * scale, 6), "unit": unit,
            "source": f"{ticker}-{period}-ER", "block": block["id"], "quote": quote}


def guide(ticker, period, metric, unit, pattern, scale=1.0, target=None, groups=(1, 2)):
    """Low and high labels from the first sentence or table row matching `pattern`."""
    target = target or NEXT[period]
    return [
        text_label(ticker, period, f"guide.{metric}.{end}", unit, pattern, g, scale, target=target,
                   tables=True)
        for g, end in zip(groups, ("low", "high"), strict=True)
    ]


PERIODS = ("2025Q3", "2025Q4", "2026Q1", "2026Q2")
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


def expe() -> list[dict]:
    """Highlights table: current quarter first; B2C/B2B in the quarterly series (current last)."""
    out = []
    for period in PERIODS:
        def r(*a, period=period, **k):
            return row_label("EXPE", period, *a, **k)

        out += [
            r("room_nights", "count millions", "Booked room night", 0),
            r("gross_bookings", "USD millions", "Gross bookings", 0, segment="total"),
            r("gross_bookings", "USD millions", "B2C", -1, segment="b2c"),
            r("gross_bookings", "USD millions", "B2B", -1, segment="b2b"),
            r("revenue", "USD millions", "Revenue", 0, segment="total"),
            r("revenue", "USD millions", "B2C", -1, 1, segment="b2c"),
            r("revenue", "USD millions", "B2B", -1, 1, segment="b2b"),
            r("adj_ebitda", "USD millions", "Adjusted EBITDA*", 0),
            r("free_cash_flow", "USD millions", "Free cash flow*", 0),
        ]
        if period != "2026Q1":  # that release's highlights leave EPS out
            out.append(r("eps_diluted", "USD per share", "Diluted earnings per share", 0))
    pct = r"(\d+)-(\d+)%"
    out += guide("EXPE", "2025Q3", "gross_bookings_growth", "percent",
                 r"Gross bookings \| 3-5% \| 7% \| " + pct)
    out += guide("EXPE", "2025Q3", "revenue_growth", "percent",
                 r"Revenue \| 3-5% \| 6-7% \| " + pct)
    bn = r"\$([\d.]+) - \$([\d.]+)B"
    for period in ("2025Q4", "2026Q1"):  # 2026Q1 repeats the full-year guidance before Q2's
        out += guide("EXPE", period, "gross_bookings", "USD millions",
                     r"Gross bookings \| (?:\$127 - \$129B \+6 - 8% \| ){0,2}" + bn, 1000)
        out += guide("EXPE", period, "revenue", "USD millions",
                     r"Revenue \| (?:\$15.6 - \$16.0B \+6 - 9% \| ){0,2}" + bn, 1000)
    out += guide("EXPE", "2026Q2", "gross_bookings", "USD millions",
                 r"Gross Bookings \| " + bn, 1000)
    out += guide("EXPE", "2026Q2", "revenue", "USD millions", r"Revenue \| " + bn, 1000)
    out += guide("EXPE", "2026Q2", "adj_ebitda", "USD millions",
                 r"Adjusted EBITDA\*\* \| " + bn, 1000)
    return out


def abnb() -> list[dict]:
    """Image-based releases (text layer only): prior year first, then the current quarter."""
    out = []
    for period in PERIODS:
        def x(*a, period=period, **k):
            return text_label("ABNB", period, *a, **k)

        out += [
            x("nights_and_experiences", "count millions",
              r"Nights and Seats Booked [\d.]+M ([\d.]+)M"),
            x("gross_booking_value", "USD millions",
              r"Nights and Seats Booked [\d.]+M [\d.]+M (?:[\d.]+M [\d.]+M )?"
              r"Gross Booking Value \$[\d.]+B \$([\d.]+)B", scale=1000),
            x("revenue", "USD millions", r"2025 2026(?: 2025 2026)? Revenue \$[\d,]+ \$([\d,]+)")
            if period > "2025Q4" else
            x("revenue", "USD millions", r"2024 2025(?: 2024 2025)? Revenue \$[\d,]+ \$([\d,]+)"),
        ]
    rev = r"generate revenue of \$([\d.]+) billion to \$([\d.]+) billion"
    for period in PERIODS:
        out += guide("ABNB", period, "revenue", "USD millions", rev, 1000)
        out += guide("ABNB", period, "revenue_growth", "percent",
                     rev + r", representing year-over-year growth of (\d+)% to (\d+)%",
                     groups=(3, 4))
    floor = text_label("ABNB", "2026Q2", "guide.adj_ebitda_margin.low", "percent",
                       r"Adjusted EBITDA Margin of at least ([\d.]+)%", target="FY2026")
    out.append(floor)
    return out


def trip() -> list[dict]:
    """Current quarter first. Segments: Brand Tripadvisor / Viator / TheFork until Q3 2025,
    Experiences / Hotels and Other (+ TheFork) from Q4 2025, TheFork discontinued from Q2 2026."""
    out = []
    segs = {"2025Q3": (("tripadvisor", "Brand Tripadvisor"), ("viator", "Viator"),
                       ("thefork", "TheFork")),
            "2025Q4": (("experiences", "Experiences"), ("hotels_other", "Hotels and Other"),
                       ("thefork", "TheFork")),
            "2026Q1": (("experiences", "Experiences"), ("hotels_other", "Hotels and Other"),
                       ("thefork", "TheFork")),
            "2026Q2": (("experiences", "Experiences"), ("hotels_other", "Hotels and Other"))}
    for period in PERIODS:
        def r(*a, period=period, **k):
            return row_label("TRIP", period, *a, **k)

        out += [
            r("revenue", "USD millions", "Total Revenue", 0, segment="total"),
            r("adj_ebitda", "USD millions", "Total Adjusted EBITDA", 0, segment="total"),
            r("free_cash_flow", "USD millions", "Free cash flow", 0),
            r("eps_diluted", "USD per share", "Diluted", 0),
        ]
        out += [r("revenue", "USD millions", label, 0, segment=key) for key, label in segs[period]]
    return out


def lyft() -> list[dict]:
    """Highlights: current, prior quarter, prior-year quarter; the income statement current
    first (the Q4 release has only full-year columns, so no Q4 EPS label)."""
    out = []
    for period in PERIODS:
        def r(*a, period=period, **k):
            return row_label("LYFT", period, *a, **k)

        out += [
            r("active_riders", "count millions", "Active Riders", 0),
            r("rides", "count millions", "Rides", 0),
            r("gross_bookings", "USD millions", "Gross Bookings", 0),
            r("revenue", "USD millions", "Revenue", 0),
            r("adj_ebitda", "USD millions", "Adjusted EBITDA", 0),
            r("free_cash_flow", "USD millions", "Free cash flow", 0),
        ]
        if period != "2025Q4":
            out.append(r("eps_diluted", "USD per share", "Diluted", 0))
        gb = r"Gross Bookings of approximately \$([\d.]+) billion to \$([\d.]+) billion"
        out += guide("LYFT", period, "gross_bookings", "USD millions", gb, 1000)
        out += guide("LYFT", period, "gross_bookings_growth", "percent",
                     gb + r", up approximately (\d+)% to (\d+)%", groups=(3, 4))
        out += guide("LYFT", period, "adj_ebitda", "USD millions",
                     r"Adjusted EBITDA of approximately \$(\d+) million to \$(\d+) million")
    return out


def dash() -> list[dict]:
    """Five-quarter highlights, current last; income statement prior year first."""
    out = []
    for period in PERIODS:
        def r(*a, period=period, **k):
            return row_label("DASH", period, *a, **k)

        out += [
            r("total_orders", "count millions", "Total Orders", 4),
            r("marketplace_gov", "USD millions", "Marketplace GOV", 4),
            r("revenue", "USD millions", "Revenue", 4),
            r("adj_ebitda", "USD millions", "Adjusted EBITDA", 4),
            r("eps_diluted", "USD per share", "Diluted", 1),
        ]
        if period != "2025Q3":  # that release shows trailing-twelve-month FCF only
            out.append(r("free_cash_flow", "USD millions", "Free Cash Flow", 4))
        out += guide("DASH", period, "marketplace_gov", "USD millions",
                     r"\| Q\d 20\d\d \| \$([\d.]+) billion - \$([\d.]+) billion", 1000)
        out += guide("DASH", period, "adj_ebitda", "USD millions",
                     r"billion \| \$([\d,]+) million - \$([\d,]+) million")
    return out


def cart() -> list[dict]:
    """Prose letters through Q4 2025 (images), tables from Q1 2026 (prior year first)."""
    out = []
    for period in ("2025Q3", "2025Q4"):
        hl = rf"Q{period[-1]}'25 Financial Highlights(?:(?!Q\d'25 Financial).)*?"

        def x(*a, period=period, **k):
            return text_label("CART", period, *a, **k)

        out += [
            x("gtv", "USD millions", hl + r"GTV of \$([\d,]+) million"),
            x("orders", "count millions", hl + r"Orders of ([\d.]+) million"),
            x("revenue", "USD millions",
              hl + r"Total revenue of \$([\d,]+) million"),
        ]
    out += [
        text_label("CART", "2025Q3", "adj_ebitda", "USD millions",
                   r"Adjusted EBITDA of \$(278) million"),
        text_label("CART", "2025Q4", "adj_ebitda", "USD millions",
                   r"Adjusted EBITDA of \$(303) million"),
    ]
    for period in ("2026Q1", "2026Q2"):
        def r(*a, period=period, **k):
            return row_label("CART", period, *a, **k)

        out += [
            r("gtv", "USD millions", "GTV", 1),
            r("orders", "count millions", "Orders", 1),
            r("revenue", "USD millions", "Total revenue", 1),
            r("adj_ebitda", "USD millions", "Adjusted EBITDA (1)", 1),
            r("free_cash_flow", "USD millions", "Free cash flow (1)", 1),
            r("net_income", "USD millions", "Net income", 1),
            r("eps_diluted", "USD per share", "Diluted", 1),
        ]
    for period in PERIODS:
        out += guide("CART", period, "gtv", "USD millions",
                     r"GTV \|? ?\$([\d,]+) - \$([\d,]+) million")
        out += guide("CART", period, "adj_ebitda", "USD millions",
                     r"Adjusted EBITDA \|? ?\$([\d,]+) - \$([\d,]+) million")
    return out


if __name__ == "__main__":
    verified = {}
    if OUT.exists():  # keep reviewers' marks on labels that didn't change
        for line in OUT.read_text().splitlines():
            old = json.loads(line)
            verified[old["id"]] = old.get("verified_by")
    rows = uber() + bkng() + expe() + abnb() + trip() + lyft() + dash() + cart()
    for r in rows:
        segment = f".{r['segment']}" if r["segment"] else ""
        r["id"] = f"{r['ticker']}-{r['period']}-{r['metric']}{segment}"
        r["verified_by"] = verified.get(r["id"])
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} labels → {OUT}")
    for r in rows[96:]:
        print(f"  {r['id']:45s} {r['value']:>12} {r['unit']:15s} {r['quote'][:70]}")
