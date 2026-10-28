"""Report mode (V4 design doc §9): a resumable step graph, S1–S11, per company and as-of.

  taskset -c 16-31 uv run python -m tenk_agent.research.pipeline UBER 2026-08-06 [--resume]
  taskset -c 16-31 uv run python -m tenk_agent.research.pipeline UBER 2026-08-06 --no-llm

  S1  datapack   code       history with sources                      (research/datapack.py)
  S2  events     sub-agent  what changed this quarter                 → events.json
  S3  model      code       history + layer-2 checks (FAIL stops)     → model history
  S4  propose    sub-agent  bounded assumptions (research.yaml proposer.method)
  S5  forecast   code       drivers, anchors, scenarios, checks again → model.json, checks.json
  S6  peers      code       the sector's default models, cached
  S7  value      code       comps, DCF, PTs, rating, sensitivities    → valuation.json
  S8  revise     code       against the parent version                → revisions.json
  S9  debates    sub-agents one per scenario driver                   → debates.json
  S10 write      LLM        prose slots from facts; number trace; one repair → note.json
  S11 export     code       note.md, note.pdf, workbook.xlsx (Model, Assumptions, Summary,
                            Scenarios, Valuation, Checks, Log), parity, lint of the whole note

LLM steps save their output; --resume reuses what a previous run saved (a resumable report).
Code steps are cheap and always rerun. Each report is saved as a model version (draft) with its
parent: the latest saved version made before this as-of. --no-llm skips S2, S4's LLM, S9 and
S10's prose (code-only drafts and tests).
"""

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from tenk_agent.asof import AsOf
from tenk_agent.corpus import companies_path
from tenk_agent.models import get_model
from tenk_agent.research.book import Book
from tenk_agent.research.forecast import forecast_with_proposer, knobs
from tenk_agent.research.model import checks as layer2
from tenk_agent.research.model.graph import build, load_template
from tenk_agent.research.model.history import TEMPLATE_OF
from tenk_agent.research.note import sections as note_sections
from tenk_agent.research.note import trace
from tenk_agent.research.note.facts import Facts
from tenk_agent.store import Store

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "output" / "reports"


class SavedModel:
    """A saved version's model.json, read like a Model (value, line units)."""

    def __init__(self, data: dict):
        self.data = data
        self.units = {x["name"]: x["unit"] for x in data["lines"]}

    def value(self, line, period):
        cell = self.data["cells"].get(f"{line}|{period}")
        return cell["value"] if cell else None


def run(db: Path, ticker: str, as_of: str, settings: dict, llm_steps: bool = True,
        resume: bool = False, search=None, out: Path = OUT) -> dict:
    store = Store(db, AsOf.of(as_of))
    store.enable_facts()
    if search is not None:  # search what was public at the as-of
        from dataclasses import replace

        search = replace(search, decomposer=replace(search.decomposer, store=store),
                         hybrid=replace(search.hybrid, store=store))
    folder = out / ticker / as_of
    folder.mkdir(parents=True, exist_ok=True)
    llm = None
    if llm_steps:
        config = settings["proposer"]
        llm = get_model(config["model"], base_url=config.get("base_url"), thinking=False)
    budget = settings["proposer"]["budget"]
    exceptions: list[dict] = []

    def saved(name: str, make):
        path = folder / name
        if resume and path.exists():
            return json.loads(path.read_text())
        value = make()
        path.write_text(json.dumps(value, indent=1, default=str))
        return value

    # S1–S5: data pack, events, model, proposer, forecast
    from tenk_agent.evaluation.backtest import releases

    by_period = releases(store, ticker)
    period = max(by_period)
    events = saved("events.json", lambda: _events(llm, store, search, ticker, period, budget)
                   if llm_steps else [])
    if not llm_steps:
        settings = dict(settings, proposer=dict(settings["proposer"], method="defaults"))
    f = forecast_with_proposer(store, ticker, settings, search)
    model = f.model
    results = layer2.run(model, f.pack, store)
    if any(c.status == "fail" for c in results):
        failed = [c.name for c in results if c.status == "fail"]
        raise RuntimeError(f"layer-2 check failed: {failed}")
    exceptions += layer2.exceptions(model, results) + f.exceptions

    # S6–S7: peers and valuation (the book caches the peers' default models)
    book = Book(store, settings)
    book.forecasts[ticker] = f
    valuation = book.valuation(ticker)
    exceptions += [{"severity": "info", "item": e} for e in valuation["exceptions"]]
    sensitivities = _sensitivities(book, ticker)

    # S8: revisions against the parent version
    parents = store.model_versions(ticker)
    parent = parents[-1] if parents else None
    parent_model = (SavedModel(json.loads((Path(parent["path"]) / "model.json").read_text()))
                    if parent else None)
    revisions = _revisions(model, parent, parent_model)

    # S9: debates, one per scenario driver
    _, template = load_template(model.template, ticker)
    if llm_steps:
        from tenk_agent.research.subagents import debate

        debates = saved("debates.json", lambda: [d for d in (
            debate(llm, store, search, ticker, drv, model.assumptions[drv], budget)
            for drv in template.get("scenarios") or [] if drv in model.assumptions) if d])
    else:
        debates = []

    # S10: the note
    facts = Facts()
    prior, prior_ref = _prior(book, ticker, by_period, period, parent, parent_model)
    ctx = context(store, f, valuation, events, debates, revisions, results, exceptions,
                  sensitivities, prior, prior_ref, settings, by_period[period].filing_date)
    built = note_sections.build(ctx, facts)
    if llm_steps:
        from tenk_agent.research.note.write import fill

        cached = folder / "note.json"
        if resume and cached.exists():
            texts = json.loads(cached.read_text())["slots"]
            for s in built:
                for b in s.blocks:
                    if isinstance(b, note_sections.Slot):
                        b.text = texts.get(b.key, "")
        else:
            exceptions += fill(built, facts, llm)
    header = (f"# {ctx['name']} ({ticker}) · Earnings update · {period}\n\nAs of {as_of} · "
              "Status: DRAFT for senior analyst review · Prepared by: research agent  \n"
              "For education and testing. Not investment advice. No rating or PT is approved "
              "until the responsible analyst confirms it.")
    note_md = note_sections.markdown(built, header)
    blocks = [b for s in built for b in s.blocks if isinstance(b, str) and b.strip()]
    slots = [{"key": b.key, "text": b.text, "facts": b.facts} for s in built for b in s.blocks
             if isinstance(b, note_sections.Slot) and b.text]
    untraced = lint(note_md, facts, blocks, slots)
    exceptions += [{"severity": "error", "item": f"untraced number {u.text!r} ({u.where})"}
                   for u in untraced]
    (folder / "note.json").write_text(json.dumps(
        {"slots": {b.key: b.text for s in built for b in s.blocks
                   if isinstance(b, note_sections.Slot)},
         "sections": [s.title for s in built], "untraced": [u.text for u in untraced],
         "blocks": blocks, "slot_facts": slots},
        indent=1))

    # S11: export, version, logs
    numbered = [{"id": f"X-{i:02d}", **x} for i, x in enumerate(exceptions, 1)]
    (folder / "note.md").write_text(note_md + _logs(facts, numbered))
    (folder / "model.json").write_text(json.dumps(model.to_json(), indent=1))
    (folder / "valuation.json").write_text(json.dumps(valuation, indent=1, sort_keys=True))
    (folder / "revisions.json").write_text(json.dumps(revisions, indent=1))
    (folder / "facts.json").write_text(json.dumps(facts.to_json(), indent=1))
    (folder / "exceptions.json").write_text(json.dumps(numbered, indent=1))
    from tenk_agent.research.export import report as export

    workbook_path, parity = export.workbook(folder, model, f, valuation, results, facts,
                                            numbered, revisions, settings)
    export.pdf(folder / "note.md", folder / "note.pdf")
    inputs = hashlib.sha256(json.dumps(model.assumptions, sort_keys=True).encode()).hexdigest()
    version = store.add_model_version(ticker, as_of, parent["id"] if parent else None,
                                      inputs[:16], str(folder))
    summary = {"ticker": ticker, "as_of": as_of, "version": version,
               "parent": parent["id"] if parent else None, "rating": valuation["rating"],
               "price_target": valuation["price_target"], "sections": len(built),
               "facts": len(facts.items), "untraced": len(untraced), "exceptions": len(numbered),
               "parity": parity, "folder": str(folder)}
    (folder / "report.json").write_text(json.dumps(summary, indent=1))
    return summary


def lint(note_md: str, facts: Facts, blocks: list[str] | None = None,
         slots: list[dict] | None = None) -> list:
    """Every number in the note body must trace to a fact; every block code wrote (`blocks`:
    tables and lines) must read exactly as printed, so a figure changed to another fact's value
    is still caught; each prose slot's numbers must trace to that slot's own facts (`slots`:
    {text, facts}), not to any fact in the note."""
    body = note_md.split("\n## ", 1)[1] if "\n## " in note_md else note_md
    out = [trace.Untraced("block changed", b.splitlines()[0][:40]) for b in blocks or []
           if b not in note_md]
    by_id = {f.id: f for f in facts.items}
    for slot in slots or []:
        own = Facts([by_id[i] for i in slot["facts"] if i in by_id])
        if slot["text"] not in note_md:
            out.append(trace.Untraced("prose changed", slot["key"]))
        out += trace.check(slot["text"], own, slot["key"])
    for line in body.splitlines():
        if line.startswith("##") or line.startswith("|---"):
            continue
        out += trace.check(line, facts, line[:40])
    return out


def _events(llm, store, search, ticker, period, budget):
    from tenk_agent.research.subagents import events

    return events(llm, store, search, ticker, period, budget)


def _prior(book, ticker, by_period, period, parent, parent_model):
    """Our estimate for the reported quarter: the parent version's, else the default model
    the day after the previous release."""
    if parent_model:
        version = parent["id"].split(":")[1]
        prefix = f"model:{ticker}:{parent['as_of']}:{version}"
        return parent_model, lambda line, q: f"{prefix}:{line}.{q}"
    from tenk_agent.evaluation.backtest import run_day
    from tenk_agent.research.forecast import next_quarter

    prior_period = next((p for p in sorted(by_period) if next_quarter(p) == period), None)
    if prior_period is None:
        return None, None
    as_of = run_day(by_period[prior_period])
    model = book.at(as_of).base(ticker).model
    return model, lambda line, q: f"model:{ticker}:{as_of}:base:{line}.{q}"


def _revisions(model, parent, parent_model) -> list[dict]:
    if not parent_model:
        return []
    version = parent["id"].split(":")[1]
    out = []
    for line, label in (("revenue", "revenue"), ("adj_ebitda", "adj. EBITDA"),
                        ("eps_diluted", "EPS")):
        for period in [model.forecast[0]] + sorted(
                {c for c in model.columns if c.startswith("FY")})[-2:]:
            old, new = parent_model.value(line, period), model.value(line, period)
            if old is None or new is None:
                continue
            reported = any(q in model.history for q in parent_model.data.get("forecast", []))
            out.append({"label": f"{period} {label}", "old": old, "new": new,
                        "unit": model.line(line).unit,
                        "old_id": f"model:{model.ticker}:{parent['as_of']}:{version}:"
                                  f"{line}.{period}",
                        "new_id": f"model:{model.ticker}:{model.as_of}:base:{line}.{period}",
                        "type": "Reported + assumption" if reported else "Assumption",
                        "confidence": "Low" if period.startswith("FY")
                        and period[2:] > model.as_of[:4] else "Medium"})
    return out


def _sensitivities(book: Book, ticker: str) -> list[dict]:
    """Section 7: the 2027E adj. EBITDA and PT effect of a take-rate, growth and cost move."""
    f = book.base(ticker)
    base = f.model
    names = sorted(base.assumptions)
    picks = []
    rate = next((n for n in names if n.startswith("take_rate") and n.endswith(".shift0")), None)
    growth = next((n for n in names if n.endswith(".g0") and ("volume" in n or "gb_g" in n
                                                              or "revenue_g" in n)), None)
    cost = next((n for n in names if n.startswith("cost_to_gb") and n.endswith(".shift0")), None)
    for driver, delta, what in ((rate, -0.005, "take rate"), (growth, -0.01, "growth"),
                                (cost, 0.005, "costs as a share of gross bookings")):
        if driver:
            picks.append((driver, delta, what))
    base_val = book.valuation(ticker)
    year = sorted({c for c in base.columns if c.startswith("FY")})[-1]
    out = []
    for driver, delta, what in picks:
        model = build(ticker, base.template, base.as_of, base.history, f.pack, base.forecast,
                      base.assumptions | {driver: base.assumptions[driver] + delta})
        version = book.add_scratch(model)
        val = book.valuation(ticker, version)
        out.append({"what": what, "shock": delta * 100, "driver": driver,
                    "ebitda_change": model.value("adj_ebitda", year) - base.value("adj_ebitda",
                                                                                  year),
                    "pt_change": val["price_target"] - base_val["price_target"],
                    "id": book.ref(ticker, version, "adj_ebitda", year),
                    "pt_id": f"model:{ticker}:{base.as_of}:val-{version}:price_target"})
    return out


def context(store, f, valuation, events, debates, revisions, results, exceptions,
            sensitivities, prior, prior_ref, settings, release_date) -> dict:
    model = f.model
    ticker = model.ticker
    company = yaml.safe_load(companies_path().read_text())["companies"][ticker]
    lines, _ = load_template(TEMPLATE_OF[ticker], ticker)
    main = company["main_kpi"]
    main_line = next((x.name for x in lines if x.kind == "input" and x.source == f"kpi:{main}"),
                     "revenue")
    year_ago = (date.fromisoformat(model.as_of) - timedelta(days=365)).isoformat()
    closes = [v for d, v in store.series(f"px:{ticker}:close") if d >= year_ago]
    last = model.history[-1]
    guides = {}
    ids = {}
    for g in store.guidance(ticker):
        if g["target_period"] == last and g["segment"] == "total" and g["basis"] == "level":
            line = {"marketplace_gov": "gross_bookings", "gtv": "gross_bookings"}.get(
                g["metric"], g["metric"])
            mid = g["low"] if g["high"] is None else (g["low"] + g["high"]) / 2
            guides[line], ids[line] = mid, f"guide:{ticker}:{g['metric']}:{last}"
    sources = [f"{x.form} {x.fiscal_year}{'' if x.fiscal_period == 'FY' else x.fiscal_period} "
               f"(filed {x.filing_date})" for x in _latest_filings(store, ticker)]
    sources += ["prices (Tiingo)", "FRED DGS10", "Damodaran ERP"]
    return {
        "ticker": ticker, "name": company["name"], "model": model, "valuation": valuation,
        "main_line": main_line, "range_52w": (min(closes), max(closes)) if closes else (None,
                                                                                         None),
        "release_date": release_date, "prior_model": prior, "prior_ref": prior_ref,
        "guides_last": guides, "guide_ids": ids, "anchors": [a for a in f.anchors if a.reached],
        "debates": debates, "events": events, "revisions": revisions,
        "checks": [asdict(c) for c in results], "sensitivities": sensitivities,
        "weights": settings["scenarios"]["weights"],
        "valuation_weights": settings["valuation"]["weights"], "sources": sources,
        "rating": settings["valuation"]["rating"],
        "anchored": {a.solve for a in f.anchors}, "exceptions": exceptions,
        "insiders": store.insider_summary(ticker, *_quarter_dates(last)) | {
            "period": last},
        "rating_change": None,
    }


def _quarter_dates(quarter: str) -> tuple[str, str]:
    year, q = int(quarter[:4]), int(quarter[5])
    end = (date(year, 3 * q, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return f"{year}-{3 * q - 2:02d}-01", end.isoformat()


def _latest_filings(store, ticker):
    latest = {}
    for x in store.filings():
        if x.ticker == ticker:
            latest[x.form] = x
    return [latest[k] for k in sorted(latest)]


def _logs(facts: Facts, exceptions: list[dict]) -> str:
    rows = ["", "## EVIDENCE LOG", "", "| ID | Figure | Value | Source | Period |",
            "|---|---|---|---|---|"]
    rows += [f"| E-{f.id[2:]} | {f.label} | {f.text()} | {f.source} | {f.period} |"
             for f in facts.items]
    rows += ["", "## EXCEPTION LOG", "", "| ID | Severity | Item |", "|---|---|---|"]
    rows += [f"| {x['id']} | {x['severity']} | {x['item']} |" for x in exceptions]
    return "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("as_of")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--db", type=Path, default=ROOT / "data/v4/build/parent_child.db")
    args = ap.parse_args()
    settings = knobs()
    search = None
    if not args.no_llm:
        from tenk_agent.retrieval.search import SubQuestionRetriever

        retrieval = yaml.safe_load((ROOT / "experiments/v1/config.yaml").read_text())["retrieval"]
        today = Store(args.db, AsOf.today())
        search = SubQuestionRetriever.from_settings(today, retrieval | {"device": "cpu"})
    for ticker in args.tickers:
        print(json.dumps(run(args.db, ticker, args.as_of, settings, not args.no_llm, args.resume,
                             search), indent=1))


if __name__ == "__main__":
    main()
