"""S11 export (V4 design doc §11): the report's workbook and the note's PDF.

The workbook is the Model tab and Assumptions tab (research/export/workbook.py, checked for
parity with LibreOffice) plus Summary, Scenarios, Valuation, DCF, Revisions, Model (prior),
Checks and Log (the evidence and exception rows), the tabs of spec §2.3. The PDF is the note's
Markdown printed with headless Chrome.

DCF (spec §2.3): the base case's unlevered free cash flows (blue values from the model) with
live formulas for each discount factor, present value, the terminal value, EV, the terminal
value's share of EV and the value per share, so a changed WACC or growth recalculates; then the
price-per-share grid over WACC × terminal growth (values: each grid point refades the annual
years toward its growth, research/valuation.py). Model (prior): the parent version's lines and
values as it saved them, or "initial draft (no prior run)".
"""

import html
import re
from pathlib import Path

from openpyxl import load_workbook

from era_agent.research.export import workbook as model_tab
from era_agent.research.forecast import scenario_summary, scenarios


def workbook(folder: Path, model, f, valuation: dict, checks, facts, exceptions, revisions,
             settings, prior: dict | None = None, prior_id: str | None = None
             ) -> tuple[Path, str]:
    path = model_tab.write(model, folder / "workbook.xlsx")
    checked, differ = model_tab.parity(model, path)
    book = load_workbook(path)

    def sheet(title: str, header: list[str], rows: list[list]):
        ws = book.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font = model_tab.HEADER
        for row in rows:
            ws.append(row)
        ws.column_dimensions["A"].width = 40

    v = valuation
    sheet("Summary", ["item", "value"], [
        ["rating (draft)", v["rating"]], ["price", v["price"]], ["price date", v["price_date"]],
        ["12-month price target", v["price_target"]], ["return", v["return"]],
        ["market cap (USD millions)", v["market_cap"]], ["EV (USD millions)", v["ev"]],
        ["net debt (USD millions)", v["net_debt"]], ["diluted shares (millions)", v["shares"]],
        ["WACC", v["cost_of_capital"]["wacc"]], ["parity", f"{checked - len(differ)}/{checked}"]])
    summary = scenario_summary(scenarios(f, settings), settings["scenarios"]["weights"])
    rows = [[f"{name} {line}", year, value] for name, lines in summary.items()
            for line, years in lines.items() for year, value in years.items()]
    sheet("Scenarios", ["scenario and line", "year", "value"], rows)
    sheet("Valuation", ["item", "value"], [
        *[[f"peer {v['target_year']}E EV/EBITDA {t}", m]
          for t, m in v["peer_multiples"]["peers"].items()],
        ["peer median", v["peer_multiples"]["median"]],
        *[[f"{k}", x] for k, x in v["cost_of_capital"].items()],
        *[[f"{n} {k}", x] for n, s in v["scenarios"].items() for k, x in s.items()],
        *[[f"DCF per share, WACC|g {k}", x] for k, x in v["dcf_grid"].items()]])
    dcf_tab(book, valuation)
    prior_tab(book, prior, prior_id)
    sheet("Revisions", ["line", "old", "new", "source", "confidence"],
          [[r["label"], r["old"], r["new"], r["source"], r["confidence"]] for r in revisions])
    sheet("Checks", ["check", "status", "tested", "failures", "skipped"],
          [[c.name, c.status, c.tested, "; ".join(c.failures), len(c.skipped)] for c in checks])
    sheet("Log", ["id", "kind", "label or severity", "value or item", "source"],
          [[f"E-{x.id[2:]}", "evidence", x.label, x.text(), x.source] for x in facts.items]
          + [[x["id"], "exception", x["severity"], x["item"], ""] for x in exceptions])
    book.save(path)
    return path, f"{checked - len(differ)}/{checked}"


def dcf_tab(book, v: dict) -> None:
    from openpyxl.comments import Comment

    ws = book.create_sheet("DCF")
    d = v["dcf_base"]
    blue, bold = model_tab.BLUE, model_tab.HEADER
    ws.column_dimensions["A"].width = 34
    inputs = [("WACC", d["wacc"], "0.00%"), ("terminal growth", d["terminal_growth"], "0.00%"),
              ("net debt (USD millions)", v["net_debt"], "#,##0"),
              ("diluted shares (millions)", v["shares"], "#,##0.0"),
              ("terminal year (years from the as-of)", d["terminal_t"], "0.000"),
              ("last annual UFCF (USD millions)", d["last_annual"], "#,##0")]
    ws.append(["Base-case DCF as of " + v["as_of"]])
    ws["A1"].font = bold
    for i, (label, value, fmt) in enumerate(inputs, start=2):
        ws.cell(i, 1, label)
        c = ws.cell(i, 2, value)
        c.font, c.number_format = blue, fmt
    wacc, g, debt, shares, t_last, last = (f"$B${i}" for i in range(2, 8))
    head = 9
    for col, text in enumerate(["period", "years from as-of (mid-period)", "UFCF (USD millions)",
                                "discount factor", "present value"], start=1):
        ws.cell(head, col, text).font = bold
    first = head + 1
    for i, (period, t, cf) in enumerate(d["flows"], start=first):
        ws.cell(i, 1, period)
        ws.cell(i, 2, t).font = blue
        c = ws.cell(i, 3, cf)
        c.font, c.number_format = blue, "#,##0"
        if i == first:
            c.comment = Comment("from the model: adj. EBITDA - SBC - tax on EBIT - capex "
                                "(quarters); annual years grow toward the terminal growth",
                                "model")
        ws.cell(i, 4, f"=1/(1+{wacc})^B{i}").number_format = "0.0000"
        ws.cell(i, 5, f"=C{i}*D{i}").number_format = "#,##0"
    end = first + len(d["flows"]) - 1
    rows = [("explicit-period PV", f"=SUM(E{first}:E{end})", "#,##0"),
            ("terminal value (PV)", f"={last}*(1+{g})/({wacc}-{g})/(1+{wacc})^{t_last}", "#,##0"),
            ("EV (USD millions)", "=B{0}+B{1}", "#,##0"),
            ("terminal value share of EV", "=B{1}/B{2}", "0.0%"),
            ("DCF value per share (USD)", f"=(B{{2}}-{debt})/{shares}", "$#,##0.00")]
    top = end + 2
    for i, (label, formula, fmt) in enumerate(rows):
        ws.cell(top + i, 1, label).font = bold if i >= 2 else None
        cell = ws.cell(top + i, 2, formula.format(top, top + 1, top + 2))
        cell.number_format = fmt
    grid_top = top + len(rows) + 1
    ws.cell(grid_top, 1, "Price per share: WACC (rows) × terminal growth (columns)").font = bold
    waccs = sorted({float(k.split("|")[0]) for k in v["dcf_grid"]})
    growths = sorted({float(k.split("|")[1]) for k in v["dcf_grid"]})
    for j, gr in enumerate(growths, start=2):
        ws.cell(grid_top + 1, j, gr).number_format = "0.0%"
    for i, w in enumerate(waccs, start=grid_top + 2):
        ws.cell(i, 1, w).number_format = "0.00%"
        for j, gr in enumerate(growths, start=2):
            c = ws.cell(i, j, v["dcf_grid"][f"{w:.4f}|{gr:.4f}"])
            c.number_format = "$#,##0.00"


def prior_tab(book, prior: dict | None, prior_id: str | None) -> None:
    ws = book.create_sheet("Model (prior)")
    if not prior:
        ws.append(["initial draft (no prior run)"])
        return
    columns = prior["columns"]
    ws.append([f"{prior_id} as of {prior['as_of']} (values as saved; that version's workbook "
               "holds its formulas)"])
    ws.append(["line", "unit", *columns])
    for c in ws[2]:
        c.font = model_tab.HEADER
    ws.column_dimensions["A"].width = 44
    for line in prior["lines"]:
        ws.append([line.get("label") or line["name"], line["unit"],
                   *[(prior["cells"].get(f"{line['name']}|{c}") or {}).get("value")
                     for c in columns]])


def pdf(markdown_path: Path, out: Path) -> Path:
    from era_agent.evaluation.render import _print_pdf

    _print_pdf(to_html(markdown_path.read_text(), markdown_path.stem), out)
    return out


STYLE = """@page { size: A4; margin: 14mm 12mm; }
body { font: 9.5pt/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a1a; }
h1 { font-size: 15pt; }
h2 { font-size: 11.5pt; margin: 5mm 0 1.5mm; border-bottom: 1px solid #999; }
table { border-collapse: collapse; margin: 1mm 0 2mm; } td, th { padding: .6mm 2.5mm;
border-bottom: 1px solid #ddd; text-align: right; }
td:first-child, th:first-child { text-align: left; }
th { background: #eef0f5; } p { margin: 1mm 0; }"""


def to_html(markdown: str, title: str) -> str:
    """The note's small Markdown subset: headings, tables, bullet lines and paragraphs."""
    out, table = [], []

    def flush():
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|(---\|)+", r)]
            cells = [[html.escape(c.strip()) for c in r.strip("|").split("|")] for r in rows]
            out.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cells[0]) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                                 for r in cells[1:]) + "</table>")
            table.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    flush()
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{STYLE}</style>{''.join(out)}")
