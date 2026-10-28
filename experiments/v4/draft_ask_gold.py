"""V4.2: draft the Ask gold set (evaluation/gold/ask-gold.jsonl) over the eight companies.

  taskset -c 16-31 uv run python experiments/v4/draft_ask_gold.py

Number questions take their values from the extraction gold (evaluation/gold/extraction-gold.jsonl,
cross-checked in design doc §17.9-17.10) or from the XBRL quarters, never typed by hand; a
calculated answer (growth, margin, beat or miss) is computed here from those labels. Text and
decline questions are written below with verbatim evidence; every evidence quote is checked to
appear in a chunk of the named filing visible at the question's as-of, or the script stops.

Format: the V3 gold fields, with `period`, `form` and `as_of` (design doc §13.1). Categories are
spec §2.1's; `kind` is figure, calculation, comparison, text or decline.
"""

import json
import re
from pathlib import Path

from tenk_agent.asof import AsOf
from tenk_agent.facts.quarters import derive, metrics
from tenk_agent.store import Store

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "evaluation" / "gold" / "extraction-gold.jsonl"
OUT = ROOT / "evaluation" / "gold" / "ask-gold.jsonl"
DB = ROOT / "data" / "v4" / "build" / "parent_child.db"
AS_OF = "2026-09-01"  # every Q2 2026 release and 10-Q is public
NAMES = {"ABNB": "Airbnb", "BKNG": "Booking Holdings", "CART": "Instacart", "DASH": "DoorDash",
         "EXPE": "Expedia", "LYFT": "Lyft", "TRIP": "Tripadvisor", "UBER": "Uber"}

labels = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
questions: list[dict] = []


def label(ticker: str, period: str, metric: str, segment: str | None = None) -> dict:
    found = [
        r for r in labels
        if r["ticker"] == ticker and r["period"] == period and r["metric"] == metric
        and (r.get("segment") or "total") == (segment or "total")
    ]
    assert len(found) == 1, (ticker, period, metric, segment, len(found))
    return found[0]


def guide(ticker: str, issued: str, metric: str) -> tuple[dict, dict | None]:
    low = label(ticker, issued, f"guide.{metric}.low")
    high = next((r for r in labels if r["id"] == f"{ticker}-{issued}-guide.{metric}.high"), None)
    return low, high


def source(r: dict) -> dict:
    return {"company": r["ticker"], "period": r["period"], "form": "ER", "evidence": r["quote"]}


def nice(period: str) -> str:
    return f"fiscal {period[2:]}" if period.startswith("FY") else f"{period[4:]} {period[:4]}"


def add(qid, question, category, kind, expected, sources, tools, *, as_of=AS_OF, period=None,
        form="ER", answer_type=None, tolerance=0.005, points=(), abstain=False, notes="",
        failure_mode=None):
    if answer_type is None:
        answer_type = ("abstain" if abstain else "text" if expected is None
                       else "number_set" if isinstance(expected, list) else "number")
    questions.append({
        "id": qid, "split": "dev" if len(questions) % 2 else "test", "category": category,
        "kind": kind, "failure_mode": failure_mode, "question": question, "as_of": as_of,
        "period": period, "form": form, "answer_type": answer_type, "expected_answer": expected,
        "tolerance": tolerance, "required_points": list(points), "should_abstain": abstain,
        "sources": sources, "expected_tools": tools, "notes": notes, "origin": "drafted",
        "verified_by": None, "gold_set": "ask-gold",
    })


def value(r: dict, **extra) -> dict:
    return {"value": r["value"], "unit": r["unit"], "company": r["ticker"]} | extra


# --- Reported financials (financials; GAAP lines also in the release) --------------------------

for t, p in (("LYFT", "2026Q2"), ("DASH", "2026Q2"), ("BKNG", "2026Q1"), ("CART", "2025Q4")):
    r, e = label(t, p, "revenue"), label(t, p, "adj_ebitda")
    add(f"{t.lower()}-revenue-adj-ebitda-{p.lower()}",
        f"What was {NAMES[t]}'s revenue and adjusted EBITDA in {nice(p)}?",
        "reported_financials", "figure", [value(r), value(e)], [source(r), source(e)],
        ["financials", "company_kpis"], period=p)

for t, p in (("UBER", "2026Q2"), ("DASH", "2026Q1"), ("EXPE", "2025Q3")):
    r = label(t, p, "eps_diluted")
    add(f"{t.lower()}-diluted-eps-{p.lower()}",
        f"What was {NAMES[t]}'s GAAP diluted EPS in {nice(p)}?",
        "reported_financials", "figure", value(r), [source(r)], ["financials"], period=p,
        tolerance=0.001)

for t, p in (("BKNG", "2026Q2"), ("CART", "2026Q2")):
    r = label(t, p, "net_income")
    add(f"{t.lower()}-net-income-{p.lower()}", f"What was {NAMES[t]}'s net income in {nice(p)}?",
        "reported_financials", "figure", value(r), [source(r)], ["financials"], period=p)

for t, p in (("TRIP", "2026Q2"), ("UBER", "2025Q3")):
    r = label(t, p, "free_cash_flow")
    add(f"{t.lower()}-fcf-{p.lower()}", f"How much free cash flow did {NAMES[t]} generate in "
        f"{nice(p)}?", "reported_financials", "figure", value(r), [source(r)], ["company_kpis"],
        period=p)

# Q4 from the 10-K: XBRL has no three-month Q4 fact, so financials derives FY - 9M.
store = Store(DB, AsOf.of(AS_OF))
for t in ("DASH", "LYFT", "EXPE"):
    quarters = derive(store, t, "revenue", metrics()["revenue"])
    q4 = next(q for q in quarters if q.id.endswith("2025Q4"))
    r = label(t, "2025Q4", "revenue")
    assert abs(q4.value / 1e6 - r["value"]) < 1, (t, q4.value, r["value"])  # 10-K ties the release
    add(f"{t.lower()}-revenue-2025q4", f"What was {NAMES[t]}'s revenue in the fourth quarter of "
        "2025?", "reported_financials", "figure", value(r), [source(r)], ["financials"],
        period="2025Q4", notes=f"financials derives it ({q4.derivation}); the release prints it")

# --- Operating KPIs ----------------------------------------------------------------------------

for t, p, m, seg, what in (
    ("UBER", "2026Q2", "gross_bookings", "mobility", "Mobility gross bookings"),
    ("UBER", "2026Q1", "trips", None, "trips"),
    ("UBER", "2025Q4", "mapcs", None, "monthly active platform consumers (MAPCs)"),
    ("BKNG", "2026Q2", "room_nights", None, "room nights"),
    ("EXPE", "2026Q2", "gross_bookings", "b2b", "B2B gross bookings"),
    ("ABNB", "2026Q2", "nights_and_experiences", None, "nights and experiences booked"),
    ("ABNB", "2025Q4", "gross_booking_value", None, "gross booking value"),
    ("LYFT", "2026Q2", "active_riders", None, "active riders"),
    ("LYFT", "2025Q3", "rides", None, "rides"),
    ("DASH", "2026Q2", "total_orders", None, "total orders"),
    ("CART", "2026Q2", "orders", None, "orders"),
    ("CART", "2026Q1", "gtv", None, "gross transaction value (GTV)"),
):
    r = label(t, p, m, seg)
    add(f"{t.lower()}-{m.replace('_', '-')}{'-' + seg if seg else ''}-{p.lower()}",
        f"How many {what} did {NAMES[t]} report in {nice(p)}?" if r["unit"].startswith("count")
        else f"What were {NAMES[t]}'s {what} in {nice(p)}?",
        "operating_kpis", "figure", value(r), [source(r)], ["company_kpis"], period=p)

r = [label("UBER", "2026Q2", "gross_bookings", s) for s in ("mobility", "delivery", "freight")]
add("uber-gross-bookings-segments-2026q2", "Break down Uber's Q2 2026 gross bookings by segment.",
    "operating_kpis", "figure", [value(x, segment=x["segment"]) for x in r], [source(x) for x in r],
    ["company_kpis"], period="2026Q2")

# Comparisons across companies: values only; definitions differ (the answer should say so).
for qid, question, picks in (
    ("gross-bookings-bkng-expe-2026q2",
     "Compare Booking Holdings' and Expedia's gross bookings in Q2 2026.",
     [("BKNG", "gross_bookings", None), ("EXPE", "gross_bookings", None)]),
    ("rides-trips-uber-lyft-2026q2", "How did Lyft's rides compare with Uber's trips in Q2 2026?",
     [("LYFT", "rides", None), ("UBER", "trips", None)]),
    ("orders-dash-cart-2026q2", "Which had more orders in Q2 2026, DoorDash or Instacart?",
     [("DASH", "total_orders", None), ("CART", "orders", None)]),
    ("room-nights-bkng-expe-2026q2",
     "How many room nights did Booking Holdings and Expedia each report for Q2 2026?",
     [("BKNG", "room_nights", None), ("EXPE", "room_nights", None)]),
):
    r = [label(t, "2026Q2", m, s) for t, m, s in picks]
    add(qid, question, "operating_kpis", "comparison", [value(x) for x in r],
        [source(x) for x in r], ["company_kpis"], period="2026Q2")

# --- Calculations on reported figures ----------------------------------------------------------


def pct(a, b):
    return round((b - a) / a * 100, 2)


for t, m, seg, what in (
    ("UBER", "gross_bookings", None, "gross bookings"),
    ("DASH", "marketplace_gov", None, "Marketplace GOV"),
    ("LYFT", "gross_bookings", None, "gross bookings"),
):
    a, b = label(t, "2026Q1", m, seg), label(t, "2026Q2", m, seg)
    add(f"{t.lower()}-{m.replace('_', '-')}-qoq-2026q2",
        f"By how much did {NAMES[t]}'s {what} grow from Q1 2026 to Q2 2026, in percent?",
        "operating_kpis", "calculation",
        {"value": pct(a["value"], b["value"]), "unit": "percent", "company": t},
        [source(a), source(b)], ["company_kpis", "calculator"], period="2026Q2", tolerance=0.01)

for t, p in (("UBER", "2026Q2"), ("DASH", "2026Q2"), ("BKNG", "2026Q2"), ("CART", "2026Q2")):
    e, r = label(t, p, "adj_ebitda"), label(t, p, "revenue")
    add(f"{t.lower()}-ebitda-margin-{p.lower()}",
        f"What was {NAMES[t]}'s adjusted EBITDA margin (adjusted EBITDA as a percent of revenue) "
        f"in {nice(p)}?", "reported_financials", "calculation",
        {"value": round(e["value"] / r["value"] * 100, 2), "unit": "percent", "company": t},
        [source(e), source(r)], ["company_kpis", "calculator"], period=p, tolerance=0.01)

for t, gb in (("UBER", "gross_bookings"), ("DASH", "marketplace_gov")):
    r, b = label(t, "2026Q2", "revenue"), label(t, "2026Q2", gb)
    add(f"{t.lower()}-revenue-take-rate-2026q2",
        f"What was {NAMES[t]}'s revenue as a percent of its "
        f"{'gross bookings' if gb == 'gross_bookings' else 'Marketplace GOV'} in Q2 2026?",
        "operating_kpis", "calculation",
        {"value": round(r["value"] / b["value"] * 100, 2), "unit": "percent", "company": t},
        [source(r), source(b)], ["company_kpis", "calculator"], period="2026Q2", tolerance=0.01)

# --- Guidance ----------------------------------------------------------------------------------

for t, metric, what in (
    ("DASH", "adj_ebitda", "adjusted EBITDA"),
    ("UBER", "gross_bookings", "gross bookings"),
    ("CART", "gtv", "GTV"),
    ("LYFT", "adj_ebitda", "adjusted EBITDA"),
    ("EXPE", "revenue", "revenue"),
    ("ABNB", "revenue", "revenue"),
    ("BKNG", "room_nights_growth", "room night growth"),
):
    low, high = guide(t, "2026Q2", metric)
    add(f"{t.lower()}-guide-{metric.replace('_', '-')}-2026q3",
        f"What did {NAMES[t]} guide for Q3 2026 {what}?", "guidance", "figure",
        [value(low, bound="low"), value(high, bound="high")], [source(low)], ["guidance"],
        period="2026Q3")

low, _ = guide("ABNB", "2026Q2", "adj_ebitda_margin")
add("abnb-guide-ebitda-margin-fy2026", "What adjusted EBITDA margin does Airbnb expect for 2026?",
    "guidance", "figure", value(low), [source(low)], ["guidance"], period="FY2026",
    notes="Open-ended: 'at least'; the answer should say it is a floor")

# Guide vs actual: did the quarter land inside, above or below the range?
for t, metric, issued, target in (
    ("UBER", "gross_bookings", "2026Q1", "2026Q2"),
    ("DASH", "adj_ebitda", "2026Q1", "2026Q2"),
    ("LYFT", "gross_bookings", "2025Q4", "2026Q1"),
    ("CART", "adj_ebitda", "2025Q3", "2025Q4"),
    ("EXPE", "revenue", "2026Q1", "2026Q2"),
    ("ABNB", "revenue", "2025Q4", "2026Q1"),
):
    low, high = guide(t, issued, metric)
    actual = label(t, target, metric)
    where = ("above" if actual["value"] > high["value"] else
             "below" if actual["value"] < low["value"] else "within")
    # The actual and the bound it crossed (both bounds when it lands within); the unbeaten bound
    # need not be claimed (gold change after run 3, design doc §17.11)
    bounds = {"above": [high], "below": [low], "within": [low, high]}[where]
    add(f"{t.lower()}-{metric.replace('_', '-')}-vs-guide-{target.lower()}",
        f"Did {NAMES[t]}'s {nice(target)} {metric.replace('_', ' ').replace('adj ', 'adjusted ')} "
        "beat its guidance?", "guidance", "comparison",
        [value(b, bound="high" if b is high else "low") for b in bounds] + [value(actual)],
        [source(low), source(actual)], ["guidance", "company_kpis"], period=target,
        points=[f"The actual was {where} the guided range"],
        answer_type="number_set", notes=f"actual {where} the range")

# --- Filing text, declines and false premises: written by hand ----------------------------------
HAND = ROOT / "experiments" / "v4" / "ask_gold_hand.json"
for q in json.loads(HAND.read_text()):
    questions.append(q | {"split": "dev" if len(questions) % 2 else "test"})


def check_evidence(q: dict) -> list[str]:
    """Each source's evidence in a chunk of its company, period and form, visible at the
    question's as-of; whitespace and case ignored."""
    s = Store(DB, AsOf.of(q["as_of"]))
    problems = []
    for src in q["sources"]:
        fy, fp = (int(src["period"][2:]), "FY") if src["period"].startswith("FY") else (
            int(src["period"][:4]), src["period"][4:])
        chunks = [c for c in s.chunks(src["company"], fy)
                  if c.fiscal_period == fp and c.form == src["form"]]
        want = norm(src["evidence"])
        if not any(want in norm(c.text) for c in chunks):
            problems.append(f"{q['id']}: evidence not in {src['company']} {src['period']} "
                            f"{src['form']}: {src['evidence'][:60]!r}")
    return problems


def norm(text: str) -> str:
    return re.sub(r"[\s​*]+", " ", text.replace("$", "").lower()).strip()


problems = [p for q in questions for p in check_evidence(q)]
ids = [q["id"] for q in questions]
problems += [f"duplicate id {i}" for i in {i for i in ids if ids.count(i) > 1}]
if problems:
    raise SystemExit("\n".join(problems))
OUT.write_text("".join(json.dumps(q) + "\n" for q in questions))
by = {}
for q in questions:
    by[q["category"]] = by.get(q["category"], 0) + 1
print(f"{len(questions)} questions → {OUT}", by)
