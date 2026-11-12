# V4 · Outputs and review

How the note, workbook and review status are produced. Rules live in the [design doc §11](../V4_TECHNICAL_DESIGN_DOC.md#11-outputs-and-review).

| Output | How |
| --- | --- |
| Workbook | `research/export/workbook.py` writes the Model and Assumptions tabs from the same formulas Python ran. Inputs blue (with source), formulas black, assumption links green |
| Other tabs, note | `research/export/report.py`; note goes Markdown → HTML → PDF via headless Chrome |
| Parity check | LibreOffice recalculates the workbook; every formula cell must match `model.json` (1e-9 relative). The count goes in `report.json` |
| Review | `era research approve TICKER:vN --by INITIALS` sets the version's status, shown wherever it is cited |

**Tests:** `test_model_workbook.py` (colors, parity), `test_export_tabs.py` (DCF tab recalculates to the Python result), `test_forecast.py::forecast_parity_with_libreoffice`.
