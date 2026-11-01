# Remaining inconsistencies (temporary)

Spec vs. design doc vs. build, checked 2026-11-01. Mirrored in design doc §13.1. Delete this file once resolved.

## Decisions taken

| Item | Decision |
| --- | --- |
| Proposer scenario-shock bounds [0.5σ, 2σ] (design doc §7.5, D-042) | Cut (`a62fe92`) |
| Carry open exceptions forward (§9.6) | Build (#9) |
| Non-operating item exception (§13.2) | Build (#10) |
| Summary tab layout | Merge old vs. new into Summary; headline moves to Valuation; drop the Revisions tab (#5) |
| Exception severities | **Open.** Spec §2.3: warning, data gap, information. Build: `info`, `warning`, `error` (`pipeline.py:168`, `:238`, `:251`; `note/consistency.py`) |

## To build

| # | Item | Spec / doc | Build today | Effort |
| --- | --- | --- | --- | --- |
| 1 | Variance flag | Spec §3.2, doc §7.5: over 2% (revenue) or 5% (adj. EBITDA) vs. the guidance midpoint needs a written reason, else an exception | `variance_flag` in `research.yaml` is never read; table 2.2 has no Flag column (`note/sections.py:150`) | S (~1 h) |
| 2 | Consensus | Spec §2.3: "Consensus n/a" column in 2.1; doc §9.6: "no consensus" exception on every run | Absent from `research/` (`note/sections.py:131`) | XS (~20 min) |
| 3 | Evidence log fields | Spec §2.3: ID, figure, value, source, period, filing date, page or table, retrieval date, type; doc §9.6 adds workbook cell | Log tab: id, kind, label, value, source (`export/report.py:65`) | M (~3–4 h) |
| 4 | Exception fields | Spec §2.3: severity, description, treatment, status (open or resolved) | No treatment or status; treatment column blank (`export/report.py:67`) | S (~1–2 h) |
| 5 | Summary tab | Spec §2.3: old vs. new, change in $ and %, source of change, type, confidence | Summary = valuation headline; old vs. new on Revisions without $/% change or source (`export/report.py:42,61`) | S (~1–2 h) |
| 6 | Scenarios tab | Spec §2.3: weights, probability-weighted value, one-at-a-time sensitivities | Scenario values by line and year only (`export/report.py:49`) | S (~1 h) |
| 7 | Thin peer set | Spec example X-04; doc §9.6: fewer than 4 peers is an exception | Not logged (`valuation.py`) | XS (~10 min) |
| 9 | Carry forward open exceptions | Doc §9.6 | Not built; depends on #4's status field | S (~1 h) |
| 10 | Non-operating item exception | Doc §13.2: any of the last 4 quarters over 10% of NI | Only "forecast 0" is built | S (~1 h) |
| — | Bounded research runs for sections 2.3, 4, 6, 7, 8 | Spec §2.3 req. 2; doc §8.4 | 2.3 and 6 are single LLM calls without tools; 4, 7, 8 have no LLM text; only section 3 debates are tool-using runs | L (~1–2 days) |
| — | `initiation` report type | Spec §2, §2.3 | `update` only | L (~2–3 days) |

## Not checked in detail

- Assumptions tab columns (spec §2.3: value, basis, confidence, variables affected, last updated).

## Already fixed this session

- `a62fe92`: workbook DCF and Model (prior) tabs; stale `research_report` line in the build log; proposer growth bounds (now design §7.6); `note/consistency.py` in the code layout (now Appendix C).
- Design doc restructured (2026-11-01): system spine + deep dives; build log moved to `V4_BUILD_LOG.md`; decisions to D-040–D-044. The not-built list is mirrored in design §13.1.
