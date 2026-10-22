# V0 · Parsing and chunking: 10-K HTML → structured document → chunks

**Builds on** nothing: start here. **Code:** [`preprocess/`](../../src/tenk_agent/preprocess/), [`chunking/`](../../src/tenk_agent/chunking/), run over the corpus by [`build.py`](../../src/tenk_agent/build.py). **Design and results:** [experiments/V0_PARSING_CHUNKING.md](../../experiments/V0_PARSING_CHUNKING.md) (it names the rules D1–D8, V1–V7, A1–A5, C1–C10 and T1–T3 used below). **Run it:** [Getting started](../GETTING_STARTED.md#3-build-the-corpus). Overview: [ARCHITECTURE.md](../ARCHITECTURE.md).

## Data

| Data | Where |
|---|---|
| 10-K primary document (HTML), filing metadata (accession, fiscal year, period end, URL) | `data/raw/filings/<TICKER>/FY<year>/*.htm`, `metadata.json`; all of them in `data/raw/manifest.json` |
| XBRL company facts (gold cross-checks only, D-007) | `data/raw/edgar/<TICKER>/companyfacts.json` |
| **Work files, per filing** | `data/v0-chunking/<TICKER>-FY<year>/` (`build.out`) |
| **Database, per chunking** | `data/v0-chunking/<method>.db` (V1) |

| Work file | Holds |
|---|---|
| `blocks.json` | Step 1: every paragraph and table in reading order, with features |
| `labels.json`, `labels-log.json` | Step 2: the structure agent's label per block (the cache); settings, time, tokens and each window's violations |
| `document.json` | The structured document: filing metadata, one section per Item, dropped counts, blocks to review |
| `chunks-<method>.json`, `chunks-log.json` | Each method's chunks; the `chunking:` settings they were made with |
| `chunk.log` | The filing's build output |

Built so far: the FY2025 filings of AAPL, AMZN, MSFT and NVDA (the V1 and V2 gold sets' filings).

## Knobs

`config.yaml`; the V0 experiment pins its own copy in [experiments/v0/config.yaml](../../experiments/v0/config.yaml).

| Knob | What it does | Default |
|---|---|---|
| `llm.model`, `llm.base_url` | The structure agent and the agentic chunker's LLM (vLLM) | Qwen3.6-27B-FP8 on :8001 |
| `labeling.window` | Blocks labeled per call | 150 |
| `labeling.context` | Labeled blocks from the previous window shown to the next | 10 |
| `labeling.repair_rounds` | Validate → repair rounds per window, at most | 2 |
| `chunking.tokenizer` | Counts every token size: the embedder's, so sizes are what it sees | Qwen3-Embedding-0.6B |
| `chunking.target` | Rule-based and agentic chunk size, tokens | 600 |
| `chunking.parent`, `child`, `child_overlap` | Parent-child sizes, tokens | 1,280 / 256 / 20 |
| `chunking.candidate_size` | Agentic: candidate pieces over this split further (paragraph → sentence → word) | 128 |
| `chunking.caption_chars`, `table_chars`, `overlap_chars` | Longest caption paragraph (C3); tables split into row groups above this (C4); longest paragraph repeated in the next chunk (C7) | 300 / 7,200 / 600 |
| `build.methods`, `build.out` | What `tenk build` chunks and loads, and where | `[parent_child]`, `data/v0-chunking` |

## Outputs

| Output | Where |
|---|---|
| Labeling time and tokens; sections, dropped blocks and blocks to review | terminal (`chunk.log` in a build), `labels-log.json`, `document.json` |
| Chunks per method | terminal, `chunks-<method>.json` |
| V0 experiment: scores per method (criteria 1–3) | `output/chunking/<label>/scores.json` |
| V0 experiment: sanity cases, ok / coarser / fail | `output/chunking/<label>/sanity.json` |

## How it works

```
HTML ─► blocks + features (code) ─► labels per window (LLM) ─► validate ─► repair ─► assemble (code) ─► document ─► chunks
```

| Step | Code | What happens | AAPL FY2025 |
|---|---|---|---|
| 1. Blocks | `preprocess/blocks.py` | Rules true of every 10-K (D1–D8): hidden elements dropped, inline XBRL unwrapped (text blocks kept as hints), `dei:` cover tags read, split cells joined (`$` + `1,234`), paragraphs and tables in reading order with features (emphasis, Item label, XBRL start, page break, link) and pages | 698 blocks |
| 2. Label | `preprocess/structure.py`, [prompts/structure.md](../../src/tenk_agent/prompts/structure.md) | The LLM labels ~150 blocks a call (temperature 0, thinking off, output forced to [structure.schema.json](../../src/tenk_agent/prompts/structure.schema.json)): `item_heading`, `heading` (level, kind, run-in), `data_table` (units), `layout_table`, `body`, or noise (`cover`, `toc`, `furniture`, …) | 10 calls, 34 min |
| 3. Validate, repair | `preprocess/validation.py` | Code checks each window (V1–V7: one label per block, Items in Form 10-K order, levels step down, risk kinds only in 1A, …); violations go back to the LLM, 2 rounds at most | 2 blocks left to review |
| 4. Assemble | `preprocess/structure.py` | Labels applied to the filing's own text (A1–A5): one section per Item, noise dropped, run-in headings split, statements filed under Item 15 moved into Item 8, heading paths, printed page numbers | 23 sections; 110 blocks dropped |
| 5. Chunk | `chunking/` | One of three methods (below), all within one Item, each data table its own chunk with its caption | parent-child: 216 parents |
| 6. Ids, metadata | `chunking/common.py` | `<TICKER>-FY<year>-<item>-<seq>`; Item, heading path, pages; tables add `units` and `years_covered` | |

| Method | Code | Splits | LLM |
|---|---|---|---|
| `rule` | `chunking/rule_based.py` | A new chunk at every heading; prose packed to 600 tokens, long paragraphs cut at sentence ends | no |
| `parent_child` | `chunking/parent_child.py` (LlamaIndex) | Parents ≤ 1,280 tokens that never cross a heading, each with children ≤ 256 tokens; a table's children are its title and one per row, naming the columns | no |
| `agentic` | `chunking/agentic.py` (Chonkie) | The LLM picks each chunk's end; code keeps each risk factor on its own and never ends a chunk on a heading | one call per cut |

**Parent-child is the default** (`build.methods`): V1 retrieves best with it ([V1 results](../../experiments/V1_RETRIEVAL.md)).

**A table chunk** (`AAPL-FY2025-8-002`, first lines), with two of its row children:

```
CONSOLIDATED STATEMENTS OF OPERATIONS
(In millions, except number of shares, which are reflected in thousands, and per-share amounts)
| Years ended |
| September 27, 2025 | September 28, 2024 | September 30, 2023 |
| Net sales: |
| Products | $307,003 | $294,866 | $298,085 |
| Services | 109,158 | 96,169 | 85,200 |
| Total net sales | 416,161 | 391,035 | 383,285 |

row: Net sales › Products: September 27, 2025 = $307,003; September 28, 2024 = $294,866; …
row: Net sales › Total net sales: September 27, 2025 = 416,161; September 28, 2024 = 391,035; …
```

Metadata: `item 8`, heading path `CONSOLIDATED STATEMENTS OF OPERATIONS`, pages 31 (printed 29), units `in millions, except per-share amounts`, years 2023–2025.

## Gotchas

- **Labels are cached and keyed by their settings.** In the experiment, changing `labeling:` stops preprocessing with a "labeled with … not …" error: `--relabel`, or set another `run.label`. A build never preprocesses a filing that has a `document.json`: delete it (and `labels.json`, to relabel). Chunks are redone whenever `chunking:` changes.
- **The tokenizer loads from the local Hugging Face cache only** (`local_files_only`): download it once (`huggingface-cli download Qwen/Qwen3-Embedding-0.6B`, or any `tenk search`).
- **An excerpt has no cover page.** `preprocess(html, cover, …)` takes the full filing as `cover` for the `dei:` tags; a full filing is its own cover.
- **Units differ by filer.** Netflix reports in thousands; per-share figures in dollars. `units` is the LLM's reading of the caption; a table without one has none.
- **The same number is in several chunks.** A 10-K repeats the statements' figures in MD&A and the notes; the gold sets name a primary source and its restatements ([V1](V1_RETRIEVAL.md#gold-questions)).
- `tenk build --stage load` rebuilds `<method>.db` from scratch: run `embed` again afterwards.
