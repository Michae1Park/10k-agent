# TODO: make the build and design doc match the spec (temporary)

The spec is fixed; the build and design doc change to match it. Delete this file once done. Each finished item also comes out of V4_RESULTS "Gaps against the spec".

## Quick (≤ 30 min)

- [ ] **#24** Note says "initial draft (no prior version)"; spec says "(no prior run)" (`note/sections.py:99`, `:206`); regenerate the example note
- [ ] **#20** `tenk research approve` on an unknown version should error (`store.py:522`)
- [ ] **#7** Log a thin-peer-set exception (X-04) when there are fewer than 4 peers (`valuation.py`)
- [ ] **#18** Parity mismatch stops the run (D-041)
- [ ] **#2** Consensus "n/a" column in table 2.1, plus a "no consensus" exception (X-03) (`note/sections.py:131`)
- [ ] **#22** Show EV/(EBITDA − SBC), P/E and FCF yield in note §6 and the Valuation tab (spec §3.3; `valuation.py:263`)

## Small (1–2 h)

- [ ] **#1** Variance flag: >2% revenue or >5% adj. EBITDA vs. guide midpoint needs a reason or an exception; Flag column in 2.2 (spec §3.2; `note/sections.py:150`)
- [ ] **#4** Exception fields: treatment, status (open/resolved) (`export/report.py:67`)
- [ ] **#9** Carry open exceptions forward from the parent version (after #4)
- [ ] **#10** Exception when a non-operating item exceeds 10% of NI in any of the last 4 quarters
- [ ] **#5** Summary tab = old vs. new, $ and % change, source, type, confidence; EV-to-equity bridge, blended PT and rating move to Valuation; drop the Revisions tab (spec §2.3; `export/report.py:42,61`)
- [ ] **#19** Revisions: separate source of change and type (recurring/mixed/timing), every changed line (`pipeline.py:336`); with #5
- [ ] **#6** Scenarios tab: weights, probability-weighted value, one-at-a-time sensitivities (`export/report.py:49`)
- [ ] **#11** Assumptions tab: basis, confidence, variables affected, last updated (`export/workbook.py:87`)
- [ ] **#12** S9 debates use the Research toolset (spec §2.3 req. 2; `research/subagents.py`); rerun to check
- [ ] **#23** Section 7: model driver and evidence columns; unquantified risks from risk-factor text (`note/sections.py:273`)
- [ ] **#17** Section 10 gets one repair turn on a failed consistency check, like Section 1 (spec §2.3 req. 6)
- [ ] **#27** Model (prior) tab keeps the prior version with live formulas, not values (spec §2.3 Model tab; V4_RESULTS gaps)
- [ ] **#33** Section 3.1 driver table: driver, latest evidence, model variable (spec §2.3 example; `note/sections.py`)
- [ ] **#29** Later quarters from the seasonal share only (spec §3.2); drop the FY-column anchor for full-year-only guidance (ABNB; V4_RESULTS gaps)

## Medium (2–4 h)

- [ ] **#30** Update `EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx` to the revised spec example: adj. EBITDA 2.08 (+4.0%, +30% y/y), Corporate −0.24, DCF $103, base $94, bull $122 (cost share −25 bps), sensitivities take rate −$1.1B / about −$10 and cost share +25 bps
- [ ] **#31** Relative rating 3/2/3 (`valuation.py` `rank`); variance flag also on gross bookings and on an anchor override (with #1); outside data shown as directional checks in the note

- [ ] **#3** Evidence log fields: period, filing date, page or table, retrieval date, type, workbook cell (`export/report.py:65`)
- [ ] **#21** Compare release KPIs with XBRL facts; filing wins, log the gap (spec §1.6, X-01)
- [ ] **#13** Extend `forecast.horizon` to 2028Q4 (DCF: 3 modeled years); recheck parity and backtest
- [ ] **#15** Report held-out quarters (released after the LLM's training cutoff) separately (spec §6); the cutoff is unpublished (D-043), so pick a stated cutoff date

## Large (days)

- [ ] Bounded research runs for note sections 2.3, 4.2, 6, 7, 8 (spec §2.3 req. 2)
- [ ] `initiation` report type, and `type` as a Report input (spec §2, §2.3; design §1)
