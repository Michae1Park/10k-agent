# V4 · Worked example: UBER as of 2026-08-06

One Report run, followed across the interfaces of [design doc §2.3](../V4_TECHNICAL_DESIGN_DOC.md#23-interface-catalog) with real payloads. Values are from `output/reports/UBER/2026-08-06/`.

**1. Build → Store (interfaces 1, 6).** The Q2 2026 release (8-K filed 2026-08-05, before the open) is chunked as `UBER-2026Q2-ER-EX99-*`, and extraction stores a KPI row:

```json
{"metric": "gross_bookings", "segment": "mobility", "period": "2026Q2", "value": 28988.0,
 "unit": "USD millions", "chunk_id": "UBER-2026Q2-ER-EX99-…", "quote": "| Mobility | … | 28,988 | …"}
```

At as-of 2026-08-06 the store sees it (`filed` 2026-08-05 < D); at 2026-08-05 it would not (design §2.5).

**2. S1 → S3 (interfaces 2, 7, 8).** The template line `gross_bookings_mobility` takes `source: kpi:gross_bookings.mobility`, so `model.json` gets:

```json
"gross_bookings_mobility|2026Q2": {"value": 28988.0, "kind": "input",
  "ref": "kpi:UBER:gross_bookings.mobility:2026Q2", "filed": "2026-08-05"}
```

The layer-2 checks pass; `no_future_input` confirms `filed < 2026-08-06`.

**3. S5 forecast.** Later cells are formulas: `take_rate_mobility|2026Q3` = the same quarter last year + the held shift → `{"value": 0.2532, "kind": "formula"}`. FY2027 adj. EBITDA sums its quarters.

**4. S2 events and S9 debates (interfaces 4, 5, 9).** A sub-agent with the `ask` tools returns an event:

```json
{"type": "results", "summary": "Uber reported Q2 2026 revenue of $14.19 billion, up 12% YoY, …",
 "evidence": ["kpi:UBER:revenue:2026Q2", "kpi:UBER:eps_diluted:2026Q2", …]}
```

One debate per scenario driver (`gb_g_mobility.g0`, …) returns bull, bear, hypothesis, trigger, gap and evidence ids; items whose ids don't resolve are dropped.

**5. S7 value → `valuation.json` (interface 8).** Peers LYFT, DASH and CART are built with defaults at the same as-of; the PT blends the multiple and DCF values per scenario: `price_target` and `rating` (values local only), with an `inputs_hash`.

**6. S10 facts and prose (interfaces 10, 11).** The price target becomes fact `F-2` with source `model:UBER:2026-08-06:val-base:price_target`. The `takeaways` slot writes "…a 12-month PT of $<PT> {F-2}…"; `trace.py` finds every number marked; the consistency check confirms that section 1's PT equals section 6's.

**7. S11 export (interface 12).** `note.md` and `note.pdf`; `workbook.xlsx` with parity 750/750 formula cells; 18 exceptions numbered `X-01`…`X-18`, e.g. `{"id": "X-01", "severity": "info", "item": "shares_diluted missing in 2023Q4, 2024Q4, 2025Q4"}`; version `UBER:v2` saved as `draft`.

**8. Read back.** A later Ask run at an as-of after 2026-08-06 calls `research_report(company="UBER")` and cites `model:UBER:2026-08-06:v2:price_target`, shown with its status `draft`.
