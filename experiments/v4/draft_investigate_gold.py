"""Draft the Investigate gold set (evaluation/gold/investigate-gold.jsonl; spec §2.4, §5).

  ERA_COMPANIES=companies.yaml uv run python experiments/v4/draft_investigate_gold.py

Each question needs more than one Ask or Research run. Its gold is a list of parts, each the
work one sub-run would do:
  ask parts       expected_answer: values read here from the store, with their ids
  research parts  expected_refs: model: id patterns (values depend on the model, as in
                  research-gold)
  required_points facts a right answer must state, judged by hand or the judge
  expected_gaps   what a right answer must name as missing or not comparable
The same questions are the single-Research baseline (design doc §12.2).
"""

import json
import re
from pathlib import Path

from era_agent.agent.tools import Toolbox
from era_agent.asof import AsOf
from era_agent.store import Store

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "v4" / "build" / "parent_child.db"
OUT = ROOT / "evaluation" / "gold" / "investigate-gold.jsonl"
AS_OF = "2026-09-01"
tools = Toolbox(Store(DB, AsOf.of(AS_OF)), None, None, "ask")
questions = []


def rows(tool: str, **arguments) -> dict:
    result = tools.call(tool, arguments)
    if "error" in result:
        raise ValueError(f"{tool} {arguments}: {result['error']}")
    return {r["id"]: r for r in result["rows"]}


_kpis: dict = {}


def kpi(ticker: str, metric: str, period: str) -> dict:
    if ticker not in _kpis:
        _kpis[ticker] = rows("company_kpis", company=ticker, periods=["2025Q2", "2026Q2"])
    return _kpis[ticker][f"kpi:{ticker}:{metric}:{period}"]


def guide(ticker: str, gid: str) -> dict:
    """The latest guide with this id (an update replaces the guide it follows)."""
    result = tools.call("guidance", {"company": ticker})
    return [r for r in result["rows"] if r["id"] == gid][-1]


def value(row: dict, company: str) -> dict:
    return {"value": row["value"], "unit": row["unit"], "company": company, "id": row["id"]}


def derived(label: str, number: float, unit: str, company: str, inputs: list[str]) -> dict:
    return {"value": round(number, 2), "unit": unit, "company": company, "label": label,
            "from": inputs}


def growth(t: str, metric: str) -> dict:
    old, new = kpi(t, metric, "2025Q2"), kpi(t, metric, "2026Q2")
    return derived(f"{metric} growth Q2 2026 y/y", (new["value"] / old["value"] - 1) * 100,
                   "percent", t, [old["id"], new["id"]])


def ratio(t: str, top: str, bottom: str, period: str, label: str) -> dict:
    a, b = kpi(t, top, period), kpi(t, bottom, period)
    return derived(f"{label} {period}", a["value"] / b["value"] * 100, "percent", t,
                   [a["id"], b["id"]])


def mid(row: dict, company: str) -> dict:
    """The midpoint; the scorer also accepts the range itself (both ends)."""
    return derived(f"guidance midpoint {row['id']}", (row["low"] + row["high"]) / 2, row["unit"],
                   company, [row["id"]]) | {"low": row["low"], "high": row["high"]}


def ref(t: str, run: str, key: str) -> str:
    return re.escape(f"model:{t}:{AS_OF}:") + run + re.escape(f":{key}")


def ask(*values: dict) -> dict:
    return {"mode": "ask", "expected_answer": list(values)}


def research(*refs: str) -> dict:
    return {"mode": "research", "expected_refs": list(refs)}


def add(qid, category, companies, question, parts, points=(), gaps=(), notes=""):
    questions.append({
        "id": qid, "split": "dev" if len(questions) % 2 else "test", "category": category,
        "companies": companies, "question": question, "as_of": AS_OF, "parts": parts,
        "required_points": list(points), "expected_gaps": list(gaps), "tolerance": 0.005,
        "requires_assumptions": any(p["mode"] == "research" for p in parts), "notes": notes,
        "origin": "drafted", "verified_by": None, "gold_set": "investigate-gold",
    })


# --- peer comparison with reasons -------------------------------------------------------------
add("uber-lyft-margin-drivers", "peer_reasons", ["UBER", "LYFT"],
    "Which of Uber and Lyft improved its adjusted EBITDA margin on revenue more from Q2 2025 to "
    "Q2 2026, and what does each management say drove it?",
    [ask(*(ratio(t, "adj_ebitda", "revenue", p, "adj. EBITDA margin on revenue")
           for t in ("UBER", "LYFT") for p in ("2025Q2", "2026Q2")))],
    points=["Names the company with the larger margin change, in percentage points",
            "Gives each management's stated drivers, cited to release or 10-Q text"],
    gaps=["Lyft states its margin on gross bookings, not revenue: definitions differ"])

add("cart-dash-growth-valuation", "peer_reasons", ["CART", "DASH"],
    "Instacart and DoorDash both grew order value in Q2 2026. Whose grew faster year over year, "
    "and which is cheaper on our 2027E EV / adjusted EBITDA?",
    [ask(growth("CART", "gtv"), growth("DASH", "marketplace_gov")),
     research(ref("CART", "val-base", "ev_to_ebitda"), ref("DASH", "val-base", "ev_to_ebitda"))],
    points=["Names the faster grower", "Names the cheaper company on 2027E EV / EBITDA"],
    gaps=["GTV and Marketplace GOV are defined differently"])

add("abnb-bkng-growth-rating", "peer_reasons", ["ABNB", "BKNG"],
    "Airbnb's nights grew faster than Booking's room nights in Q2 2026. Does our model expect "
    "that gap to hold in 2027 revenue growth, and how do our ratings compare?",
    [ask(growth("ABNB", "nights_and_experiences"), growth("BKNG", "room_nights")),
     research(ref("ABNB", "base", "revenue.FY2027"), ref("BKNG", "base", "revenue.FY2027"),
              ref("ABNB", "val-base", "rating"), ref("BKNG", "val-base", "rating"))],
    points=["Gives both 2027E revenue growth rates and says whether the gap holds",
            "Gives both ratings"],
    gaps=["Airbnb counts nights and experiences, Booking counts room nights"])

# --- reported vs modeled ----------------------------------------------------------------------
add("dash-beat-model-scenario", "reported_vs_model", ["DASH"],
    "Did DoorDash beat its Q2 2026 Marketplace GOV guidance? Does our model expect Q3 to land "
    "above the Q3 guide, and what would 1 point lower growth do to our price target?",
    [ask(value(kpi("DASH", "marketplace_gov", "2026Q2"), "DASH"),
         mid(guide("DASH", "guide:DASH:marketplace_gov:2026Q2"), "DASH"),
         mid(guide("DASH", "guide:DASH:marketplace_gov:2026Q3"), "DASH")),
     research(ref("DASH", "base", "gross_bookings.2026Q3"),
              ref("DASH", r"val-scratch-\d+", "price_target"))],
    points=["Says beat or miss against the Q2 midpoint, with the gap",
            "Compares our Q3 forecast with the Q3 guidance midpoint",
            "Gives the price target, base vs scenario"],
    notes="Our Q3 forecast is anchored to the guidance midpoint (§7.2); expect it near 33,500")

add("lyft-miss-and-q3", "reported_vs_model", ["LYFT"],
    "Why did our Q2 2026 revenue estimate for Lyft miss, and is our Q3 2026 gross bookings "
    "forecast inside Lyft's Q3 guidance range?",
    [research(r"model:LYFT:2026-0[45]-\d\d:base:revenue\.2026Q2",  # the estimate, as research-gold
              ref("LYFT", "base", "gross_bookings.2026Q3")),
     ask(guide("LYFT", "guide:LYFT:gross_bookings:2026Q3") | {"company": "LYFT"})],
    points=["Splits the Q2 miss into volume or gross value and take rate (the bridge)",
            "Says whether the Q3 forecast is inside 5,500 to 5,670"],
    notes="The ask part is the guide row itself (low, high)")

add("bkng-expe-q3-guide-vs-model", "reported_vs_model", ["BKNG", "EXPE"],
    "For Q3 2026, how do our gross bookings forecasts for Booking and Expedia compare with each "
    "company's guidance?",
    [ask(mid(guide("BKNG", "guide:BKNG:gross_bookings_growth:2026Q3"), "BKNG"),
         mid(guide("EXPE", "guide:EXPE:gross_bookings:2026Q3"), "EXPE")),
     research(ref("BKNG", "base", "gross_bookings.2026Q3"),
              ref("EXPE", "base", "gross_bookings.2026Q3"))],
    points=["States Booking's guide as growth and Expedia's as a level, and compares like "
            "with like",
            "Gives each forecast vs its guide"],
    gaps=["Booking guides growth, Expedia a dollar range: one must be converted"])

add("uber-segments-scenario", "reported_vs_model", ["UBER"],
    "Uber's Delivery gross bookings grew faster than Mobility in Q2 2026. By how much, and what "
    "would a 50 bp lower Mobility take rate do to our 2027E adjusted EBITDA and price target?",
    [ask(growth("UBER", "gross_bookings.delivery"), growth("UBER", "gross_bookings.mobility")),
     research(ref("UBER", r"scratch-\d+", "adj_ebitda.FY2027"),
              ref("UBER", r"val-scratch-\d+", "price_target"))],
    points=["Gives both growth rates and the gap in points",
            "Gives 2027E adjusted EBITDA and the price target, base vs scenario"])

# --- sector view ------------------------------------------------------------------------------
add("travel-take-rate-premium", "sector", ["BKNG", "EXPE", "ABNB", "TRIP"],
    "Across the four travel companies, whose take rate moved most from Q2 2025 to Q2 2026, and "
    "does that company trade at a premium to the others on 2027E EV / adjusted EBITDA?",
    [ask(*(ratio(t, "revenue", gb, p, "take rate")
           for t, gb in (("BKNG", "gross_bookings"), ("EXPE", "gross_bookings"),
                         ("ABNB", "gross_booking_value"))
           for p in ("2025Q2", "2026Q2"))),
     research(*(ref(t, "val-base", "ev_to_ebitda") for t in ("BKNG", "EXPE", "ABNB", "TRIP")))],
    points=["Names the company with the largest take-rate change, in bps",
            "Says premium or discount vs the other three's multiples"],
    gaps=["Tripadvisor reports no total gross bookings, so it has no take rate"])

add("mobility-cheapest-growth", "sector", ["UBER", "LYFT", "DASH", "CART"],
    "Which mobility and delivery company is cheapest on 2027E EV / adjusted EBITDA, and how fast "
    "did its revenue grow in Q2 2026?",
    [research(*(ref(t, "val-base", "ev_to_ebitda") for t in ("UBER", "LYFT", "DASH", "CART")))
     | {"any": True},
     ask(*(growth(t, "revenue") for t in ("UBER", "LYFT", "DASH", "CART")))
     | {"pick": "cheapest"}],
    points=["Names the cheapest company", "Gives that company's Q2 2026 y/y revenue growth"],
    notes="The second part depends on the first: score only the cheapest company's growth "
          "(all four are listed here so the scorer can pick it)")

add("uber-lyft-nyc-vs-reported", "sector", ["UBER", "LYFT"],
    "Do NYC TLC trips for Uber and Lyft in May 2026 tell the same growth story as the trips and "
    "rides each company reported for Q2 2026?",
    [ask(*(derived(f"NYC trips growth May 2026 y/y {lic}",
                   (now["value"] / then["value"] - 1) * 100, "percent", t, [then["id"], now["id"]])
           for t, lic in (("UBER", "HV0003"), ("LYFT", "HV0005"))
           for c in [rows("crosscheck", series="tlc", start="2025-05", end="2026-05")]
           for then, now in [(c[f"cc:tlc:{lic}:2025-05-01"], c[f"cc:tlc:{lic}:2026-05-01"])]),
         growth("UBER", "trips"), growth("LYFT", "rides"))],
    points=["Says NYC growth is far below reported growth for Uber and close for Lyft"],
    gaps=["TLC covers NYC only and one month; company figures are global and quarterly"])

# --- what changed -----------------------------------------------------------------------------
add("expe-what-changed", "what_changed", ["EXPE"],
    "What changed for Expedia in its Q2 2026 release: guidance, the main KPIs, and any new risk "
    "factors since the 10-K?",
    [ask(growth("EXPE", "gross_bookings"), growth("EXPE", "room_nights"),
         mid(guide("EXPE", "guide:EXPE:revenue:FY2026"), "EXPE"))],
    points=["Says full-year 2026 revenue guidance was raised (from 15,600-16,000 to 16,050-16,220)",
            "Gives gross bookings and room night growth",
            "Names new or changed risk factors from the Q2 2026 10-Q, or says there are none"],
    notes="Guidance midpoint is the latest FY2026 revenue guide (the update)")

add("trip-segments-rating", "what_changed", ["TRIP"],
    "How did Tripadvisor's Viator business grow in Q2 2026, and what is our rating and price "
    "target?",
    [ask(value(kpi("TRIP", "gross_bookings.viator", "2026Q2"), "TRIP"),
         value(kpi("TRIP", "gross_bookings.viator", "2025Q2"), "TRIP")),
     research(ref("TRIP", "val-base", "price_target"), ref("TRIP", "val-base", "rating"))],
    points=["Gives Viator gross bookings growth", "Gives our rating and price target"],
    gaps=["Segments changed in 2026 (Experiences, Hotels & Other): segment revenue and EBITDA "
          "are not comparable with 2025"])

OUT.write_text("".join(json.dumps(q) + "\n" for q in questions))
print(f"{len(questions)} questions -> {OUT.relative_to(ROOT)}")
