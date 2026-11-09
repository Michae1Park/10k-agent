"""The public example report: a redacted copy of a local UBER run (docs/examples/).

  uv run python experiments/v4/redact_example.py [output/reports/UBER/2026-08-06]

Prices (Tiingo), macro (FRED) and ERP (Damodaran) data are licensed or third-party, so every
value derived from them reads "local only": rating, price, PT, return, market cap, EV, 52-week
range, multiples, WACC, beta, DCF values and PT impacts. Model values (KPIs, estimates, cash
flows, net debt, shares) stay. Writes the note (Markdown and PDF) and the workbook;
tests/test_public_repo.py checks the note.
"""

import re
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "examples"
LOCAL = "local only"
BANNER = ('> **Public copy, redacted.** Prices, macro and ERP data are licensed or third-party '
          'data kept local, so every value that comes from them (rating, price, PT, return, market '
          'cap, EV, 52-week range, multiples, WACC, beta, DCF values, PT impacts) reads "local '
          'only". Run `tenk setup` with your own keys, then `tenk research report {t} --as-of '
          '{d}`, to see the full note.')
MARKET_SOURCE = re.compile(r"^(?:px|macro:|erp:)|val-(?:base|bear|bull|scratch)|computed: "
                           r"(?:price|market cap)|px:SPY")
MARKET_LABEL = re.compile(r"EV/EBITDA|WACC|^Beta$|target multiple|DCF|price target|PT change|"
                          r"Implied return|Draft rating|Market cap|Enterprise value|52-week|"
                          r"^Price$",
                          re.IGNORECASE)


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def table_row(values: list[str]) -> str:
    return "| " + " | ".join(values) + " |"


def redact_note(text: str, ticker: str, as_of: str) -> str:
    lines = text.splitlines()
    out, section = [], 0
    for i, line in enumerate(lines):
        m = re.match(r"^## (\d+)\.", line)
        if m:
            section = int(m.group(1))
        elif line.startswith("## EVIDENCE LOG"):
            section = 100
        elif line.startswith("## EXCEPTION LOG"):
            section = 101
        if i == 3:  # after the header block
            out += [line, BANNER.format(t=ticker, d=as_of)]
            continue
        if section == 1 and line.startswith("|") and not line.startswith(("| Draft", "|---")):
            c = cells(line)
            line = table_row([LOCAL] * (len(c) - 1) + [c[-1]])  # keep the next earnings date
        elif section == 1 and re.match(r"^[•-] Estimates:", line):
            line = line[0] + " Estimates: rating, price target and return are local only."
        elif section == 1 and re.match(r"^[•-] Valuation:", line):
            line = line[0] + " Valuation: multiples, WACC and beta are local only."
        elif section == 6:
            if line.startswith("Comps"):
                line = re.sub(r"(\b[A-Z]{3,5}|median|now) \d[\d.,]*x", rf"\1 {LOCAL}", line)
            elif line.startswith("|") and not line.startswith(("| Scenario", "|---")):
                c = cells(line)
                line = table_row([c[0]] + [LOCAL] * (len(c) - 2) + [c[-1]])  # keep the weight
            elif line.startswith("Method:"):
                line = re.sub(r"; WACC .*$", "; WACC and β local only.", line)
            elif line.startswith("Probability-weighted"):
                line = "Probability-weighted price target, return and rating: local only."
            elif line.strip() and not line.startswith(("#", "|")):  # the LLM justification
                line = "Target multiple justification and DCF comparison: local only."
        elif section == 7 and line.startswith("|") and not line.startswith(("| Risk", "|---")):
            c = cells(line)
            line = table_row(c[:-1] + [LOCAL])  # the PT impact
        elif section == 100 and re.match(r"^\| E-\d+ \|", line):
            c = cells(line)
            if MARKET_SOURCE.search(c[3]) or MARKET_LABEL.search(c[1]):
                c[2] = LOCAL
                line = table_row(c)
        elif section == 101 and "multiple" in line:
            line = re.sub(r"\d[\d.,]*x\b", LOCAL, line)
        out.append(line)
    return "\n".join(out) + "\n"


def redact_workbook(src: Path, dst: Path) -> None:
    wb = load_workbook(src)
    s = wb["Summary"]
    for row in s.iter_rows(min_row=2):
        if str(row[0].value) in ("rating (draft)", "price", "12-month price target", "return",
                                 "market cap (USD millions)", "EV (USD millions)", "WACC"):
            row[1].value = LOCAL
    s.insert_rows(1)
    s["A1"] = ("Public copy, redacted: prices, macro and ERP data and every value derived from "
               "them are local only (run tenk setup and tenk research report).")
    v = wb["Valuation"]
    for row in v.iter_rows(min_row=2):
        label = str(row[0].value or "")
        if row[1].value is not None and not label.endswith(("adj_ebitda", "_id", "dcf_used")):
            row[1].value = LOCAL
        if label.startswith("DCF per share, WACC|g "):  # the label carries the WACC itself
            row[0].value = re.sub(r"WACC\|g [\d.]+\|", f"WACC|g {LOCAL}|", label)
    d = wb["DCF"]
    for row in d.iter_rows():
        grid = isinstance(row[0].value, int | float)  # a WACC row of the price grid
        for c in row:
            formula = isinstance(c.value, str) and c.value.startswith("=")
            if formula or grid or (c.row == 2 and c.column == 2):  # B2 is the WACC
                c.value = LOCAL
    log = wb["Log"]
    for row in log.iter_rows(min_row=2):
        kind = str(row[1].value or "")
        if kind == "evidence" and (MARKET_SOURCE.search(str(row[4].value or ""))
                                   or MARKET_LABEL.search(str(row[2].value or ""))):
            row[3].value = LOCAL
        elif kind == "exception" and "multiple" in str(row[3].value or ""):
            row[3].value = re.sub(r"\d[\d.,]*x\b", LOCAL, str(row[3].value))
    wb.save(dst)


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "output/reports/UBER/2026-08-06")
    ticker, as_of = folder.parent.name, folder.name
    stem = OUT / f"{ticker}-{as_of}"
    note = redact_note((folder / "note.md").read_text(), ticker, as_of)
    Path(f"{stem}-note.md").write_text(note)
    from tenk_agent.research.export.report import pdf

    pdf(Path(f"{stem}-note.md"), Path(f"{stem}-note.pdf"))
    redact_workbook(folder / "workbook.xlsx", Path(f"{stem}-workbook.xlsx"))
    print(stem)


if __name__ == "__main__":
    main()
