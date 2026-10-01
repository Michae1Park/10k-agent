"""Generated questions with known answers, over the active (training) corpus.

Answers come from each filing's XBRL facts (the same lookup as the V5 tool), so no model
writes a question or an answer. Question kinds mirror the eval categories the agent is
scored on: single values, several years, a calculated change, two companies, and two kinds
of abstention. Whole companies are held out (`split: heldout`) for an in-distribution test.
"""

import hashlib
import json
import random
from pathlib import Path

from tenk_agent import gold
from tenk_agent.corpus import COMPANIES, EVAL_COMPANIES, FISCAL_YEARS, Company
from tenk_agent.store import Store
from tenk_agent.xbrl import financial_facts

# Metric -> how a question names it.
LABELS = {
    "revenue": "total revenue",
    "cost_of_revenue": "cost of revenue",
    "gross_profit": "gross profit",
    "research_and_development": "research and development expense",
    "operating_income": "operating income",
    "net_income": "net income",
    "capital_expenditures": "capital expenditures (purchases of property and equipment)",
    "operating_cash_flow": "net cash provided by operating activities",
    "total_assets": "total assets",
    "cash_and_equivalents": "cash and cash equivalents",
}
SINGLE = (
    "What was {name}'s {label} in fiscal {year}?",
    "How much did {name} report as {label} for fiscal year {year}?",
    "According to its 10-K, what was {name}'s {label} in FY{year}?",
)
SERIES = "What was {name}'s {label} in fiscal years {years}?"
CHANGE = "By what percentage did {name}'s {label} change from fiscal {prev} to fiscal {year}?"
PAIR = "Compare {a}'s and {b}'s {label} in fiscal {year}."
OUT_OF_RANGE = "What was {name}'s {label} in fiscal {year}?"
# Companies outside both corpora, for "not in the corpus" abstentions. Names used by a gold
# question are dropped at generation time.
OUTSIDE = ("IBM", "Walt Disney", "Exxon Mobil", "Caterpillar", "UnitedHealth", "McDonald's")


def short_name(company: Company) -> str:
    return company.aliases[0] if company.aliases else company.name.split(",")[0]


def split_of(tickers: list[str], heldout: set[str]) -> str:
    return "heldout" if set(tickers) & heldout else "train"


def heldout_companies(companies, fraction: float, seed: int) -> set[str]:
    tickers = sorted(c.ticker for c in companies)
    count = max(1, round(len(tickers) * fraction))
    return set(random.Random(seed).sample(tickers, count))


def generate(
    store: Store, data_dir: Path, heldout_fraction: float = 0.2, seed: int = 7
) -> list[dict]:
    if {c.ticker for c in COMPANIES} & {c.ticker for c in EVAL_COMPANIES}:
        raise SystemExit("The active corpus overlaps the eval corpus; set TENK_CORPUS (D-022).")
    rng = random.Random(seed)
    heldout = heldout_companies(COMPANIES, heldout_fraction, seed)
    facts = _all_facts(store, data_dir)
    questions: list[dict] = []

    def add(kind: str, tickers: list[str], text: str, **fields) -> None:
        digest = hashlib.sha1(text.encode()).hexdigest()[:10]
        questions.append(
            {
                "id": f"ft-{kind}-{digest}",
                "split": split_of(tickers, heldout),
                "category": "failure_case" if kind.startswith("abstain") else "retrieval",
                "failure_mode": "unsupported" if kind.startswith("abstain") else None,
                "question": text,
                "kind": kind,
                "tolerance": 0.005,
                "should_abstain": False,
                "origin": "xbrl_template",
            }
            | fields
        )

    for company in COMPANIES:
        name, by_year = short_name(company), facts.get(company.ticker, {})
        for metric, label in LABELS.items():
            values = {y: v[metric] for y, v in by_year.items() if v.get(metric) is not None}
            for year, value in sorted(values.items()):
                add(
                    "single",
                    [company.ticker],
                    rng.choice(SINGLE).format(name=name, label=label, year=year),
                    answer_type="number",
                    expected_answer={"value": value, "unit": "USD millions"},
                    sources=[_source(company.ticker, year)],
                    expected_tools=["search_filings"],
                )
                prev = values.get(year - 1)
                if prev:
                    add(
                        "change",
                        [company.ticker],
                        CHANGE.format(name=name, label=label, prev=year - 1, year=year),
                        answer_type="number",
                        expected_answer={
                            "value": round((value - prev) / abs(prev) * 100, 2),
                            "unit": "percent",
                        },
                        tolerance=0.01,
                        # The later year first: scoring takes its company and year from it.
                        sources=[_source(company.ticker, year), _source(company.ticker, year - 1)],
                        expected_tools=["search_filings", "calculator"],
                    )
            if len(values) >= 2:
                years = sorted(values)
                add(
                    "series",
                    [company.ticker],
                    SERIES.format(
                        name=name,
                        label=label,
                        years=", ".join(map(str, years[:-1])) + f" and {years[-1]}",
                    ),
                    answer_type="number_set",
                    expected_answer=[
                        {"fiscal_year": y, "value": values[y], "unit": "USD millions"}
                        for y in years
                    ],
                    sources=[_source(company.ticker, y) for y in years],
                    expected_tools=["search_filings"],
                )
        add(
            "abstain_year",
            [company.ticker],
            OUT_OF_RANGE.format(name=name, label="total revenue", year=FISCAL_YEARS[0] - 3),
            answer_type="abstain",
            should_abstain=True,
            sources=[],
            expected_tools=[],
        )

    tickers = sorted(facts)
    for _ in range(len(tickers) * 3):
        a, b = rng.sample(tickers, 2)
        year, metric = rng.choice(FISCAL_YEARS), rng.choice(list(LABELS))
        va, vb = facts[a].get(year, {}).get(metric), facts[b].get(year, {}).get(metric)
        if va is None or vb is None:
            continue
        ca, cb = (next(c for c in COMPANIES if c.ticker == t) for t in (a, b))
        add(
            "pair",
            [a, b],
            PAIR.format(a=short_name(ca), b=short_name(cb), label=LABELS[metric], year=year),
            answer_type="number_set",
            expected_answer=[
                {"company": a, "fiscal_year": year, "value": va, "unit": "USD millions"},
                {"company": b, "fiscal_year": year, "value": vb, "unit": "USD millions"},
            ],
            sources=[_source(a, year), _source(b, year)],
            expected_tools=["search_filings"],
        )

    gold_text = " ".join(q["question"].lower() for q in gold.load())
    for outside in OUTSIDE:
        if outside.lower() in gold_text:
            continue
        year = rng.choice(FISCAL_YEARS)
        add(
            "abstain_company",
            [],
            f"What was {outside}'s total revenue in fiscal {year}?",
            answer_type="abstain",
            should_abstain=True,
            sources=[],
            expected_tools=[],
        )

    unique = {q["id"]: q for q in questions}  # a repeated pair draw is one question
    return list(unique.values())


def _source(ticker: str, year: int) -> dict:
    # No evidence quote: the answer is the XBRL fact, so retrieval isn't scored.
    return {"company": ticker, "fiscal_year": year, "item": "8", "section": "", "evidence": ""}


def _all_facts(store: Store, data_dir: Path) -> dict[str, dict[int, dict[str, float]]]:
    facts: dict[str, dict[int, dict[str, float]]] = {}
    for filing in store.filings():
        result = financial_facts(data_dir, store, filing.ticker, filing.fiscal_year)
        values = {m: v["value"] for m, v in result.get("values", {}).items() if v and "value" in v}
        facts.setdefault(filing.ticker, {})[filing.fiscal_year] = values
    return facts


def write(questions: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(q) + "\n" for q in questions))
