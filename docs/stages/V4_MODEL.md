# V4 · Model and valuation

How the model is built, forecast and valued. Rules live in the [design doc §7](../V4_TECHNICAL_DESIGN_DOC.md#7-model-engine-and-valuation); equations in [V4_FINANCIAL_MODELING.md](V4_FINANCIAL_MODELING.md).

<p align="center">
  <img src="../img/v4-model-valuation.svg" alt="Steps S3 to S7: model graph and checks, guidance-anchored forecast with a bounded proposer, peers, price targets, rating" width="100%">
</p>

## Steps

S-numbers are steps of the Report pipeline ([design doc §9.1](../V4_TECHNICAL_DESIGN_DOC.md#91-contract) lists all eleven). This doc covers the Model engine's steps; S2 (events) and S8–S11 (revise, debates, write, export) are Report-only; see [V4_REPORT.md](V4_REPORT.md).

| Step | Does |
| --- | --- |
| Data pack (S1) | Gathers every input with its source id and filing date; per-share figures on the latest split basis |
| History (S3) | Fills the company's template with reported numbers; runs integrity checks |
| Proposer (S4) | Off by default. Suggests bounded changes to starting growth, take-rate shift and fade |
| Forecast (S5) | Defaults → outside inputs → proposer → guidance anchors → scenarios; checks again |
| Peers (S6) | Builds same-sector models with defaults; cached |
| Valuation (S7) | Market cap, EV, peer multiple, DCF, PT per scenario, blended PT, rating |

## Key ideas

**One formula, two outputs.** Each model line is written once in a small formula language. Python evaluates it into `model.json`, and the same formula is written into the Excel workbook. So the workbook and the model always agree.

**Guidance anchors.** Management guides bookings, revenue or EBITDA for next quarter. For each guide in that order, a search finds the one assumption value that makes the forecast hit the midpoint. If the assumption's bounds stop it, the closest value is kept and the miss logged.

**Outside inputs** (`research/outside.py`). Public data nudges starting growth:

1. Take the series' average y/y over its latest 3 months.
2. Fit the driver's past y/y against the series (needs 6+ quarters).
3. Blend: `g0 = (1 − w) × default + w × fit`, with `w = 0.5 × R²`. A non-positive slope is ignored.

| Company driver | Outside series |
| --- | --- |
| BKNG, EXPE, ABNB volume; TRIP revenue | BTS airline passengers |
| UBER Mobility bookings | NYC TLC trips (Uber) |
| LYFT rides | NYC TLC trips (Lyft) |
| DASH orders / order value | FRED restaurant sales / CPI |
| CART order value | CPI |

## Lessons learned

| Rule | Why |
| --- | --- |
| Balance sheet includes redeemable NCI and temporary equity | UBER, DASH and CART only tie that way |
| All per-share figures use the latest split basis | BKNG's 25-for-1 split mixed bases |
| EBITDA = revenue − costs as a share of bookings | A margin on bookings made take-rate cuts have no effect |
| Missing last-year quarter → step from two years back | ABNB's 2025 bookings weren't extracted |
| Full-year-only guidance anchors the full year | ABNB guides only a full-year margin (a known spec gap) |
| Proposer skips anchored assumptions | The anchor runs after and would undo it |

## Tests

| File | Checks |
| --- | --- |
| `test_model_dsl.py` | Same result in both evaluators; missing inputs give None, not 0 |
| `test_model_graph.py` | Each integrity check passes and fails correctly; late filings caught |
| `test_model_workbook.py` | Cell colors; parity with LibreOffice |
| `test_forecast.py` | Growth fades; ratios in bounds; forecast cells are formulas; anchors hit the midpoint |
| `test_credentials_outside.py` | Outside-input fit; missing credentials fail clearly |
| `test_valuation.py` | DCF falls as WACC rises; deterministic rounding; ratings |
