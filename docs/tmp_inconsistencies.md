# Remaining inconsistencies (temporary)

Spec vs. design doc vs. build, checked 2026-11-01. Mirrored in design doc §13.1. Delete this file once resolved.

## Decisions taken

| Item | Decision |
| --- | --- |
| Proposer scenario-shock bounds [0.5σ, 2σ] (design doc §7.3, D-042) | Cut (`a62fe92`) |
| Carry open exceptions forward (§9.3) | Build (#9) |
| Non-operating item exception (§13.2) | Build (#10) |
| Summary tab layout | Merge old vs. new into Summary; headline moves to Valuation; drop the Revisions tab (#5) |
| Exception severities | **Open.** Spec §2.3: warning, data gap, information. Build: `info`, `warning`, `error` (`pipeline.py:168`, `:238`, `:251`; `note/consistency.py`) |

## To build

| # | Item | Spec / doc | Build today | Effort |
| --- | --- | --- | --- | --- |
| 1 | Variance flag | Spec §3.2, doc §7.3: over 2% (revenue) or 5% (adj. EBITDA) vs. the guidance midpoint needs a written reason, else an exception | `variance_flag` in `research.yaml` is never read; table 2.2 has no Flag column (`note/sections.py:150`) | S (~1 h) |
| 2 | Consensus | Spec §2.3: "Consensus n/a" column in 2.1; doc §9.3: "no consensus" exception on every run | Absent from `research/` (`note/sections.py:131`) | XS (~20 min) |
| 3 | Evidence log fields | Spec §2.3: ID, figure, value, source, period, filing date, page or table, retrieval date, type; doc §9.3 adds workbook cell | Log tab: id, kind, label, value, source (`export/report.py:65`) | M (~3–4 h) |
| 4 | Exception fields | Spec §2.3: severity, description, treatment, status (open or resolved) | No treatment or status; treatment column blank (`export/report.py:67`) | S (~1–2 h) |
| 5 | Summary tab | Spec §2.3: old vs. new, change in $ and %, source of change, type, confidence | Summary = valuation headline; old vs. new on Revisions without $/% change or source (`export/report.py:42,61`) | S (~1–2 h) |
| 6 | Scenarios tab | Spec §2.3: weights, probability-weighted value, one-at-a-time sensitivities | Scenario values by line and year only (`export/report.py:49`) | S (~1 h) |
| 7 | Thin peer set | Spec example X-04; doc §9.3: fewer than 4 peers is an exception | Not logged (`valuation.py`) | XS (~10 min) |
| 9 | Carry forward open exceptions | Doc §9.3 | Not built; depends on #4's status field | S (~1 h) |
| 10 | Non-operating item exception | Doc §13.2: any of the last 4 quarters over 10% of NI | Only "forecast 0" is built | S (~1 h) |
| — | Bounded research runs for sections 2.3, 4, 6, 7, 8 | Spec §2.3 req. 2; doc §8.3 | 2.3 and 6 are single LLM calls without tools; 4, 7, 8 have no LLM text; only section 3 debates are tool-using runs | L (~1–2 days) |
| — | `initiation` report type | Spec §2, §2.3 | `update` only | L (~2–3 days) |
| 11 | Assumptions tab columns | Spec §2.3: value, basis, confidence, variables affected, last updated | Name and value only (`export/workbook.py:87`) | S (~1 h) |
| 12 | Debates with the Research toolset | Spec §2.3 req. 2 | S9 sub-agents use the `ask` set (`research/subagents.py`) | S (~1 h) + an LLM rerun to check |
| 13 | DCF: 3 modeled years | Spec §3.3 (3 modeled, 7 faded), §3.2 annual to 2028E | Model ends 2027Q4; 2028 faded in the DCF | M (~2–3 h): extend `forecast.horizon` to 2028Q4 and recheck parity and backtest |
| 14 | Outside data as forecast inputs | Spec §3.2: BTS, NYC TLC, FRED | Context only | Decide: build, or record as a deliberate difference |
| 15 | Held-out quarter set | Spec §6 | Not reported (D-043) | Decided; spec could note it |
| 16 | Extraction gold size | Spec §5: ~384 labels | 337 | Decide: add labels, or note the count in the spec |
| 17 | Section 10 repair turn | Spec §2.3 req. 6 | Section 10 is code-written; no repair | Likely note only |
| 18 | Parity mismatch should stop the run | Design §10.3 (D-041) | Recorded in `report.json`; the run continues | XS (~15 min) |
| 19 | Revision type and source of change | Spec §2.3 table 4.1: source of change + type (recurring/mixed/timing), every changed line | `type` holds the source; three lines only (`pipeline.py:336`) | S (~1–2 h); pairs with #5 |
| 20 | `approve` on an unknown version | Should error | Silently updates nothing (`store.py:522`) | XS (~10 min) |

## Deliberate differences (design §13.3)

Uber EBITDA as revenue − GB-share costs; bridge and rating on Summary; Model (prior) as values; FY anchor for full-year-only guidance.

## Already fixed this session

- `a62fe92`: workbook DCF and Model (prior) tabs; stale `research_report` line in the build log; proposer growth bounds (now design §7.3); `note/consistency.py` in the code layout (now Appendix C).
- Design doc restructured (2026-11-01): system spine + deep dives; build log moved to `V4_BUILD_LOG.md`; decisions to D-040–D-044. The not-built list is mirrored in design §13.1.
