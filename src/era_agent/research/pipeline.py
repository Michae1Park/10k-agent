"""Report mode (V4 design doc §9): a resumable step graph, S1–S11, per company and as-of.

  uv run python -m era_agent.research.pipeline UBER 2026-08-06 [--resume]
  uv run python -m era_agent.research.pipeline UBER 2026-08-06 --no-llm

  S1  datapack   code       history with sources                      (research/datapack.py)
  S2  events     sub-agent  what changed this quarter                 → events.json
  S3  model      code       history + layer-2 checks (FAIL stops)     → model history
  S4  propose    sub-agent  bounded assumptions (research.yaml proposer.method)
  S5  forecast   code       drivers, anchors, scenarios, checks again → model.json, checks.json
  S6  peers      code       the sector's default models, cached
  S7  value      code       comps, DCF, PTs, rating, sensitivities    → valuation.json
  S8  revise     code       against the parent version                → revisions.json
  S9  debates    sub-agents one per scenario driver                   → debates.json
                            (then section 7's sensitivities, every debated driver included)
  S10 write      LLM        prose slots from facts; number trace; one repair → note.json;
                            consistency check (research/note/consistency.py), one repair turn
                            for the takeaways; section 10 written last, by exception X-ids
  S11 export     code       note.md, note.pdf, workbook.xlsx (Model, Assumptions, Summary,
                            Scenarios, Valuation, Checks, Log), parity, lint of the whole note

Budgets (spec §2.3 requirement 8): research.yaml `budgets` gives each LLM step its calls and
tokens (events, debate, section) and the run's target minutes; report.json records each step's
seconds, LLM calls and tokens, and a run over the target is an exception.

LLM steps save their output; --resume reuses what a previous run saved (a resumable report).
Code steps are cheap and always rerun. Each report is saved as a model version (draft) with its
parent: the latest saved version made before this as-of. --no-llm skips S2, S4's LLM, S9 and
S10's prose (code-only drafts and tests).
"""

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from era_agent.asof import AsOf
from era_agent.corpus import companies_path
from era_agent.models import get_model
from era_agent.research.book import Book
from era_agent.research.forecast import forecast_with_proposer, knobs
from era_agent.research.model import checks as layer2
from era_agent.research.model.graph import build, load_template
from era_agent.research.model.history import TEMPLATE_OF
from era_agent.research.note import sections as note_sections
from era_agent.research.note import trace
from era_agent.research.note.facts import Facts
from era_agent.store import Store

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "output" / "reports"


class Meter:
    """The LLM, counting calls and tokens per step (report.json `timing`)."""

    def __init__(self, llm):
        self.llm, self.step, self.usage = llm, "", {}

    def complete(self, *args, **kwargs):
        reply = self.llm.complete(*args, **kwargs)
        u = self.usage.setdefault(self.step, {"calls": 0, "input_tokens": 0,
                                              "output_tokens": 0})
        u["calls"] += 1
        u["input_tokens"] += getattr(reply, "input_tokens", 0) or 0
        u["output_tokens"] += getattr(reply, "output_tokens", 0) or 0
        return reply

    def __getattr__(self, name):
        return getattr(self.llm, name)


class Clock:
    """Seconds per step, and LLM usage from the Meter."""

    def __init__(self, meter: Meter | None):
        self.meter, self.start, self.steps = meter, time.perf_counter(), {}
        self._last, self._name = self.start, None

    def step(self, name: str) -> None:
        now = time.perf_counter()
        if self._name:
            self.steps[self._name] = round(self.steps.get(self._name, 0) + now - self._last, 1)
        self._name, self._last = name, now
        if self.meter:
            self.meter.step = name

    def summary(self) -> dict:
        self.step("")
        total = round(time.perf_counter() - self.start, 1)
        usage = self.meter.usage if self.meter else {}
        return {"seconds": total, "steps": {k: {"seconds": v} | usage.get(k, {})
                                            for k, v in self.steps.items() if k}}


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
        llm = Meter(get_model(config["model"], base_url=config.get("base_url"), thinking=False))
    clock = Clock(llm)
    budgets = budgets_of(settings)
    exceptions: list[dict] = []

    def saved(name: str, make):
        path = folder / name
        if resume and path.exists():
            return json.loads(path.read_text())
        value = make()
        path.write_text(json.dumps(value, indent=1, default=str))
        return value

    # S1–S5: data pack, events, model, proposer, forecast
    from era_agent.evaluation.backtest import releases

    by_period = releases(store, ticker)
    period = max(by_period)
    clock.step("S2 events")
    events = saved("events.json", lambda: _events(llm, store, search, ticker, period,
                                                  budgets["events"]) if llm_steps else [])
    clock.step("S3-S5 model and forecast")
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
    clock.step("S6-S7 peers and valuation")
    book = Book(store, settings)
    book.forecasts[ticker] = f
    valuation = book.valuation(ticker)
    results += layer2.valuation_checks(valuation, settings)
    if any(c.status == "fail" for c in results):
        failed = [f"{c.name}: {c.failures}" for c in results if c.status == "fail"]
        raise RuntimeError(f"valuation check failed: {failed}")
    exceptions += [{"severity": "info", "item": e} for e in valuation["exceptions"]]

    # S8: revisions against the parent version
    clock.step("S8 revisions")
    parents = store.model_versions(ticker)
    parent = parents[-1] if parents else None
    parent_model = (SavedModel(json.loads((Path(parent["path"]) / "model.json").read_text()))
                    if parent else None)
    revisions = _revisions(model, parent, parent_model)

    # S9: debates, one per scenario driver; then the sensitivities, with each debated driver
    clock.step("S9 debates")
    _, template = load_template(model.template, ticker)
    if llm_steps:
        from era_agent.research.subagents import debate

        debates = saved("debates.json", lambda: [d for d in (
            debate(llm, store, search, ticker, drv, model.assumptions[drv], budgets["debate"])
            for drv in template.get("scenarios") or [] if drv in model.assumptions) if d])
    else:
        debates = []
    clock.step("S7 sensitivities")
    sensitivities = _sensitivities(book, ticker, [d["driver"] for d in debates])

    # S10: the note
    clock.step("S10 write")
    facts = Facts()
    prior, prior_ref = _prior(book, ticker, by_period, period)
    ctx = context(store, f, valuation, events, debates, revisions, results, exceptions,
                  sensitivities, prior, prior_ref, settings, by_period[period].filing_date,
                  parent)
    built = note_sections.build(ctx, facts)
    if llm_steps:
        from era_agent.research.note.write import fill

        cached = folder / "note.json"
        if resume and cached.exists():
            texts = json.loads(cached.read_text())["slots"]
            for s in built:
                for b in s.blocks:
                    if isinstance(b, note_sections.Slot):
                        b.text = texts.get(b.key, "")
        else:
            exceptions += fill(built, facts, llm, budgets["section"]["max_tokens"])

    # Consistency check (spec §2.3 requirement 5): code checks; a failure is an exception row;
    # the takeaways (section 1) get one repair turn, other sections are never rewritten
    from era_agent.research.note import consistency

    def consistent():
        return consistency.check(built, facts, valuation, settings["valuation"]["rating"],
                                 debates, sensitivities, events)

    found = consistent()
    s1_failed = next(r for r in found if r.name == "section1_numbers").failures
    if s1_failed and llm_steps:
        from era_agent.research.note.write import repair

        repair(built[0], "takeaways", facts, llm, s1_failed, budgets["section"]["max_tokens"])
        found = consistent()
    exceptions += consistency.as_exceptions(found)
    header = (f"# {ctx['name']} ({ticker}) · Earnings update · {period}\n\nAs of {as_of} · "
              "Status: DRAFT for senior analyst review · Prepared by: research agent  \n"
              "No rating or PT is approved until the responsible analyst confirms it.")
    note_md = note_sections.markdown(built, header)
    s10 = next(s for s in built if s.number == 10)
    # section 10 and section 9's exception count are rewritten at export, once the log is final
    blocks = [b for s in built if s is not s10 for b in s.blocks
              if isinstance(b, str) and b.strip() and not b.startswith("Exceptions: ")]
    slots = [{"key": b.key, "text": b.text, "facts": b.facts} for s in built for b in s.blocks
             if isinstance(b, note_sections.Slot) and b.text]
    untraced = lint(note_md, facts, blocks, slots, final=False)
    exceptions += [{"severity": "error", "item": f"untraced number {u.text!r} ({u.where})"}
                   for u in untraced]
    (folder / "note.json").write_text(json.dumps(
        {"slots": {b.key: b.text for s in built for b in s.blocks
                   if isinstance(b, note_sections.Slot)},
         "sections": [s.title for s in built], "untraced": [u.text for u in untraced],
         "blocks": blocks, "slot_facts": slots},
        indent=1))

    # S11: export, version, logs; section 10 last, listing the numbered open issues
    clock.step("S11 export")
    elapsed = time.perf_counter() - clock.start
    if elapsed > budgets["run_minutes"] * 60:
        exceptions.append({"severity": "warning", "item": f"run time over the target of "
                           f"{budgets['run_minutes']} minutes (see report.json timing)"})
    numbered = [{"id": f"X-{i:02d}", **x} for i, x in enumerate(exceptions, 1)]
    s10.blocks = note_sections.review_blocks(numbered)
    facts.add("exceptions in the log", float(len(numbered)), "count", "exception log")
    for sev in ("error", "warning", "info"):  # section 9 prints each count beside the total
        n = sum(x["severity"] == sev for x in numbered)
        if n:
            facts.add(f"{sev} exceptions in the log", float(n), "count", "exception log")
    s9 = next(s for s in built if s.number == 9)
    s9.blocks = [note_sections.exceptions_line(numbered) if isinstance(b, str)
                 and b.startswith("Exceptions: ") else b for b in s9.blocks]
    # the final line joins the code-written blocks, so a later lint checks it verbatim
    saved = json.loads((folder / "note.json").read_text())
    saved["blocks"].append(note_sections.exceptions_line(numbered))
    (folder / "note.json").write_text(json.dumps(saved, indent=1))
    issues = consistency.open_issues(s10, numbered)
    if not issues.ok:  # by construction it passes; a failure is logged, never hidden
        numbered += [{"id": f"X-{len(numbered) + i:02d}", **x}
                     for i, x in enumerate(consistency.as_exceptions([issues]), 1)]
    note_md = note_sections.markdown(built, header)
    (folder / "note.md").write_text(note_md + _logs(facts, numbered))
    (folder / "model.json").write_text(json.dumps(model.to_json(), indent=1))
    (folder / "valuation.json").write_text(json.dumps(valuation, indent=1, sort_keys=True))
    (folder / "revisions.json").write_text(json.dumps(revisions, indent=1))
    (folder / "facts.json").write_text(json.dumps(facts.to_json(), indent=1))
    (folder / "exceptions.json").write_text(json.dumps(numbered, indent=1))
    from era_agent.research.export import report as export

    workbook_path, parity = export.workbook(
        folder, model, f, valuation, results, facts, numbered, revisions, settings,
        parent_model.data if parent_model else None, parent["id"] if parent else None)
    export.pdf(folder / "note.md", folder / "note.pdf")
    inputs = hashlib.sha256(json.dumps(model.assumptions, sort_keys=True).encode()).hexdigest()
    version = store.add_model_version(ticker, as_of, parent["id"] if parent else None,
                                      inputs[:16], str(folder))
    summary = {"ticker": ticker, "as_of": as_of, "version": version,
               "parent": parent["id"] if parent else None, "rating": valuation["rating"],
               "price_target": valuation["price_target"], "sections": len(built),
               "facts": len(facts.items), "untraced": len(untraced), "exceptions": len(numbered),
               "consistency": {r.name: r.ok for r in found} | {"open_issues": issues.ok},
               "parity": parity, "folder": str(folder), "timing": clock.summary()}
    (folder / "report.json").write_text(json.dumps(summary, indent=1))
    return summary


def lint(note_md: str, facts: Facts, blocks: list[str] | None = None,
         slots: list[dict] | None = None, final: bool = True) -> list:
    """Every number in the note body must trace to a fact; every block code wrote (`blocks`:
    tables and lines) must read exactly as printed, so a figure changed to another fact's value
    is still caught; each prose slot's numbers must trace to that slot's own facts (`slots`:
    {text, facts}), not to any fact in the note. final=False (inside the run): section 9's
    exception count is not set yet, so its line is skipped."""
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
        if line.startswith(("##", "|---")) or (not final and line.startswith("Exceptions: ")):
            continue
        out += trace.check(line, facts, line[:40])
    return out


def _events(llm, store, search, ticker, period, budget):
    from era_agent.research.subagents import events

    return events(llm, store, search, ticker, period, budget)


def _prior(book, ticker, by_period, period):
    """Our estimate for the reported quarter (section 2.1): the base forecast made the day
    after the previous release (design doc §9.2). Section 4's "old" is the parent version."""
    from era_agent.evaluation.backtest import run_day
    from era_agent.research.forecast import next_quarter

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
                        "source": "Reported + assumption" if reported else "Assumption",
                        "confidence": "Low" if period.startswith("FY")
                        and period[2:] > model.as_of[:4] else "Medium"})
    return out


def budgets_of(settings: dict) -> dict:
    """research.yaml `budgets`; a step without one uses the proposer's (older knob files)."""
    default = settings["proposer"]["budget"]
    given = settings.get("budgets") or {}
    return {"events": given.get("events", default), "debate": given.get("debate", default),
            "section": given.get("section", {"max_tokens": 1500}),
            "run_minutes": given.get("run_minutes", 30)}


def _sensitivities(book: Book, ticker: str, debated: list[str] = ()) -> list[dict]:
    """Section 7: the target-year adj. EBITDA and PT effect of a take-rate, growth and cost move,
    and of a move in each debated driver (spec §2.3: every debated driver has a sensitivity)."""
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
    for driver in debated:
        if driver in base.assumptions and driver not in {p[0] for p in picks}:
            line = driver.rsplit(".", 1)[0].replace("_", " ")
            if driver.endswith(".g0"):
                picks.append((driver, -0.01, f"debated: {line}"))
            elif driver.endswith(".shift0"):
                picks.append((driver, 0.005 if line.startswith("cost") else -0.005,
                              f"debated: {line}"))
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
            sensitivities, prior, prior_ref, settings, release_date, parent=None) -> dict:
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
        "parent": parent_view(parent),
    }


def parent_view(parent: dict | None) -> dict | None:
    """The parent version's id, as-of, rating and price target (section 1's change line)."""
    if not parent:
        return None
    saved = json.loads((Path(parent["path"]) / "valuation.json").read_text())
    return {"id": parent["id"], "as_of": parent["as_of"], "rating": saved["rating"],
            "price_target": saved["price_target"]}


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
        from era_agent.retrieval.search import SubQuestionRetriever

        retrieval = yaml.safe_load((ROOT / "experiments/v1/config.yaml").read_text())["retrieval"]
        today = Store(args.db, AsOf.today())
        search = SubQuestionRetriever.from_settings(today, retrieval | {"device": "cpu"})
    for ticker in args.tickers:
        print(json.dumps(run(args.db, ticker, args.as_of, settings, not args.no_llm, args.resume,
                             search), indent=1))


if __name__ == "__main__":
    main()
