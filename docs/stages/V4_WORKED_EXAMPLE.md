# V4 · Worked example: one Uber report

One Report run for **Uber, as of 2026-08-06**, followed from a filed number to the finished note. Every value below is real, from `output/reports/UBER/2026-08-06/`.

The run in one line: Uber's Q2 2026 earnings release is filed → its numbers feed the model → the model forecasts and values the stock → the note is written and checked → a draft version is saved.

| # | Step | What happens | Output |
| --- | --- | --- | --- |
| 1 | Data | The Q2 release is stored and its KPIs extracted | KPI rows |
| 2 | Model | Filed numbers become model inputs | `model.json` (history) |
| 3 | Forecast | Future quarters are filled by formulas | `model.json` (forecast) |
| 4 | Events and debates | Sub-agents read filings for news and bull/bear cases | `events.json`, `debates.json` |
| 5 | Valuation | Peers, multiples and DCF give a price target and rating | `valuation.json` |
| 6 | Note | Code lists the facts; the LLM writes prose citing them | `facts.json`, `note.json` |
| 7 | Export | Note, PDF, workbook and issue log; version saved | report folder, `UBER:v2` |
| 8 | Read back | A later question cites the saved report | an Ask answer |

---

## 1. Data: the release is filed and extracted

Uber filed its Q2 2026 release (8-K) on **2026-08-05**, before the market opened. The build splits it into chunks and the extractor pulls each KPI with the exact quote it came from:

```json
{"metric": "gross_bookings", "segment": "mobility", "period": "2026Q2", "value": 28988.0,
 "unit": "USD millions", "chunk_id": "UBER-2026Q2-ER-EX99-…", "quote": "| Mobility | … | 28,988 | …"}
```

**As-of rule:** a run sees only data filed *strictly before* its date.

| Run as of | Sees the Q2 release? |
| --- | --- |
| 2026-08-05 | No: filed the same day |
| 2026-08-06 | Yes |

## 2. Model: filed numbers become inputs

Uber's model template has a line `gross_bookings_mobility` whose source is the KPI above. The model stores the value with its source id and filing date:

```json
"gross_bookings_mobility|2026Q2": {"value": 28988.0, "kind": "input",
  "ref": "kpi:UBER:gross_bookings.mobility:2026Q2", "filed": "2026-08-05"}
```

The integrity checks then run. One of them, `no_future_input`, confirms every input was filed before 2026-08-06.

## 3. Forecast: future quarters are formulas

No forecast number is typed in. Each is a formula over history and assumptions:

| Cell | Formula | Value |
| --- | --- | --- |
| Mobility take rate, Q3 2026 | same quarter last year + the held shift | 25.32% |
| Adj. EBITDA, FY2027 | sum of its four quarters | (formula) |

## 4. Events and debates: reading the filings

Two kinds of sub-agent search the filings. Each item they return must cite evidence ids that resolve; items that don't are dropped.

**Events** — what happened. Example:

```json
{"type": "results", "summary": "Uber reported Q2 2026 revenue of $14.19 billion, up 12% YoY, …",
 "evidence": ["kpi:UBER:revenue:2026Q2", "kpi:UBER:eps_diluted:2026Q2", …]}
```

**Debates** — one per scenario driver (e.g. Mobility bookings growth). Each gives a bull case, bear case, hypothesis, trigger, gap and evidence.

## 5. Valuation: price target and rating

1. Build the peers (LYFT, DASH, CART) with default assumptions at the same as-of.
2. Value Uber per scenario (bear, base, bull) with a peer multiple and a DCF.
3. Blend into one **price target** and derive the **rating**.

`valuation.json` stores the result with an `inputs_hash`, so the same inputs always give the same bytes. (Prices are licensed for internal use, so values stay local.)

## 6. Note: facts first, then prose

Code builds a numbered list of every printable fact. The LLM may only cite facts, never type a number:

| | Content |
| --- | --- |
| Fact | `F-2` = the price target, source `model:UBER:2026-08-06:val-base:price_target` |
| LLM writes | "…a 12-month PT of $<PT> {F-2}…" |
| Trace check | every number in the prose is a marked fact |
| Consistency check | the PT in section 1 equals the PT in section 6 |

## 7. Export: files and a saved version

| Output | Result |
| --- | --- |
| `note.md`, `note.pdf` | The draft note |
| `workbook.xlsx` | 750 of 750 formula cells match the Python model |
| `exceptions.json` | 18 issues, `X-01` to `X-18`; e.g. `X-01` (info): "shares_diluted missing in 2023Q4, 2024Q4, 2025Q4" |
| Version | `UBER:v2`, status `draft` until a human approves it |

## 8. Read back: later runs can cite the report

An Ask run at any later as-of can call `research_report(company="UBER")` and cite `model:UBER:2026-08-06:v2:price_target`. The answer shows the status, `draft`, so a reader knows it is unreviewed.
