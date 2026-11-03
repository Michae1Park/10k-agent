# V4 · Data layer

How filings, facts and market data are fetched and stored. Rules live in the [design doc §3](../V4_TECHNICAL_DESIGN_DOC.md#3-data-layer); if this doc disagrees, the design doc wins.

## The build

<p align="center">
  <img src="../img/v4-data-pipeline.svg" alt="The V4 build: fetch into a raw cache, then four lanes (text, structured facts, KPI extraction, market data) into one SQLite file" width="100%">
</p>

Two commands:

1. `tenk fetch` downloads everything into a raw cache.
2. `tenk build` loads it into one SQLite file along four lanes:

| Lane | Does |
| --- | --- |
| Text | Split filings into chunks, load, embed |
| Facts | XBRL company facts and inline XBRL |
| KPIs | LLM extraction from releases ([V4_EXTRACTION.md](V4_EXTRACTION.md)) |
| Market | Prices, macro series, equity risk premium, cross-checks |

`--stage append` adds new filings to an existing database.

## Quarterly figures

<p align="center">
  <img src="../img/v4-quarters.svg" alt="Quarter derivation: Q1 direct, Q2 and Q3 direct or year-to-date difference, Q4 as FY minus 9M, newest fact visible at the as-of date, checks, financials tool" width="100%">
</p>

Companies don't always report each quarter on its own, so `facts/quarters.py` derives them on every read:

| Quarter | Source |
| --- | --- |
| Q1 | The three-month figure |
| Q2, Q3 | The three-month figure; else year-to-date minus the prior year-to-date |
| Q4 | Full year minus nine months (no 10-Q covers Q4) |
| Balances | As reported |

- Each figure uses the **newest version visible at the as-of**, so restatements count only once filed.
- One XBRL concept is used per fiscal year.
- Each quarter records how it was derived, its input ids and its filing date.

## Text

- Each filing is split into its Items (Item 1A, Item 7, …) by per-form rules (`preprocess/forms.py`).
- Chunks are small children inside larger parents; tables stay whole.
- Every chunk is labeled with its source, e.g. "Uber Q2 2026 10-Q, Part I Item 2 MD&A".

## Implementation notes

| Note | Why |
| --- | --- |
| NYC TLC trips are summed per month and license at fetch | Each month's raw file is ~540 MB |
| Company facts and inline XBRL share one table | Company facts lack segment dimensions and company-specific concepts |
| `metrics.yaml` lists concepts in priority order; the first found sets the year | Keeps one concept per year |
| A company whose fetch fails is logged and skipped | One failure doesn't stop the build |

## Tests

| File | Checks |
| --- | --- |
| `test_asof.py` | Strictly-before rule; newest restatement wins; nothing visible until the day after filing |
| `test_facts.py` | Year-to-date math; no Q4 before the 10-K; restatements; one concept per year |
| `test_filings_v4.py` | Period keys; releases matched to reports; duplicate filings flagged |
| `test_market.py` | Previous close only; revised series use the vintage known then |
| `test_acquisition.py` | User-Agent required; date window; Item 2.02 8-Ks only |
| `test_store.py`, `test_rules.py` | Chunk loading and filters; KPIs dated by their release |

V3 regression: `experiments/v4/v3_regress.py` checks the shared pipeline still gives identical output on the V3 corpus.
