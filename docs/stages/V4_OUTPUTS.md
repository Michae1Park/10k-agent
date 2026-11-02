# V4 · Outputs and review: how they work

Companion to [design doc §11](../V4_TECHNICAL_DESIGN_DOC.md#11-outputs-and-review), which holds the contract (report folder, workbook tabs, review, parity). This doc does not restate the rules; where the two disagree, the design doc wins.

## Export

`research/export/workbook.py` writes the Model tab from the same DSL nodes Python evaluated (inputs blue with provenance, formulas black, assumption links green) and the Assumptions tab. `research/export/report.py` adds the other tabs and prints the note: Markdown → HTML → PDF with headless Chrome (`evaluation/render.py`).

**Parity.** The workbook is recalculated by LibreOffice (`soffice --headless`, up to 300 s); every Model formula cell is compared with `model.json` at 1e-9 relative, and the count goes to `report.json` `parity`.

**Review.** `tenk research approve TICKER:vN --by INITIALS` sets the version's status; `research_report` and `model:…:v<n>` ids show it.

## Tests

`test_model_workbook.py` (colors, parity), `test_export_tabs.py` (the DCF tab recalculates in LibreOffice to `dcf()`'s EV, terminal share and per-share value; Model (prior) without and with a parent), `test_forecast.py::forecast_parity_with_libreoffice`.
