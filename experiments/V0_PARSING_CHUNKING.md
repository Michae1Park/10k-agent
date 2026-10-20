# V0 · Parsing and chunking

**Goal:** the best way to turn a 10-K into chunks for retrieval, judged by criteria that work for any filer.

**Result:** rule-based and parent-child are level. Both are perfect on risk factors and give ~93% exact heading paths. Agentic matches them on risk factors only with two rules in code, and its paths are coarser (83% exact).

| Method | Item right | Path exact | One risk factor per chunk | Tables whole | Chunks |
|---|---|---|---|---|---|
| Rule-based | 100% | 93.7% | 100% | 38 / 38 | 190 |
| Parent-child | 100% | 93.2% | 100% | 38 / 38 | 177 parents |
| Agentic | 100% | 83.3% | 100% | 38 / 38 | 138 |

**Contents:** [1 Test data](#1-test-data) · [2 Preprocessing](#2-preprocessing) · [3 Chunking](#3-chunking) · [4 Evaluation](#4-evaluation) · [5 Results](#5-results)

---

## 1. Test data

**File:** [aapl-fy25-excerpt.html](v0/v0_testdata/aapl-fy25-excerpt.html). It holds two verbatim slices of the AAPL FY2025 10-K, about 38.5k tokens in all.

| Slice | Items | Pages | Headings | Tables |
|---|---|---|---|---|
| 1 | 1, 1A | 1–16 | 53 | 0 |
| 2 | 7, 7A, 8 | 21–51 | 103 | 38 data + 1 index |

- **Item 1A:** a preamble, 5 categories and 27 risk factors (129–1,241 tokens, median 331).
- **Items 7–8:** MD&A, the five financial statements, Notes 1–13 and both auditor reports.
- **Ground truth:** [aapl-fy25-excerpt.gt.json](v0/v0_testdata/aapl-fy25-excerpt.gt.json), reviewed by hand (§4.4).

Tokens are counted with the embedder's tokenizer, `Qwen/Qwen3-Embedding-0.6B` (1 token ≈ 4.3 chars).

---

## 2. Preprocessing

```
HTML ─► blocks + features (code) ─► labels per window (LLM) ─► validate ─► repair ─► assemble (code) ─► document
```

Code does only what holds for every 10-K. An LLM labels everything that depends on a filer's styling.

### 2.1 Code: blocks and features

| # | Rule |
|---|---|
| D1 | Parse with BeautifulSoup + lxml, and drop hidden elements |
| D2 | Unwrap inline XBRL facts, and record each note / policy / table text block as a hint |
| D3 | Read filing metadata (ticker, fiscal year, period end) from the `dei:` cover tags |
| D4 | Clean text: entities, `&nbsp;`, whitespace |
| D5 | Split the page into paragraph and table blocks, with ids `b0001…` in reading order |
| D6 | Join split cells: `$` \| `1,234` → `$1,234` |
| D7 | Read each block's features for the LLM: emphasis, length, Item label, XBRL start, page break, link |
| D8 | Give each block its page number |

### 2.2 LLM: the structure agent

- **Model:** `Qwen/Qwen3.6-27B-FP8` on vLLM, at temperature 0 with thinking off. Guided decoding forces the output schema.
- **Files:** the prompt is [system.md](v0/v0_structure/system.md) and the output schema is [labels.schema.json](v0/v0_structure/labels.schema.json).
- **Input:** about 150 blocks per call, one line each (`id | features | text`). Each call also gets the open Item, the open heading path and the previous window's last labels.
- **Output:** one label per block, e.g. `{"id": "b0412", "role": "heading", "level": 2, "kind": "risk_factor"}`. The LLM labels; code builds the document from the filing's own text.

| `role` | Extra fields |
|---|---|
| `item_heading` | `item` |
| `heading` | `level`, `kind` (e.g. `risk_factor`, `statement`, `note`), `run_in` |
| `data_table` | `units`, `units_from` |
| `layout_table` | `rows` |
| `body`, `cover`, `toc`, `part_heading`, `index_table`, `furniture` | — |

**Validate → repair:** code checks each window's labels, and the LLM fixes violations, at most 2 rounds.

| # | Check |
|---|---|
| V1 | One label per block, in order |
| V2 | Items valid, in Form 10-K order, each once |
| V4 | Fields match the role |
| V5 | Heading levels start at 1 in each Item and step down one at a time |
| V6 | Risk kinds only in Item 1A; statement and note kinds only in Item 8 |
| V7 | An XBRL note starts at a `note` heading |

### 2.3 Output: the document

Code assembles one JSON document per filing:
- **Sections:** one per Item, starting at each `item_heading`.
- **Dropped:** cover, TOC, part headings, furniture and index tables.
- **Each block:** its text, kind, heading path, page and XBRL text block. Headings also carry level and kind; tables carry rows and units.
- **Run-in headings** split into heading + body. Statements filed under Item 15 move into Item 8.

```json
{"item": "1A", "title": "Risk Factors", "blocks": [
  {"id": "b0391", "kind": "heading", "text": "Business Risks", "level": 1, "heading_kind": "risk_category", "heading_path": ["Business Risks"]},
  {"id": "b0399", "kind": "paragraph", "text": "Substantially all of the Company's manufacturing is performed …", "heading_path": ["Business Risks", "The Company depends on …"]}
]}
```

### 2.4 Input to each chunker

| Chunker | Reads |
|---|---|
| Rule-based | The document's sections and blocks |
| Parent-child | One LlamaIndex `Document` per subsection |
| Agentic | One Markdown text per Item, with headings as `#` and tables as `[TABLE n]` |

### 2.5 Chunk metadata (all three)

- **Fields:** each chunk records `item`, `heading_path`, `kind` (prose / table), `block_ids`, `pages` and `tokens`. Tables also record `units` and `years_covered`.
- **Path:** a chunk's `heading_path` is the deepest path shared by all its blocks.
- **Ids:** `<TICKER>-FY<year>-<item>-<seq>`, e.g. `AAPL-FY2025-1A-007`.

---

## 3. Chunking

| Method | Stack | LLM? | Size |
|---|---|---|---|
| Rule-based | Own code | no | ≤ 600 tokens |
| Parent-child | LlamaIndex `HierarchicalNodeParser` | no | Parents ≤ 1,280, children ≤ 256 |
| Agentic | Chonkie `SlumberChunker` + `Qwen3.6-27B-FP8` | yes | ≤ 600 tokens |

**Shared by all three** ([chunkers.py](v0/v0_src/chunkers.py)):
- No chunk crosses an Item.
- Each data table is one chunk with its caption, as `| cell | cell |` lines.
- Settings are in [config.yaml](v0/v0_src/config.yaml) under `chunking:`.

### 3.1 Rule-based

- **A heading always starts a new chunk.** Without this, packing merges several risk factors into one chunk.
- **Prose:** paragraphs are packed greedily up to 600 tokens. A longer paragraph splits at sentence ends, not after abbreviations like "U.S." or "Inc.".
- **Overlap:** a new chunk repeats the previous chunk's last paragraph if it's short (≤ 600 chars).
- **Long subsections:** every later piece starts with the subsection's heading line.

### 3.2 Parent-child

Search matches small **children**; the model reads the larger **parent**.

| | Parents | Children |
|---|---|---|
| Size | ≤ 1,280 tokens, which holds every AAPL risk factor whole | ≤ 256 tokens, with a 20-token overlap |
| Boundary | Never crosses a subsection | Within the parent |
| Tables | Each table is its own parent | A title child (caption + header rows) and one child per row |

A row child names its columns: `Americas: 2025 = $178,353; Change = 7%; 2024 = $167,045`. Every child carries its subsection's heading path.

### 3.3 Agentic

An LLM reads each Item's text, a window of about 600 tokens at a time, and picks where the next chunk starts.

Two rules are enforced in code, because the LLM breaks them on its own:

| Rule | Without it |
|---|---|
| Each risk factor is chunked on its own, and kept whole when ≤ 600 tokens | The LLM merged two adjacent credit-risk factors |
| No chunk ends on a heading | 13 heading-only chunks ("General Risks", "Note 2 – Revenue") |

---

## 4. Evaluation

Three criteria, each scored against the ground truth.

| # | Criterion | Metrics | Good |
|---|---|---|---|
| 1 | Every chunk knows where it came from | 1a: Item right · 1b: path exact / merged / coarse / wrong · 1c: headings found by preprocessing (precision / recall / level) | 100% · ↑ exact + merged, 0 wrong · 1.0 / 1.0 / 100% |
| 2 | One risk factor per chunk | 2a: purity (Item 1A chunks with one risk factor) · 2b: intact (risk factors ≤ target in one chunk) · 2c: fragmentation · 2d: later pieces carry the heading | 100% · 100% · 1.0 · 100% |
| 3 | Parent-child token ratio ≈ 5:1 | 3b: parent ÷ mean child tokens, for parents ≥ 80% of the cap | median 4–6 |

**Path classes (1b)**

| Class | Meaning |
|---|---|
| Exact | All text sits under one deepest heading, and the path names it |
| Merged | Whole sibling subsections; the path names their parent |
| Coarse | Part of a subsection mixed with other text; the path is their common ancestor |
| Wrong | The path is missing or names the wrong place |

### 4.4 Ground truth

A hand-checked answer key for the excerpt: its 5 Items, 156 headings with levels and paths, 27 risk factors, 39 tables with units, and 47 page footers.

- **Matching:** chunks are matched by text, not block id ([score.py](v0/v0_src/score.py)), so any method can be scored.
- **Building:** [v0_ground_truth.py](v0/v0_testdata/v0_ground_truth.py) drafts it from the HTML with styling rules (no code shared with preprocessing), and a person reviews it.

---

## 5. Results

All three methods ran on the same preprocessed document.

### Scores

| Method | 1a | 1b exact / merged / coarse / wrong | 2a | 2b | 2c | 2d | 3b |
|---|---|---|---|---|---|---|---|
| Rule-based | 100% | 93.7 / 0.5 / 1.6 / 4.2 | 100% | 100% | 1.01 | 100% | — |
| Parent-child | 100% | 93.2 / 0.6 / 2.3 / 4.0 | 100% | 100% | 1.00 | 100% | 5.5 |
| Agentic | 100% | 83.3 / 4.3 / 11.6 / 0.7 | 100% | 100% | 1.06 | 100% | — |

| Method | Chunks | Mean / p95 / max tokens |
|---|---|---|
| Rule-based | 190 | 203 / 547 / 1,036 |
| Parent-child | 177 parents; 195 prose, 38 table-title and 309 row children | 216 / 671 / 1,241 |
| Agentic | 138 | 286 / 580 / 1,036 |

**Preprocessing (1c):** heading precision 94.0%, recall 100%, level 99.4%. All 5 Items were found.

### Agentic variants

| Variant | 2a | 2b | Path exact + merged | Heading-only chunks |
|---|---|---|---|---|
| Slumber as shipped | 97.4% | 95.5% | 88.4% | 13 |
| 10-K prompt only | 90.9% | — | 86.9% | 5 |
| **Two rules in code (adopted)** | **100%** | **100%** | **87.6%** | **0** |
| Rules in code + 10-K prompt | 100% | 100% | 85.9% | 0 |

A 10-K-specific prompt didn't stop the LLM merging risk factors. Only the rules in code did.

### Takeaways

- **All three get the basics right.** Every chunk cites the right Item, all 38 tables are whole with units, and coverage is ≥ 99.3%.
- **All three keep risk factors apart.** For agentic, that's only because code sets the risk-factor boundaries.
- **Path errors come from preprocessing, not chunking.** Rule-based and parent-child cut at the agent's 10 false headings, e.g. "Apple Inc." above each statement. Agentic skips weak headings, so it's rarely wrong (0.7%), but it skips real ones too, so it's often coarse (11.6%).
- **The 5:1 ratio rarely applies.** Most parents are cut short by a subsection: 101 of 139 prose parents have one child.
- **Rule-based and parent-child are level.** Parent-child adds row children for every table. Agentic costs an LLM call per chunk and gives coarser paths.
