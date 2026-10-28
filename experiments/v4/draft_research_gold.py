"""V4.5: draft the Research gold set (evaluation/gold/research-gold.jsonl).

  uv run python experiments/v4/draft_research_gold.py

Research answers compute something new, so the gold checks how, not a number (design doc
§13.1): each question names the model: ids a right answer must cite (regular expressions over
model:<T>:<as_of>:<version>:<line>.<period>), and every answer must list the assumptions behind
its model numbers. Categories are spec §2.2's; debates come with Report mode (V4.6).
Per company: 3 forecast, 2 scenario, 2 valuation and 1 variance review; 8 peer comparisons.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evaluation" / "gold" / "research-gold.jsonl"
AS_OF = "2026-09-01"
NAMES = {"ABNB": "Airbnb", "BKNG": "Booking Holdings", "CART": "Instacart", "DASH": "DoorDash",
         "EXPE": "Expedia", "LYFT": "Lyft", "TRIP": "Tripadvisor", "UBER": "Uber"}
MAIN = {  # the main KPI's model line and how a question names it
    "UBER": ("gross_bookings", "gross bookings"), "LYFT": ("gross_bookings", "gross bookings"),
    "DASH": ("gross_bookings", "Marketplace GOV"), "CART": ("gross_bookings", "GTV"),
    "BKNG": ("volume", "room nights"), "EXPE": ("gross_bookings", "gross bookings"),
    "ABNB": ("volume", "nights and experiences booked"), "TRIP": ("revenue", "revenue"),
}
RATE = {  # a take-rate (or margin) driver and how a question names a 50 bp cut
    "UBER": ("take_rate_mobility.shift0", "Mobility take rate falls 50 bps"),
    "TRIP": ("adj_ebitda_margin.shift0", "adjusted EBITDA margin is 100 bps lower"),
}
GROWTH = {
    "UBER": ("gb_g_mobility.g0", "Mobility gross bookings grow 2 points slower"),
    "TRIP": ("revenue_g.g0", "revenue grows 2 points slower"),
    "BKNG": ("volume_g.g0", "room nights grow 2 points slower"),
    "EXPE": ("volume_g.g0", "room nights grow 2 points slower"),
    "ABNB": ("volume_g.g0", "nights grow 2 points slower"),
}
questions = []


def ref(ticker: str, version: str, key: str, as_of: str = AS_OF) -> str:
    return re.escape(f"model:{ticker}:{as_of}:") + version + re.escape(f":{key}")


def add(qid, ticker, category, question, refs, tools, notes=""):
    questions.append({
        "id": qid, "split": "dev" if len(questions) % 2 else "test", "category": category,
        "company": ticker, "question": question, "as_of": AS_OF, "expected_refs": refs,
        "requires_assumptions": True, "expected_tools": tools, "notes": notes,
        "origin": "drafted", "verified_by": None, "gold_set": "research-gold",
    })


for t, name in NAMES.items():
    main_line, main_name = MAIN[t]
    low = t.lower()
    add(f"{low}-forecast-revenue-2026q3", t, "forecast",
        f"What do we expect for {name}'s Q3 2026 revenue, and how does it compare with the "
        "company's guidance?", [ref(t, "base", "revenue.2026Q3")], ["model_view", "guidance"])
    add(f"{low}-forecast-main-kpi-2026q4", t, "forecast",
        f"What is our forecast for {name}'s Q4 2026 {main_name}?",
        [ref(t, "base", f"{main_line}.2026Q4")], ["model_view"])
    add(f"{low}-forecast-ebitda-2027", t, "forecast",
        f"What is our 2027E adjusted EBITDA for {name}, and what margin does that imply?",
        [ref(t, "base", "adj_ebitda.FY2027")], ["model_view"])
    driver, words = RATE.get(t, ("take_rate.shift0", "take rate falls 50 bps"))
    add(f"{low}-scenario-rate", t, "scenario",
        f"If {name}'s {words}, what happens to 2027E adjusted EBITDA and our price target?",
        [ref(t, r"scratch-\d+", "adj_ebitda.FY2027"),
         ref(t, r"val-scratch-\d+", "price_target")], ["run_scenario"],
        notes=f"driver {driver}")
    driver, words = GROWTH.get(t, ("volume_g.g0", "volume grows 2 points slower"))
    add(f"{low}-scenario-growth", t, "scenario",
        f"What happens to {name}'s 2027E revenue and EPS if {words} than we assume?",
        [ref(t, r"scratch-\d+", "revenue.FY2027"), ref(t, r"scratch-\d+", "eps_diluted.FY2027")],
        ["run_scenario"], notes=f"driver {driver}")
    add(f"{low}-valuation-fair-price", t, "valuation",
        f"What is a fair price for {name} on EV / EBITDA, and what does the DCF say?",
        [ref(t, "val-base", "price_target"), ref(t, "val-base", "dcf_pt"),
         ref(t, "val-base", "multiple_pt")], ["compare_peers", "model_view"],
        notes="any one of the price target, DCF or multiple value")
    add(f"{low}-valuation-eps-pe", t, "valuation",
        f"What is our 2027E EPS for {name}, and what P/E does today's price imply?",
        [ref(t, "base", "eps_diluted.FY2027"), ref(t, "val-base", "pe")],
        ["model_view", "compare_peers"])
    add(f"{low}-variance-2026q2", t, "variance_review",
        f"How did our Q2 2026 revenue estimate for {name} compare with the actual, and what "
        "drove the difference?",
        [r"model:" + t + r":2026-0[45]-\d\d:base:revenue\.2026Q2"], ["variance_review"],
        notes="the estimate made the day after the Q1 2026 release")

for metric, words in (
    ("ev_to_ebitda_less_sbc", "cheapest on 2027E EV / (adjusted EBITDA − SBC)"),
    ("ev_to_ebitda", "most expensive on 2027E EV / adjusted EBITDA"),
    ("adj_ebitda_margin", "highest 2027E adjusted EBITDA margin"),
    ("revenue", "largest 2027E revenue"),
    ("fcf_yield", "highest 2027E free cash flow yield"),
    ("return", "most upside to our price target"),
    ("pe", "lowest 2027E P/E"),
    ("free_cash_flow", "most 2027E free cash flow"),
):
    valuation = metric in ("ev_to_ebitda_less_sbc", "ev_to_ebitda", "fcf_yield", "return", "pe")
    key = metric if valuation else f"{metric}.FY2027"
    add(f"peers-{metric.replace('_', '-')}", None, "peer_comparison",
        f"Which of the eight companies has the {words}?",
        [r"model:[A-Z]+:" + re.escape(AS_OF) + (":val-base:" if valuation else ":base:")
         + re.escape(key)], ["compare_peers"])

OUT.write_text("".join(json.dumps(q) + "\n" for q in questions))
by = {}
for q in questions:
    by[q["category"]] = by.get(q["category"], 0) + 1
print(f"{len(questions)} questions → {OUT}", by)
