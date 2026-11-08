"""The spec's example model-change workbook (docs/EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx).

  uv run python experiments/v4/example_workbook.py

Every figure is invented (spec §2.3 example). It reproduces the note's sections 2, 4, 6 and 7
with live formulas, in the tabs a real run writes (spec §2.3): Model, Assumptions, Summary,
Scenarios, Valuation, DCF, Revisions, Model (prior), Checks, Log. Blue = input, black =
formula, green = a link to another tab. The Model tab is an excerpt.
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

OUT = (Path(__file__).resolve().parents[2] / "docs"
       / "EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx")
BLUE, BLACK, GREEN = Font(color="0000CC"), Font(color="000000"), Font(color="007A33")
BOLD = Font(bold=True)
NOTE = "Illustrative: every figure is invented to show the format. Not investment advice."

wb = Workbook()
wb.remove(wb.active)


def tab(name: str, title: str, widths=(44, 14, 14, 14, 14, 14, 14, 30)):
    ws = wb.create_sheet(name)
    ws.append([title])
    ws["A1"].font = BOLD
    ws.append([NOTE])
    ws.append([])
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    return ws


def put(ws, row: list, fmt: str | None = None):
    """Append a row; colour each cell: str starting '=' with '!' green, '=' black, numbers blue."""
    ws.append(row)
    r = ws.max_row
    for c in ws[r]:
        v = c.value
        if isinstance(v, str) and v.startswith("="):
            c.font = GREEN if "!" in v else BLACK
        elif isinstance(v, (int, float)):
            c.font = BLUE
        if fmt and isinstance(v, (int, float, str)) and c.column > 1 and (
                isinstance(v, (int, float)) or str(v).startswith("=")):
            c.number_format = fmt
    return r


def header(ws, row: list):
    ws.append(row)
    for c in ws[ws.max_row]:
        c.font = BOLD


# --- Assumptions: every input the formulas read ----------------------------------------------
A = {}
asm = tab("Assumptions", "Assumptions (blue cells the formulas reference)", (52, 14, 10))
header(asm, ["Assumption", "Value", "Unit"])
for key, label, value, unit in [
    ("price", "Share price (2026-08-05 close)", 80.0, "$"),
    ("shares", "Diluted shares", 2.0625, "B"),
    ("net_debt", "Net debt", 3.0, "$B"),
    ("w_mult", "Weight: EV/EBITDA multiple", 0.6, ""),
    ("w_dcf", "Weight: DCF", 0.4, ""),
    ("w_bear", "Scenario weight: bear", 0.25, ""),
    ("w_base", "Scenario weight: base", 0.5, ""),
    ("w_bull", "Scenario weight: bull", 0.25, ""),
    ("step", "Scenario multiple step (bear/bull)", 0.15, ""),
    ("buy", "Buy threshold (12-month return)", 0.15, ""),
    ("sell", "Sell threshold (12-month return)", -0.10, ""),
    ("wacc", "WACC", 0.095, ""),
    ("tg", "Terminal growth", 0.03, ""),
    ("fade", "Annual growth fade after the modeled years", 0.9, "x"),
    ("q3_gb", "Q3 2026E gross bookings (solved to the guidance midpoint)", 51.25, "$B"),
    ("q3_tr", "Q3 2026E take rate (same quarter last year + shift)", 0.26537, ""),
    ("q3_ebitda", "Q3 2026E adj. EBITDA (solved to the guidance midpoint)", 2.25, "$B"),
    ("bear_ebitda", "Bear 2027E adj. EBITDA (scenario drivers -1σ)", 8.61, "$B"),
    ("bull_ebitda", "Bull 2027E adj. EBITDA (scenario drivers +1σ)", 11.28, "$B"),
    ("bear_dcf", "Bear DCF value per share", 76.4, "$"),
    ("bull_dcf", "Bull DCF value per share", 128.4, "$"),
    ("gb27", "2027E gross bookings", 212.7, "$B"),
    ("mob_1pt", "2027E adj. EBITDA per 1 pt of Mobility growth", 0.06, "$B"),
]:
    r = put(asm, [label, value, unit])
    A[key] = f"Assumptions!$B${r}"

# --- Model (excerpt) --------------------------------------------------------------------------
m = tab("Model", "Model excerpt: UBER Q2 2026 (Apr-Jun), $B unless noted",
        (40, 12, 12, 10, 12, 10, 12, 12, 30))
header(m, ["Headline (section 2.1)", "Q2 2026A", "Q2 2025A", "Y/Y", "Our est.", "Var.",
           "Guide mid", "Var. vs mid", "Source"])
M = {}
for key, label, a, y, est, mid in [
    ("gb", "Gross bookings", 48.1, 40.4, 47.5, 47.0),
    ("rev", "Revenue", 12.9, 10.996, 12.6, None),
    ("ebitda", "Adjusted EBITDA", 2.08, 1.60, 2.0, 2.0),
    ("trips", "Trips (B)", 3.4, 2.93, 3.3, None),
]:
    r = m.max_row + 1
    put(m, [label, a, y, f"=B{r}/C{r}-1", est, f"=B{r}/E{r}-1",
            mid if mid is not None else "n/a",
            f"=B{r}/G{r}-1" if mid is not None else "n/a", "8-K Ex. 99.1 (Log E-01 to E-04)"])
    M[key] = r
    for col in "DFH":
        m[f"{col}{r}"].number_format = "+0.0%;-0.0%"
m.append([])
header(m, ["Quality of the quarter (section 2.3)", "Q2 2026", "Q2 2025", "Change"])
r = m.max_row + 1
put(m, ["Bookings per trip ($)", f"=B{M['gb']}/B{M['trips']}", f"=C{M['gb']}/C{M['trips']}",
        f"=B{r}/C{r}-1"])
r = m.max_row + 1
put(m, ["Take rate", f"=B{M['rev']}/B{M['gb']}", f"=C{M['rev']}/C{M['gb']}",
        f"=(B{r}-C{r})*10000"])
m[f"D{r}"].number_format = '0" bps"'
M["tr"] = r
r = m.max_row + 1
put(m, ["Free cash flow", 2.3, 1.7, f"=B{r}/C{r}-1"])
M["fcf"] = r
m.append([])
header(m, ["Segment gross bookings", "Q2 2026A", "Q2 2025A", "Y/Y"])
seg0 = m.max_row + 1
for label, a, y in [("Mobility", 24.5, 20.9), ("Delivery", 21.8, 18.0), ("Freight", 1.8, 1.5)]:
    r = m.max_row + 1
    put(m, [label, a, y, f"=B{r}/C{r}-1"])
r = m.max_row + 1
put(m, ["Total (sum of segments)", f"=SUM(B{seg0}:B{r - 1})", f"=SUM(C{seg0}:C{r - 1})",
        f"=B{r}/C{r}-1"])
M["seg_total"] = r
m.append([])
header(m, ["Guidance (section 2.2)", "Low", "High", "Midpoint", "Our forecast", "Var. vs mid"])
r = m.max_row + 1
put(m, ["Q3 gross bookings", 50.5, 52.0, f"=AVERAGE(B{r}:C{r})", f"={A['q3_gb']}",
        f"=E{r}/D{r}-1"])
M["g_gb"] = r
r = m.max_row + 1
put(m, ["Q3 adj. EBITDA", 2.2, 2.3, f"=AVERAGE(B{r}:C{r})", f"={A['q3_ebitda']}",
        f"=E{r}/D{r}-1"])
M["g_ebitda"] = r
m.append([])
header(m, ["Q3 2026E forecast", "Value"])
r = m.max_row + 1
put(m, ["Gross bookings", f"={A['q3_gb']}"])
put(m, ["Take rate", f"={A['q3_tr']}"])
put(m, ["Revenue (bookings x take rate)", f"=B{r}*B{r + 1}"])
M["q3_rev"] = r + 2
put(m, ["Adjusted EBITDA", f"={A['q3_ebitda']}"])
M["q3_ebitda"] = r + 3
m.append([])
header(m, ["Annual (section 5)", "2024A", "2025A", "2026E", "2027E"])
r = m.max_row + 1
put(m, ["Revenue", 44.0, 49.0, 51.0, 57.0])
M["rev_y"] = r
put(m, ["Revenue growth", 0.18, f"=C{r}/B{r}-1", f"=D{r}/C{r}-1", f"=E{r}/D{r}-1"])
put(m, ["Adjusted EBITDA", 6.5, 8.0, 8.9, 9.7])
M["ebitda_y"] = r + 2
put(m, ["SBC", None, None, 0.8, 0.5])
put(m, ["Cash taxes", None, None, 0.3, 0.17])
put(m, ["Capex", None, None, 0.2, 0.1])
M["sbc"], M["tax"], M["capex"] = r + 3, r + 4, r + 5

# --- Model (prior): the parent version's values -------------------------------------------------
p = tab("Model (prior)", "Model (prior): UBER:v1 as saved (values)", (40, 14))
header(p, ["Line", "Value"])
P = {}
for key, label, v in [("q3_rev", "Q3 2026E revenue", 13.4),
                      ("q3_ebitda", "Q3 2026E adj. EBITDA", 2.20),
                      ("rev26", "2026E revenue", 50.1), ("ebitda26", "2026E adj. EBITDA", 8.6),
                      ("ebitda27", "2027E adj. EBITDA", 9.4), ("rating", "Rating", "Buy"),
                      ("pt", "12-month price target", 95.0)]:
    P[key] = f"'Model (prior)'!$B${put(p, [label, v])}"

# --- Revisions (section 4.1) ---------------------------------------------------------------------
rv = tab("Revisions", "Estimate revisions: old vs. new, $B (old value is the denominator)",
         (30, 10, 10, 12, 12, 24, 12))
header(rv, ["Line", "Old", "New", "Change ($B)", "Change (%)", "Source", "Confidence"])
for label, old, new, conf in [
    ("Q3 2026E revenue", P["q3_rev"], f"Model!B{M['q3_rev']}", "Medium"),
    ("Q3 2026E adj. EBITDA", P["q3_ebitda"], f"Model!B{M['q3_ebitda']}", "Medium"),
    ("2026E revenue", P["rev26"], f"Model!D{M['rev_y']}", "Medium"),
    ("2026E adj. EBITDA", P["ebitda26"], f"Model!D{M['ebitda_y']}", "Medium"),
    ("2027E adj. EBITDA", P["ebitda27"], f"Model!E{M['ebitda_y']}", "Low"),
]:
    r = rv.max_row + 1
    put(rv, [label, f"={old}", f"={new}", f"=C{r}-B{r}", f"=C{r}/B{r}-1",
             "Reported + assumption", conf])
    rv[f"E{r}"].number_format = "+0.0%;-0.0%"

# --- DCF ----------------------------------------------------------------------------------------
d = tab("DCF", "DCF, base case ($B): 10 years, the modeled years then faded; Gordon terminal",
        (34, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10))
header(d, ["Year", *[f"{y}E" for y in range(2026, 2036)]])
r_fcf = d.max_row + 1
row = ["Unlevered FCF (EBITDA - SBC - tax - capex)",
       *[f"=Model!{c}{M['ebitda_y']}-Model!{c}{M['sbc']}-Model!{c}{M['tax']}"
         f"-Model!{c}{M['capex']}" for c in "DE"]]
cols = "BCDEFGHIJK"
for i in range(2, 10):  # faded years: growth = previous growth x fade
    c, p1, p2 = cols[i], cols[i - 1], cols[i - 2]
    row.append(f"={p1}{r_fcf}*(1+({p1}{r_fcf}/{p2}{r_fcf}-1)*{A['fade']})")
put(d, row, "0.00")
r_df = put(d, ["Discount factor", *[f"=1/(1+{A['wacc']})^{i + 1}" for i in range(10)]], "0.000")
r_pv = put(d, ["Present value", *[f"={c}{r_fcf}*{c}{r_df}" for c in cols]], "0.00")
d.append([])
r_tv = put(d, ["Terminal value (PV)",
               f"=K{r_fcf}*(1+{A['tg']})/({A['wacc']}-{A['tg']})*K{r_df}"], "0.00")
r_ev = put(d, ["Enterprise value", f"=SUM(B{r_pv}:K{r_pv})+B{r_tv}"], "0.00")
put(d, ["Terminal value share of EV", f"=B{r_tv}/B{r_ev}"], "0%")
r_dps = put(d, ["DCF value per share ($)", f"=(B{r_ev}-{A['net_debt']})/{A['shares']}"], "0.00")
d.append([])
header(d, ["Per share: WACC (rows) x terminal growth (columns)", 0.025, 0.03, 0.035])
for wacc in (0.085, 0.095, 0.105):
    r = d.max_row + 1
    cells = []
    for c in "BCD":
        g = f"{c}${r_dps + 2}"
        pv = "+".join(f"{cc}${r_fcf}/(1+$A{r})^{i + 1}" for i, cc in enumerate(cols))
        cells.append(f"=(({pv})+K${r_fcf}*(1+{g})/($A{r}-{g})/(1+$A{r})^10"
                     f"-{A['net_debt']})/{A['shares']}")
    put(d, [wacc, *cells], "0")
    d[f"A{r}"].number_format = "0.0%"

# --- Valuation (section 6) ----------------------------------------------------------------------
v = tab("Valuation", "Valuation: UBER (2027E EV / adj. EBITDA)", (40, 12, 12, 12, 12))
header(v, ["Peers", "2027E EV/EBITDA"])
peer0 = v.max_row + 1
for t, x in [("LYFT", 13.0), ("CART", 19.0), ("DASH", 20.0)]:
    put(v, [t, x])
r_med = put(v, ["Peer median (target multiple)", f"=MEDIAN(B{peer0}:B{peer0 + 2})"])
r_mcap = put(v, ["Market cap", f"={A['price']}*{A['shares']}"])
r_ev = put(v, ["Enterprise value (market cap + net debt)", f"=B{r_mcap}+{A['net_debt']}"])
put(v, ["UBER now: EV / 2027E EBITDA", f"=B{r_ev}/Model!E{M['ebitda_y']}"], "0.0")
v.append([])
header(v, ["Scenario", "Multiple", "Multiple / share", "DCF / share", "PT", "Weight"])
S = {}
for name, k, ebitda, dcf, w in [
    ("Bear", -1, A["bear_ebitda"], A["bear_dcf"], A["w_bear"]),
    ("Base", 0, f"Model!E{M['ebitda_y']}", f"DCF!B{r_dps}", A["w_base"]),
    ("Bull", 1, A["bull_ebitda"], A["bull_dcf"], A["w_bull"]),
]:
    r = v.max_row + 1
    put(v, [name, f"=B{r_med}*(1+{k}*{A['step']})",
             f"=(B{r}*{ebitda}-{A['net_debt']})/{A['shares']}", f"={dcf}",
             f"={A['w_mult']}*C{r}+{A['w_dcf']}*D{r}", f"={w}"], "0.00")
    S[name] = r
r_pt = put(v, ["Probability-weighted PT", None, None, None,
               f"=SUMPRODUCT(E{S['Bear']}:E{S['Bull']},F{S['Bear']}:F{S['Bull']})"], "0.00")
r_ret = put(v, ["Return vs price", None, None, None, f"=E{r_pt}/{A['price']}-1"], "+0.0%")
r_rat = put(v, ["Rating", None, None, None,
                f'=IF(E{r_ret}>={A["buy"]},"Buy",IF(E{r_ret}<={A["sell"]},"Sell","Hold"))'])

# --- Scenarios and sensitivities (sections 6 and 7) --------------------------------------------
sc = tab("Scenarios", "Scenarios: 2027E adj. EBITDA by case; one-at-a-time sensitivities",
         (40, 14, 16, 14))
header(sc, ["Scenario", "2027E adj. EBITDA", "PT", "Weight"])
for name, ebitda in [("Bear", f"={A['bear_ebitda']}"), ("Base", f"=Model!E{M['ebitda_y']}"),
                     ("Bull", f"={A['bull_ebitda']}")]:
    put(sc, [name, ebitda, f"=Valuation!E{S[name]}", f"=Valuation!F{S[name]}"], "0.00")
sc.append([])
header(sc, ["Sensitivity (section 7)", "2027E adj. EBITDA ($B)", "PT impact ($)"])
base_e = f"Model!E{M['ebitda_y']}"
for label, change in [("Take rate -50 bps", f"=-0.005*{A['gb27']}"),
                      ("Mobility growth -1 pt", f"=-{A['mob_1pt']}"),
                      ("Cost share +25 bps", f"=-0.0025*{A['gb27']}")]:
    r = sc.max_row + 1
    put(sc, [label, change,
             f"={A['w_mult']}*B{r}*Valuation!B{r_med}/{A['shares']}"
             f"+{A['w_dcf']}*DCF!B{r_dps}*B{r}/{base_e}"], "0.00")

# --- Summary --------------------------------------------------------------------------------------
su = tab("Summary", "Summary: UBER, as of 2026-08-06 (DRAFT for senior review)", (36, 16))
put(su, ["Draft rating", f"=Valuation!E{r_rat}"])
put(su, ["Price ($)", f"={A['price']}"], "0.00")
put(su, ["12-month price target ($)", f"=Valuation!E{r_pt}"], "0.00")
put(su, ["Return", f"=Valuation!E{r_ret}"], "+0.0%")
put(su, ["Market cap ($B)", f"=Valuation!B{r_mcap}"], "0.0")
put(su, ["Enterprise value ($B)", f"=Valuation!B{r_ev}"], "0.0")
put(su, ["Net debt ($B)", f"={A['net_debt']}"], "0.0")
put(su, ["Diluted shares (B)", f"={A['shares']}"], "0.0000")
put(su, ["WACC", f"={A['wacc']}"], "0.0%")
put(su, ["Parity (formula cells matching the model)", "750/750"])
put(su, ["Rating / PT change vs. the prior version (v1)",
         f'=IF(AND(B4={P["rating"]},ROUND(B6,0)={P["pt"]}),"none","changed")'])

# --- Checks ---------------------------------------------------------------------------------------
ch = tab("Checks", "Checks (layer 2): any FAIL blocks the note", (34, 10, 50))
header(ch, ["Check", "Status", "Test"])
for name, test, desc in [
    ("balance", '"PASS"', "Assets = liabilities + equity (full model)"),
    ("quarters_sum", '"PASS"', "Q1-Q4 sum to FY (full model)"),
    ("q4_ties", '"PASS"', "Derived Q4 + 9M = the 10-K's FY (full model)"),
    ("take_rate", f'IF(ABS(Model!B{M["tr"]}-Model!B{M["rev"]}/Model!B{M["gb"]})<0.0001,'
                  '"PASS","FAIL")',
     "Take rate recomputes from revenue / gross bookings"),
    ("segments_sum", f'IF(ABS(Model!B{M["seg_total"]}-Model!B{M["gb"]})<0.05,"PASS","FAIL")',
     "Segment bookings sum to the total"),
    ("no_future_input", '"PASS"', "Every input filed before 2026-08-06"),
    ("formula_only", '"PASS"', "Forecast cells are formulas"),
    ("wacc_gt_g", f'IF({A["wacc"]}>{A["tg"]},"PASS","FAIL")', "WACC > terminal growth"),
    ("weights", f'IF(AND(ABS({A["w_mult"]}+{A["w_dcf"]}-1)<1E-9,'
                f'ABS({A["w_bear"]}+{A["w_base"]}+{A["w_bull"]}-1)<1E-9),"PASS","FAIL")',
     "Valuation and scenario weights each sum to 1"),
]:
    put(ch, [name, f"={test}", desc])

# --- Log ------------------------------------------------------------------------------------------
lg = tab("Log", "Evidence and exception log", (8, 40, 12, 40))
header(lg, ["ID", "Figure", "Value", "Source id"])
for i, (fig, val, src) in enumerate([
    ("Q2 2026 gross bookings", f"=Model!B{M['gb']}", "kpi:UBER:gross_bookings:2026Q2"),
    ("Q2 2026 revenue", f"=Model!B{M['rev']}", "q:UBER:revenue:2026Q2"),
    ("Q2 2026 adj. EBITDA", f"=Model!B{M['ebitda']}", "kpi:UBER:adj_ebitda:2026Q2"),
    ("Q2 2026 trips", f"=Model!B{M['trips']}", "kpi:UBER:trips:2026Q2"),
    ("Q3 2026 gross bookings guidance midpoint", f"=Model!D{M['g_gb']}",
     "guide:UBER:gross_bookings:2026Q3"),
    ("Q3 2026 adj. EBITDA guidance midpoint", f"=Model!D{M['g_ebitda']}",
     "guide:UBER:adj_ebitda:2026Q3"),
], 1):
    put(lg, [f"E-{i:02d}", fig, val, src])
lg.append([])
header(lg, ["ID", "Severity", "Item"])
for row in [("X-01", "warning", "Section 3.1: a sentence with an untraced number was dropped"),
            ("X-02", "info", "Freight adj. EBITDA missing in 2023Q4; left blank"),
            ("X-03", "info", "Working capital held at 0 in the forecast")]:
    put(lg, list(row))

wb.move_sheet("Summary", offset=-wb.sheetnames.index("Summary"))
order = ["Summary", "Model", "Assumptions", "Scenarios", "Valuation", "DCF", "Revisions",
         "Model (prior)", "Checks", "Log"]
wb._sheets = [wb[n] for n in order]
wb.save(OUT)
print(OUT)
