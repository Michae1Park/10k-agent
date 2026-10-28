"""The note's ten sections (spec §2.3), built by code from the pipeline's artifacts.

Every figure is registered as a fact (research/note/facts.py) and tables print facts. Prose
slots — the takeaways, the quality-of-quarter conclusion, each debate and the valuation's
justification — are written by research/note/write.py from the slot's facts only, and the
number trace checks them. Sections 1–5 are the core deliverable; 6–10 add valuation, risks,
catalysts, sources and the review block.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from tenk_agent.research.note.facts import Fact, Facts

TITLES = ("Executive summary", "Earnings results", "Drivers and key debates",
          "Estimate revisions and model impact", "Financial summary", "Valuation",
          "Risks and sensitivity", "Catalysts", "Sources, assumptions, disclosures",
          "Senior analyst review")
NAMES = {"revenue": "Revenue", "adj_ebitda": "Adj. EBITDA", "gross_bookings": "Gross bookings",
         "volume": "Volume", "eps_diluted": "Diluted EPS", "free_cash_flow": "Free cash flow",
         "adj_ebitda_margin": "Adj. EBITDA margin", "take_rate": "Take rate", "sbc": "SBC",
         "operating_income": "GAAP operating income", "shares_diluted": "Diluted shares",
         "trips": "Trips"}


@dataclass
class Slot:
    """Prose the writer fills: its instruction and the facts it may use."""
    key: str
    instruction: str
    facts: list[str]  # F-ids
    text: str = ""


@dataclass
class Section:
    number: int
    title: str
    blocks: list = field(default_factory=list)  # str (markdown) or Slot


def fact_row(label: str, facts: list[Fact | None]) -> str:
    return f"| {label} | " + " | ".join(f.text() if f else "n/a" for f in facts) + " |"


def table(header: list[str], rows: list[str]) -> str:
    return "\n".join([f"| {' | '.join(header)} |", "|" + "---|" * len(header), *rows])


def pct(a, b):
    return None if a is None or b in (None, 0) else (a / b - 1) * 100


def build(ctx: dict, facts: Facts) -> list[Section]:
    """ctx: the artifacts (see research/pipeline.py `context`)."""
    model, val, ticker = ctx["model"], ctx["valuation"], ctx["ticker"]
    last = model.history[-1]
    first = model.forecast[0]
    main = ctx["main_line"]
    year = int(last[:4])
    sections = []

    def add(label, value, unit, source, period=""):
        return facts.add(label, value, unit, source, period) if value is not None else None

    def mref(line, period, version="base"):
        return f"model:{ticker}:{model.as_of}:{version}:{line}.{period}"

    def cell(line, period, label=None):
        if not any(x.name == line for x in model.lines):
            return None
        v, c = model.value(line, period), model.cells.get((line, period))
        unit = model.line(line).unit
        if unit == "fraction" and v is not None:
            v, unit = v * 100, "percent"
        source = (c.ref if c and c.ref else mref(line, period))
        return add(label or f"{period} {NAMES.get(line, line)}", v, unit, source, period)

    # 1 -----------------------------------------------------------------------------------
    s1 = Section(1, TITLES[0])
    price = add("Price", val["price"], "USD", val["refs"]["price"])
    pt = add("12-month price target", val["price_target"], "USD",
             f"model:{ticker}:{model.as_of}:val-base:price_target")
    ret = add("Implied return", val["return"] * 100, "percent",
              f"model:{ticker}:{model.as_of}:val-base:return")
    rating = add("Draft rating", val["rating"], "text",
                 f"model:{ticker}:{model.as_of}:val-base:rating")
    mcap = add("Market cap", val["market_cap"], "USD millions", "computed: price × shares")
    ev = add("Enterprise value", val["ev"], "USD millions", "computed: market cap + net debt")
    lo, hi = ctx["range_52w"]
    low52, high52 = add("52-week low", lo, "USD", "px"), add("52-week high", hi, "USD", "px")
    nxt = (date.fromisoformat(ctx["release_date"]) + timedelta(days=91)).isoformat()
    s1.blocks.append(table(["Draft rating", "Price", "12-month PT", "Return", "Market cap", "EV",
                            "52-week", "Next earnings"],
                           [f"| {rating.text()} | {price.text()} | {pt.text()} | {ret.text()} | "
                            f"{mcap.text()} | {ev.text()} | {low52.text()}–{high52.text()} | "
                            f"{nxt} (est.) |"]))
    change = ctx.get("rating_change") or "initial draft (no prior version)"
    s1.blocks.append(f"Rating / PT change: {change}")
    takeaway_facts = [price.id, pt.id, ret.id, rating.id]
    sections.append(s1)

    # 2 -----------------------------------------------------------------------------------
    s2 = Section(2, TITLES[1])
    prior = ctx.get("prior_model")
    rows, beat_facts = [], []
    for line in ("revenue", "adj_ebitda", main, "eps_diluted"):
        if not any(x.name == line for x in model.lines):
            continue
        actual = cell(line, last, f"{last} {NAMES.get(line, line)} actual")
        est = (add(f"{last} {NAMES.get(line, line)} our estimate", prior.value(line, last),
                   model.line(line).unit, ctx["prior_ref"](line, last), last)
               if prior and prior.value(line, last) is not None else None)
        var = add(f"{last} {NAMES.get(line, line)} variance vs our estimate",
                  pct(actual.value, est.value) if actual and est else None, "percent",
                  f"computed from {actual.id if actual else ''}, {est.id if est else ''}")
        guide = ctx["guides_last"].get(line)
        mid = add(f"{last} {NAMES.get(line, line)} guidance midpoint", guide,
                  model.line(line).unit, ctx["guide_ids"].get(line, "guide"), last)
        vmid = add(f"{last} {NAMES.get(line, line)} variance vs guide midpoint",
                   pct(actual.value, guide) if actual and guide else None, "percent",
                   f"computed from {actual.id if actual else ''}, {mid.id if mid else ''}")
        yago = model.quarters[model.quarters.index(last) - 4]
        yoy = add(f"{last} {NAMES.get(line, line)} y/y change",
                  pct(model.value(line, last), model.value(line, yago)), "percent",
                  f"computed from {mref(line, last)}, {mref(line, yago)}")
        rows.append(fact_row(NAMES.get(line, line), [actual, est, var, mid, vmid, yoy]))
        beat_facts += [f.id for f in (actual, est, var, mid, vmid, yoy) if f]
    s2.blocks.append(f"2.1 Headline ({last})")
    s2.blocks.append(table(["", "Actual", "Our est.", "Var.", "Guide mid", "Var. vs mid", "Y/Y"],
                           rows))
    if not prior:
        s2.blocks.append("Our estimate: the model as of the day after the previous release "
                         "(defaults; no saved prior version).")
    s2.blocks.append(f"2.2 Guidance for {first}")
    grows = []
    for a in ctx["anchors"]:
        mid = add(f"{first} {a.guide} guidance midpoint", a.target, model.line(a.line).unit,
                  a.guide_id, first)
        ours = cell(a.line, first, f"{first} {NAMES.get(a.line, a.line)} our forecast")
        var = add(f"{first} {a.guide} our forecast vs midpoint",
                  pct(ours.value, a.target) if ours else None, "percent", "computed")
        grows.append(fact_row(a.guide.replace("_", " "), [mid, ours, var]))
        beat_facts += [f.id for f in (mid, ours, var) if f]
    s2.blocks.append(table(["Guided line", "Midpoint", "Our forecast", "Var. vs mid"], grows)
                     if grows else "No numeric guidance for the coming quarter.")
    s2.blocks.append("2.3 Quality of the quarter")
    yago = model.quarters[model.quarters.index(last) - 4]
    qrows, quality_facts = [], []
    for line in [n for n in ("volume", "trips", "gross_bookings", "value_per_unit",
                             "gross_bookings_per_unit", "take_rate", "adj_ebitda_margin",
                             "operating_cash_flow", "capex", "free_cash_flow", "sbc")
                 if any(x.name == n for x in model.lines)]:
        a, b = cell(line, last), cell(line, yago)
        ratio = model.line(line).unit == "fraction"
        ch = add(f"{last} {NAMES.get(line, line)} y/y change",
                 (a.value - b.value) if a and b and ratio else
                 pct(a.value if a else None, b.value if b else None),
                 "points" if ratio else "percent", "computed")
        qrows.append(fact_row(NAMES.get(line, line.replace("_", " ")), [a, b, ch]))
        quality_facts += [f.id for f in (a, b, ch) if f]
    s2.blocks.append(table(["", last, yago, "Change"], qrows))
    s2.blocks.append(Slot("quality", "In three to five sentences, conclude on the quality of "
                          "the quarter: what drove growth (volume vs price vs take rate), "
                          "whether cash backs EBITDA, and what stays open. Use only these facts.",
                          quality_facts))
    sections.append(s2)
    takeaway_facts += beat_facts[:12]

    # 3 -----------------------------------------------------------------------------------
    s3 = Section(3, TITLES[2])
    for i, d in enumerate(ctx["debates"], 1):
        evidence = [add(f"evidence {e['id']}", e["value"], e["unit"], e["id"], e.get("period", ""))
                    for e in d.get("evidence_values", [])]
        driver = d.get("driver")
        if driver in model.assumptions:  # the debated assumption, as the model holds it
            evidence.append(add(f"assumption {driver}", model.assumptions[driver] * 100,
                                "percent", f"model:{ticker}:{model.as_of}:base (assumption)"))
        s3.blocks.append(f"3.{i} {d['question']}")
        s3.blocks.append(Slot(f"debate-{i}", "Write this debate in four labelled lines — Bull, "
                              "Bear, Hypothesis, Trigger — and one Data gap line, from the "
                              f"debate below and these facts.\nDebate: {d}",
                              [f.id for f in evidence if f]))
    if not ctx["debates"]:
        s3.blocks.append("No debates were run (Report steps S9).")
    sections.append(s3)

    # 4 -----------------------------------------------------------------------------------
    s4 = Section(4, TITLES[3])
    revisions = ctx.get("revisions") or []
    if revisions:
        rrows = []
        for r in revisions:
            old = add(f"{r['label']} old", r["old"], r["unit"], r["old_id"])
            new = add(f"{r['label']} new", r["new"], r["unit"], r["new_id"])
            ch = add(f"{r['label']} change", pct(r["new"], r["old"]), "percent", "computed")
            rrows.append(fact_row(r["label"], [old, new, ch]) + f" {r['type']} | "
                         f"{r['confidence']} |")
        s4.blocks.append(table(["", "Old", "New", "Change", "Type", "Confidence"], rrows))
    else:
        s4.blocks.append("Initial draft: no prior version to revise.")
    s4.blocks.append("4.3 Integrity checks (layer 2)")
    s4.blocks.append(" · ".join(f"[{c['status']}] {c['name']}" for c in ctx["checks"]))
    sections.append(s4)

    # 5 -----------------------------------------------------------------------------------
    s5 = Section(5, TITLES[4])
    years = [y for y in (f"FY{year - 2}", f"FY{year - 1}", f"FY{year}", f"FY{year + 1}")
             if y in model.columns]
    labels = [y[2:] + ("A" if not any(q.startswith(y[2:]) for q in model.forecast) else "E")
              for y in years]
    frows = []
    for line in ("revenue", "gross_bookings", "adj_ebitda", "adj_ebitda_margin",
                 "operating_income", "sbc", "eps_diluted", "free_cash_flow", "shares_diluted"):
        if any(x.name == line for x in model.lines):
            frows.append(fact_row(NAMES.get(line, line),
                                  [cell(line, y, f"{y} {NAMES.get(line, line)}") for y in years]))
    s5.blocks.append(table(["", *labels], frows))
    s5.blocks.append("Quarterly detail, segments and the assumption register: workbook.")
    sections.append(s5)

    # 6 -----------------------------------------------------------------------------------
    s6 = Section(6, TITLES[5])
    peers = val["peer_multiples"]
    prow = [add(f"{t} 2027E EV/EBITDA", m, "x", f"model:{t}:{model.as_of}:val-base:ev_to_ebitda")
            for t, m in sorted(peers["peers"].items())]
    med = add("Peer median 2027E EV/EBITDA", peers["median"], "x", "computed")
    own = add(f"{ticker} 2027E EV/EBITDA", val["multiples_2027e"]["ev_to_ebitda"], "x",
              f"model:{ticker}:{model.as_of}:val-base:ev_to_ebitda")
    s6.blocks.append("Comps (2027E EV/EBITDA): " + " · ".join(
        f"{t} {f.text()}" for t, f in zip(sorted(peers["peers"]), prow, strict=True))
                     + f" · median {med.text()} · {ticker} now {own.text()}")
    cc = val["cost_of_capital"]
    wacc = add("WACC", cc["wacc"] * 100, "percent", f"{cc['rf_id']}, {cc['erp_id']}")
    beta = add("Beta", cc["beta"], "number", "px:SPY monthly returns")
    srows, val_facts = [], [pt.id, med.id, own.id, wacc.id, beta.id]
    for name in ("bear", "base", "bull"):
        s = val["scenarios"][name]
        f_pt = add(f"{name} price target", s["price_target"], "USD",
                   f"model:{ticker}:{model.as_of}:val-{name}:price_target")
        f_m = add(f"{name} target multiple", s["target_multiple"], "x", "computed")
        f_d = add(f"{name} DCF value per share", s["dcf_pt"], "USD", "computed")
        f_w = add(f"{name} scenario weight", ctx["weights"][name], "weight", "research.yaml")
        srows.append(fact_row(name.title(), [f_pt, f_m, f_d, f_w]))
        val_facts += [f.id for f in (f_pt, f_m, f_d) if f]
    s6.blocks.append(table(["Scenario", "PT", "Multiple", "DCF / share", "Weight"], srows))
    w_m = add("multiple weight", ctx["valuation_weights"]["multiple"], "weight", "research.yaml")
    w_d = add("DCF weight", ctx["valuation_weights"]["dcf"], "weight", "research.yaml")
    s6.blocks.append(f"Method: {w_m.text()} EV/EBITDA + {w_d.text()} DCF (a DCF with a "
                     f"non-positive EV is left out); WACC {wacc.text()}, β {beta.text()}.")
    s6.blocks.append(Slot("valuation", "In two to four sentences, justify the target multiple "
                          "against the peers and say what the DCF adds. Use only these facts.",
                          val_facts + [f.id for f in prow if f]))
    sections.append(s6)

    # 7 -----------------------------------------------------------------------------------
    s7 = Section(7, TITLES[6])
    rows7 = []
    for r in ctx["sensitivities"]:
        shock = add(f"{r['what']} shock", r["shock"], "points", "research: sensitivity")
        label = f"{r['what']} {shock.text()}"
        d_e = add(f"2027E adj. EBITDA change if {label}", r["ebitda_change"],
                  "USD millions", r["id"])
        d_pt = add(f"PT change if {label}", r["pt_change"], "USD", r["pt_id"])
        rows7.append(fact_row(label, [d_e, d_pt]))
    s7.blocks.append(table(["Risk", "2027E adj. EBITDA", "PT impact"], rows7))
    s7.blocks.append("Probabilities beyond the scenario weights in section 6 are not assigned.")
    sections.append(s7)

    # 8 -----------------------------------------------------------------------------------
    s8 = Section(8, TITLES[7])
    s8.blocks.append(f"{nxt}: next earnings (estimated from the last release date; not "
                     "announced). Watch the guided lines in section 2.2.")
    for e in ctx["events"]:
        if e.get("type") in ("catalyst", "regulation", "m&a"):
            s8.blocks.append(f"- {e['summary']} ({', '.join(e['evidence'])})")
    sections.append(s8)

    # 9 -----------------------------------------------------------------------------------
    s9 = Section(9, TITLES[8])
    s9.blocks.append(f"Data as of {model.as_of}: " + "; ".join(ctx["sources"]))
    register = []
    for k, v in sorted(model.assumptions.items()):
        if k.endswith((".g0", ".shift0")) or k == "fade":
            add(f"assumption {k}", round(v, 6), "number", f"model:{ticker}:{model.as_of}:"
                      "base (assumption)")
            register.append(f"{k} = {v:.4f}" + (" (guidance)" if k in ctx["anchored"] else ""))
    s9.blocks.append("Key assumptions: " + "; ".join(register)
                     + ". Full register: workbook tab Assumptions.")
    ins = ctx.get("insiders")
    if ins and ins["transactions"]:
        sold = add(f"{ins['period']} insider open-market shares sold", ins["sold"] / 1e6,
                   "count millions", "Form 4 (insider_tx)")
        value = add(f"{ins['period']} insider open-market sales", ins["sold_value"] / 1e6,
                    "USD millions", "Form 4 (insider_tx)")
        awarded = add(f"{ins['period']} insider shares awarded", ins["awarded"] / 1e6,
                      "count millions", "Form 4 (insider_tx)")
        s9.blocks.append(f"Insider activity in {ins['period']} (Form 4, dilution context): "
                         f"{sold.text()} shares sold in the open market ({value.text()}), "
                         f"{awarded.text()} awarded.")
    issues = add("open issues", float(len(ctx["exceptions"])), "count", "exception log")
    s9.blocks.append(f"Open issues: {issues.text()}; see the exception log (X-rows).")
    buy = add("Buy threshold return", ctx["rating"]["buy"] * 100, "percent", "research.yaml")
    sell = add("Sell threshold return", ctx["rating"]["sell"] * 100, "percent", "research.yaml")
    s9.blocks.append(f"Rating definitions: Buy ≥ {buy.text()} 12-month return, Sell ≤ "
                     f"{sell.text()}, Hold between. A draft for education and testing, not "
                     "investment advice.")
    sections.append(s9)

    # 10 ----------------------------------------------------------------------------------
    s10 = Section(10, TITLES[9])
    decisions = [x["item"] for x in ctx["exceptions"] if x["severity"] in ("warning", "error")]
    s10.blocks.append("Status: DRAFT. Decisions needed:" if decisions else "Status: DRAFT.")
    s10.blocks += [f"{i}. {d}" for i, d in enumerate(decisions[:8], 1)]
    s10.blocks.append("Reviewer: ______  Approve rating ☐  Approve PT ☐  Approve estimates ☐")
    sections.append(s10)

    s1.blocks.append(Slot("takeaways", "Write four bullet takeaways (Beat, Outlook, Estimates, "
                          "Valuation), one line each, then one 'Thesis (hypothesis):' line. "
                          "Use only these facts.", takeaway_facts + val_facts[:6]))
    return sections


def markdown(sections: list[Section], header: str) -> str:
    out = [header, ""]
    for s in sections:
        out += [f"## {s.number}. {s.title.upper()}", ""]
        for b in s.blocks:
            out += [b.text if isinstance(b, Slot) else b, ""]
    return "\n".join(out)
