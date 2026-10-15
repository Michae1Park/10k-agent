# V7 · Advanced retrieval: search small, read large, check the cell

**Builds on** V0–V4 and keeps them: V7 is a second index (`data/tenk-parent-child.db`) and a few switches, so the naive pipeline stays the default and the baseline. Every change is one row of an ablation, scored on the main gold set and on the stress set ([EVAL.md](EVAL.md#stress-set)). **Run it:** `versions/v7_advanced_retrieval/run.sh` (steps below).

## Commands

Workstation terminal, repo root. Keep the same `TAG` across steps; `EXTRA=--include-unverified` while the gold set is a draft ([I-011](DECISIONS.md#i-011)).

```bash
versions/v7_advanced_retrieval/run.sh index        # ingest + embed the V7 index (~15 s + ~3 min on a free GPU)
versions/v7_advanced_retrieval/run.sh retrieval    # rows r1–r4, no model: Recall@5 / MRR
scripts/serve_vllm.sh qwen3.6-27b-fp8              # other terminal
versions/v7_advanced_retrieval/run.sh ask          # rows r1–r4 in Ask mode
versions/v7_advanced_retrieval/run.sh research     # r4 (= V7) in Research mode
scripts/serve_vllm.sh gpt-oss-20b                  # after stopping the model
versions/v7_advanced_retrieval/run.sh judge
BASE_TAG=<tag of the V2/V4 runs> versions/v7_advanced_retrieval/run.sh compare

TENK_LAYOUT=parent_child TENK_RETRIEVAL=hybrid TENK_ITEM_FILTER=1 tenk ask "..."    # V7 by hand
TENK_LAYOUT=parent_child tenk search "Intelligent Cloud revenue" --tickers MSFT --years 2025
```

## Data

| Data | Where |
|---|---|
| V7 index: parents (`chunks`), children (`children`, FTS5 `children_fts`), vectors for both | `data/tenk-parent-child.db` |
| Naive index, untouched | `data/tenk.db` |
| Stress set | `eval/gold/stress.jsonl` |

| Table | Holds |
|---|---|
| `chunks` (parents) | Same columns and ID format as V0. Each table whole; prose packed into subsections of ≤ `parent_chars` that break at headings. This is what the model reads, cites and is verified against |
| `children` | `id` (`<parent id>.<nnn>`), `parent_id`, `kind` (`prose` · `row` · `table_title`), `text`, `row` (JSON: group, label, cells as `[column, fiscal year, value]`) |

## Knobs

`config.yaml`; every V7 knob defaults to the naive behavior.

| Knob | Env | V7 | What it does |
|---|---|---|---|
| `ingest.layout` | `TENK_LAYOUT` | `parent_child` | Which index: `flat` (`tenk.db`) or `parent_child` (`tenk-parent-child.db`) |
| `ingest.parent_chars` | | 6000 | Prose parent size (~1,500 tokens) |
| `ingest.child_chars` | | 800 | Prose child size (~200 tokens); tables: one child per row |
| `retrieve.search_children` | `TENK_SEARCH_CHILDREN` | true | Search children and return parents; `false` searches the parents themselves (row r1) |
| `retrieve.mode` | `TENK_RETRIEVAL` | `hybrid` | Dense + BM25 with reciprocal rank fusion, then the reranker |
| `retrieve.item_filter` | `TENK_ITEM_FILTER` | true | "Item 7A", "risk factors", "MD&A" in the question filter by Item |
| `answer.context_chars` | `TENK_CONTEXT_CHARS` | 0 | Cap on excerpt text per Ask call and per `search_filings` result (0 = all k) |

## How it works

```
HTML ─► blocks ─► sections ─► parents (whole tables, subsections) ─► children (sentence windows, table rows)
question ─► slots (+ Item) ─► children: dense + BM25 ─► RRF ─► rerank ─► their parents, each once ─► top k
claim ─► value in a cited parent? ─► (V7) right company? in a cell of the claimed fiscal year?
```

| Part | What changes from V0–V4 | Why |
|---|---|---|
| **Row records** (`tables.py`) | Each table row becomes `Intelligent Cloud › Revenue: FY2025 = $106,265; FY2024 = $87,464; FY2023 = $72,944`: the group header it sits under, and every column's fiscal year from the header (dates and years; oldest-first columns; a one-year row over transposed segment columns) | The row label alone ("Revenue") repeats under every segment; the column order differs by filer |
| **Units** | A table without a caption inherits units from the table it continues, or from "(in millions)" at the end of the paragraph before it | 414 → 312 tables without units |
| **Parents** | Tables whole (as V0); prose up to 6,000 chars, split at headings | The model reads a whole note or table, not 600 tokens of it |
| **Children** | ~200-token sentence windows; one child per table row; one table-title child (caption + header) | A row or a sentence matches a question far more precisely than a 1,500-token block |
| **Search** | The V1 pipeline (slots, dense/hybrid, rerank) runs over children; results become their parents, best first, each once | Precision of small units, context of large ones |
| **Item filter** | An Item named in the question becomes a filter, like companies and years | "Item 7A" by meaning finds risk prose anywhere |
| **Cell check** (`verify.py`) | A number is *verified* only if a cited chunk is from the claimed company and, in a table, the number sits in the claimed fiscal year's column (or an undated one) | "87,464" is in the FY2025 10-K, but in the FY2024 column |

**Index size** (24 filings): 4,945 parents (mean 1,611 chars; naive: 6,299 chunks, 1,320) and 26,861 children: 13,061 prose (~620 chars), 12,229 rows (~190), 1,571 table titles.

## Results

`TAG=20261014`, Qwen3.6-27B-FP8, draft gold set (unverified, [I-011](DECISIONS.md#i-011)). Reports: `eval/reports/v7-*-20261014.{md,html}`.

**Retrieval** (no model; Recall@5, paired against naive V1 on the same questions):

| Row | Dev (68) | Test (26) | Stress (26) |
|---|---|---|---|
| Naive V1 | 59.3% | 71.2% | 88.5% |
| r1 parents only | 58.3% (−1.0, p 0.80) | 74.1% | 84.6% |
| **r2 children → parents** | **74.8% (+15.4, p 0.001)** | 83.3% (+12.1, p 0.14) | 90.4% |
| r3 + hybrid | 74.0% (+14.7, p 0.004) | 81.6% | 94.2% (+5.8, p 0.55) |
| r4 + Item filter | — | 81.6% | 94.2% |

- **Searching small children is the gain**: +15 points Recall@5 and +0.11 MRR on dev (14 questions fixed, 2 regressed; McNemar p 0.004). Larger chunks on their own change nothing (r1).
- **Hybrid** helps only the stress set (exact labels like "Other Bets", "Free cash flow"), within noise; it stays a switch.
- **The Item filter is untested**: no gold question names an Item. It needs questions that do.
- Test and stress alone are too small to call: the same direction, p > 0.05.

**Ask** (one search, then answer; task completion / correctness / citation accuracy, judged by gpt-oss-20b, paired against naive V2):

| Row | Test (31) | Stress (29) |
|---|---|---|
| Naive V2 | 61.3% / 76.6% / 100% | 58.6% / 71.8% / 94.4% |
| r1 parents only | 61.3% / 77.2% / 94.4% | 58.6% / 72.7% / 96.8% |
| **r2 children → parents** | **71.0%** / 78.5% / 97.2% (3 fixed, 0 regressed, p 0.25) | 58.6% / 72.7% / 88.2% |
| r3 + hybrid | 64.5% / 78.0% / 97.1% | 58.6% / 71.8% / 90.3% |
| r4 + Item filter | 67.7% / 76.9% / 100% | 58.6% / 71.8% / 90.3% |

- **Better retrieval reaches the answers only on the main test**, and not significantly: r2 fixes 3 questions and breaks none. On stress every row completes 17 of 29 (one fixed, one broken each); what fails there is reading (a recast column, the NVIDIA fiscal calendar, unlisted arithmetic, I-008), not finding.
- **The cell check fired once**: r2 stress claimed "27%" R&D intensity for FY2024; the only "27%" in the cited table is the FY2022 cell. The other citation drops on stress are ordinary "value not found" misses on ~30 claims.

**Research** (agent, r5 = r4 + a 12,000-character cap per search, [D-033](DECISIONS.md#d-033); against the V4 agent on the naive index, the test baseline is `v5-qwen3.6-27b-fp8-test-20261013`):

| | Test (31) | Stress (29) |
|---|---|---|
| Task completion | 74.2% → 74.2% (2 fixed, 2 regressed) | 82.8% → 86.2% (4 fixed, 3 regressed) |
| Answer correctness | 90.0% → 90.5% | 88.9% → 93.8% |
| Evidence recall | 75.0% → 90.5% (p 0.13) | **77.1% → 100% (p 0.02)** |
| Over-budget rate | 12.9% → 16.1% | 3.7% → 7.1% |
| Context-overflow errors | 0 → 0 | 2 → 1 |

- **The agent cites the right evidence far more often** (the one significant Research gain), but its answers barely move: the naive agent's answers were mostly right already, so finding the cited source is not where it lost points.
- **Uncapped, V7 searches overflow the 32k context** on multi-year and multi-company questions (4 of 60 errored, runs `v7-r4-items-research-*`, unjudged). The cap removes 3; the last, `rd-intensity-highest-fy2024` (eight companies), overflowed in V4 too. The price is more, smaller searches: over-budget runs go up on both sets.

## Gotchas

- **Two databases, two sets of chunk IDs.** V7 parent IDs look like V0 chunk IDs (`AAPL-FY2025-8-012`) but number different chunks. Traces and citations from one index don't resolve in the other. Gold questions are unaffected: evidence is matched by text ([EVAL.md](EVAL.md)).
- **`tenk ingest` / `tenk embed` follow `TENK_LAYOUT`.** Without it they rebuild the naive index.
- **Larger parents mean more tokens per search.** Uncapped, Research mode on a 32k-context model overflows on multi-year questions; `TENK_CONTEXT_CHARS` caps each search (r5 uses 12,000, [D-033](DECISIONS.md#d-033)).
- **Row records are heuristics.** Groups end at a "Total …" row or the next group header; a few tables (merged "$ %" change columns, maturity schedules) get generic column names. `python -c "from tenk_agent.tables import table_rows"` on a table's rows shows what V7 sees.
