# V0 · Parsing and chunking

**Goal:** the best way to turn a 10-K into chunks for retrieval, judged by criteria that work for any filer.

| Stage | What happens | Section |
|---|---|---|
| Preprocessing | HTML → one structured document. Code for what's universal, an LLM agent for the rest | §2 |
| Chunking | Three methods on that document: rule-based, parent-child, agentic | §3 |
| Evaluation | Three criteria, scored against a hand-reviewed ground truth | §4 |

**Status:** implemented and run on the AAPL excerpt. Code: [experiments/v0/](v0/). Commands and results: §5.

**At a glance** (details in §5)
- Rule-based and parent-child are level: perfect on risk factors, ~93% exact paths.
- Agentic matches them on risk factors only with two rules in code. Its paths are coarser (83% exact).
- The remaining path errors come from preprocessing (false headings), not chunking.

**Tokens** are counted with the `Qwen/Qwen3-Embedding-0.6B` tokenizer, the embedder's. On this excerpt, 1 token ≈ 4.3 chars.

**Contents:** [1 Test data](#1-test-data) · [2 Preprocessing](#2-preprocessing-html--structured-document) · [3 Chunking](#3-chunking) · [4 Evaluation](#4-evaluation) · [5 Results](#5-results) · [6 10-K traits](#6-10-k-traits)

---

## 1. Test data

**File:** [aapl-fy25-excerpt.html](V0_testdata/aapl-fy25-excerpt.html). 1.15 MB HTML, ~166k chars, 38.5k tokens.

- Two verbatim slices of the AAPL FY2025 10-K (accession 0000320193-25-000079), in filing order.
- Each slice runs from one Item heading up to the next.
- Items 1B–6 are left out, so Item 1A runs straight into Item 7.

| Slice | Items | Pages | Tokens | Headings | Tables | Footers |
|---|---|---|---|---|---|---|
| 1 | 1, 1A | 1–16 | 2,813 + 11,455 | 21 + 32 | 0 | 16 |
| 2 | 7, 7A, 8 | 21–51 | 4,742 + 610 + 18,849 | 37 + 2 + 64 | 38 data + 1 index | 31 |

**Structure inside**
- Item 1: 12 bold subheadings, 9 italic ones below them.
- Item 1A: a preamble, 5 bold categories, 27 bold-italic risk factors.
- Items 7–8: MD&A subheadings, the five financial statements, Notes 1–13, both auditor reports.

**Tables:** 41 `<table>`s in the HTML.
- 38 data tables
- 1 "Index to Consolidated Financial Statements"
- 2 that hold the auditor's critical audit matter as prose

**Cover tags:** the excerpt has no cover page, so the `dei:` tags (§2.1) come from the full filing.

**Ground truth:** [aapl-fy25-excerpt.gt.json](V0_testdata/aapl-fy25-excerpt.gt.json), reviewed (§4.4).

---

## 2. Preprocessing: HTML → structured document

```
HTML ─► D1–D8 (code) ─► blocks + features ─► LLM labels per window ─► validate ─► repair ─► assemble ─► document (JSON)
                                                     ▲                      │                                 │
                                         v0_structure/system.md      violations → repair template   adapters per chunker (§2.4)
```

1. **Code (§2.1)** splits the HTML into blocks and reads features. Only rules that hold for every 10-K.
2. **The structure agent (§2.2)** labels every block: Item, heading, table, noise…
3. **Code** checks the labels, the LLM repairs mistakes, and code assembles the document (§2.3).

### 2.1 Deterministic layer (code)

A rule belongs here only if it follows from the HTML spec, how EDGAR stores filings, or an SEC markup rule. Never from a filer's styling or wording.

| # | Rule | Why it's universal |
|---|---|---|
| D1 | Parse with BeautifulSoup + lxml; drop `display:none` elements | Inline XBRL hides its facts header (`ix:header`) |
| D2 | **Inline XBRL:** unwrap facts, record text-block spans | SEC Rule 405 requires every note, policy and note table to be block-tagged |
| D3 | **Filing metadata** from the `dei:` cover tags | Required tagging; fixes the fiscal year without guessing ([D-004](../docs/DECISIONS.md#d-004)) |
| D4 | Text: decode entities, `&nbsp;` → space, collapse whitespace, strip | HTML |
| D5 | **Blocks:** split the page into paragraphs and tables | HTML structure |
| D6 | **Split cells:** `$` / `(` / `$(` join the next cell; `)`, `%`, `)%` the previous. `( 7,763 )` → `(7,763)` | Lossless; every filing agent's HTML does it |
| D7 | **Features per block** for the LLM, then drop all styles | Reads the markup, decides nothing |
| D8 | **Page** of each block: 1 + the page breaks before it. Metadata only | Lets a chunk cite where it came from |

**D2 (inline XBRL)**
- Every `ix:*` fact is unwrapped: the text stays, the tag goes.
- Each `*TextBlock` is recorded first as a span (note, policy or table), followed through `ix:continuation`.
- Each block gets `xbrl` = the text block it sits in. The block where one starts gets the feature `xbrl=note/policy/table`.
- These are **hints, not boundaries.** Names vary by filer, and a note can carry two tags (AAPL: 16 note tags for 13 notes).

**D3 (cover tags):** `TradingSymbol`, `DocumentFiscalYearFocus`, `DocumentPeriodEndDate`, `EntityRegistrantName`, `DocumentType` (must be `10-K`). Accession and URL come from the EDGAR manifest.

**D5 (blocks)**
- A tag with no block-level descendants (`div p li ul ol h1–h6 table center`) is one paragraph.
- Loose text next to block tags is its own paragraph.
- Every `<table>` is one table block (rows × cells, empty cells dropped). Data or layout is the LLM's call.
- Ids `b0001…` in reading order.

**D7 (features)**

| Feature | Read from |
|---|---|
| `emph` | Bold (`font-weight` ≥ 600, `<b>`, `<strong>`), italic (`font-style:italic`, `<i>`, `<em>`); `runin:N` when only the first N chars are emphasized |
| `chars` | Text length |
| `item_label` | Text starts with an Item label (`^items?\s+\d{1,2}[a-c]?\b`) |
| `xbrl` | D2 |
| `pb` | A page break follows |
| `link` | The text is an `<a href="#…">` |

Emphasis is the main heading signal (0 of 24 filings use `<h1–h6>`). Deciding that it *means* "heading" is the LLM's job.

**Where `parse.py`'s rules went**

| Old rule | Now |
|---|---|
| P1 blocks, P2 hidden, P3 whitespace, P6 split cells | D5, D1, D4, D6 |
| P4 data vs layout table, P5 TOC rows | LLM: `data_table` / `layout_table` / `index_table`, `toc` |
| P7 Item headings, P8 cover, P10 duplicate Items | LLM: `item_heading`, `cover`, `toc`; validator V2 |
| P9 noise | LLM: `furniture`, `part_heading` |
| P11 statements outside Item 8 | Assembly A3 |
| P0, P12 (proposed emphasis rules) | D7 features + LLM: `heading`, `level`, `kind`, `run_in` |

**Libraries considered** (2026-10-18, on the excerpt and the 8 FY2025 filings)

| | [sec-parser](https://github.com/alphanome-ai/sec-parser) 0.58.1 | [edgartools](https://github.com/dgunning/edgartools) 5.62.0 |
|---|---|---|
| Status | Unmaintained since June 2024; 10-Q only; pins `lxml<6` | Active; MIT; `lxml>=6.1`; heavy dependencies |
| Items | Items 1–6 only: no 1A, 7, 7A, 8 | **All 23 Items in 8/8 filings.** Excerpt: 5/5 + a false "9A" |
| Headings | 113/140; Item 8 levels upside-down | 56 of 156; levels nearly all 3 |
| Risk factors | 0/27 | 0/27 |
| Tables | 38/38 + index | 41 (every `<table>`) |
| Footers dropped | 47/47 | 0/47 |

**Verdict:** both fail on headings, levels and risk factors, where the criteria are decided. The custom layer stays. edgartools' Item splitting works, but `item_heading` + validator V2–V3 cover it.

### 2.2 Structure agent (LLM)

**Stack**
- Plain `openai` client → vLLM `Qwen/Qwen3.6-27B-FP8` (`config.yaml`: `model`, `base_url`).
- Temperature 0, thinking off.
- vLLM guided decoding forces the labels schema.
- No framework: one schema-constrained call per window plus a validator loop.

**Prompt and schema** (files; code never builds prompt text inline)

| File | What |
|---|---|
| [v0_structure/system.md](v0_structure/system.md) | **The prompt.** Above `END SYSTEM PROMPT`: the system prompt, using the ground truth's definitions (§4.4). Below: the **window** and **repair** templates |
| [v0_structure/labels.schema.json](v0_structure/labels.schema.json) | The output format. vLLM enforces it, so every reply is valid JSON |

**The LLM labels; code builds the document**
- One label per block: `{"id": "b0412", "role": "heading", "level": 2, "kind": "risk_factor"}`.
- Code applies the labels to the filing's own text (§2.3).
- Why not let the LLM write the document?
  - *Exact text:* citations need the filing's own words and figures.
  - *Cost:* a label is a few tokens; a rewrite would be tens of thousands.

**Input and windows**
- One line per block: `id | features | text`.
- Text over 300 chars: first 200 + last 60 chars.
- Tables ≤ 6 rows shown whole; longer ones as first 3 rows + last row.
- ~150 blocks per call.
- Context per call: the open Item, the open heading path, the previous window's last 10 labels.
- The template repeats the output rules after the blocks, next to where the model starts writing.

**Labels**

| `role` | Extra fields |
|---|---|
| `item_heading` | `item` |
| `heading` | `level`, `kind`, `run_in` |
| `data_table` | `units`, `units_from` |
| `layout_table` | `rows` (one label per row) |
| `body`, `cover`, `toc`, `part_heading`, `index_table`, `furniture` | — |

- **Heading `kind`:** `risk_summary`, `risk_category`, `risk_factor`, `statement`, `notes_header`, `note`, `auditor_report`, `critical_audit_matter`, `other`.
- **Any label** can add `uncertain: true`, which sends it to review.

**Validate → repair**
- Code checks every window's labels; the LLM fixes violations, at most 2 rounds.
- Still failing: the block keeps its last label and goes to `review`.
- Fields that don't belong to a role are dropped, not repaired.

| # | Check | Source |
|---|---|---|
| V1 | One label per block, same order, exact ids | Output format |
| V2 | `item_heading` Items valid, in Form 10-K order, each once | Form 10-K |
| V3 | A full filing has every required Item (1, 1A, 1B, 1C from FY2023, 2–9C, 10–15). Not checked on an excerpt | Form 10-K |
| V4 | Fields match the role (`level` only on headings, …) | Output format |
| V5 | Heading levels start at 1 in each Item, never jump by more than 1 | system.md |
| V6 | `risk_*` kinds only in 1A; `statement` / `note` / `notes_header` only in Item 8 (or where A3 moves them) | system.md |
| V7 | A block where an XBRL note starts is, or directly follows, a `note` heading | Rule 405 tagging |

**Assembly** (code)

| # | Rule |
|---|---|
| A1 | A `run_in` heading splits at D7's N into `b0412.h` (heading) + `b0412.b` (body). Text unchanged |
| A2 | Sections start at each `item_heading`. `cover`, `toc`, `part_heading`, `furniture`, `index_table` are dropped (counted). `layout_table` rows become headings or paragraphs |
| A3 | If Item 8 has no `statement`: the span from the first `statement` / `notes_header` / `auditor_report` to the next Item moves into Item 8 (NVIDIA, Netflix) |
| A4 | Every block gets a `heading_path`; a heading's path includes itself |
| A5 | Every block gets a `printed_page` from its page's `furniture` ("… \| 12", "F-3"), or null |

**Open question:** does one prompt work on every filer's styling (NVDA, NFLX, MSFT in §6)?

### 2.3 Output: the structured document

One JSON file per filing, built by code (A1–A5), never by the LLM.

| Field | What |
|---|---|
| `filing` | `ticker`, `registrant`, `fiscal_year`, `period_end` (D3); `accession`, `source_url` |
| `sections[]` | One per Item, in order: `item`, `title`, `blocks[]` |
| `dropped` | Counts of dropped blocks, by role |
| `review` | Ids marked `uncertain` or left unrepaired |

| Block field | On | What |
|---|---|---|
| `id` | all | From D5; a run-in split adds `.h` / `.b` |
| `kind` | all | `heading`, `paragraph` or `table` |
| `text` | all | The filing's own text; tables as `\| a \| b \|` lines |
| `heading_path` | all | Headings above the block; a heading includes itself |
| `page`, `printed_page` | all | D8's count; A5's printed number (e.g. "F-3") or null |
| `xbrl` | all | The XBRL text block it sits in, or null |
| `level`, `heading_kind` | headings | From the labels |
| `rows`, `units`, `units_from` | tables | Cells; units from the labels |

```json
{
  "filing": {"ticker": "AAPL", "registrant": "Apple Inc.", "fiscal_year": 2025, "period_end": "2025-09-27", "accession": "0000320193-25-000079"},
  "sections": [
    {"item": "1A", "title": "Risk Factors", "blocks": [
      {"id": "b0391", "kind": "heading", "text": "Business Risks", "level": 1, "heading_kind": "risk_category", "heading_path": ["Business Risks"]},
      {"id": "b0398", "kind": "heading", "text": "The Company depends on component and product manufacturing …", "level": 2, "heading_kind": "risk_factor", "heading_path": ["Business Risks", "The Company depends on …"]},
      {"id": "b0399", "kind": "paragraph", "text": "Substantially all of the Company's manufacturing is performed …", "heading_path": ["Business Risks", "The Company depends on …"]}
    ]}
  ],
  "dropped": {"furniture": 47, "index_table": 1},
  "review": []
}
```

Block ids carry through, so every chunk maps back to blocks, and every claim to the filing.

### 2.4 From the document to each chunker

| Chunker | Reads |
|---|---|
| R (rule-based) | The document's sections and blocks directly |
| P (parent-child) | One LlamaIndex `Document` per subsection (heading + its paragraphs) |
| A (agentic) | One Markdown text per Item: Item as `#`, heading level n as `#`×(n+1), tables as `[TABLE n]`. A renderer records each block's character span, so chunk offsets map back to blocks |

### 2.5 Shared tagging: chunks → metadata

All three use the same step, so criterion 1 is computed the same way for all.

| # | Rule |
|---|---|
| T1 | Every chunk records `item`, `item_title`, `heading_path`, `kind` (prose / table), `block_ids`, `pages`, `printed_pages`, `tokens`. Tables add `units` and `years_covered` |
| T2 | `heading_path` = the deepest path shared by **all** the chunk's blocks. Inside one risk factor: `[category, risk factor]`. Across two: `[category]` |
| T3 | IDs: `<TICKER>-FY<year>-<item>-<seq>`, e.g. `AAPL-FY2025-1A-007` |

---

## 3. Chunking

| ID | Method | Stack | LLM? | Size |
|---|---|---|---|---|
| R | Rule-based | Own code (C-rules below) | no | ≤ 600 tokens |
| P | Parent-child | LlamaIndex `HierarchicalNodeParser` | no | Parents ≤ 1,280, children ≤ 256 |
| A | Agentic | Chonkie `SlumberChunker` + `Qwen3.6-27B-FP8` | yes | ≤ 600 tokens |

5 of the 27 risk factors are over 600 tokens (max 1,241), so R and A must split them.

**Shared by all three** ([chunkers.py](v0/chunkers.py))
- **C1:** no chunk crosses an Item.
- **Tables (C2–C4, C8):** each data table is its own chunk, with its caption. Same function for all three.
- **Metadata and ids:** T1–T3.
- **Settings:** [config.yaml](v0/config.yaml) `chunking:`. `run.py` refuses to score chunks made with other settings.

### 3.1 Rule-based

A new chunk at every heading; prose packed up to 600 tokens.

| # | Rule | Default |
|---|---|---|
| C1 | Chunks never cross Items | |
| C2 | Each data table is **one chunk**, as `\| cell \| cell \|` lines. Never split mid-row ([D-003](../docs/DECISIONS.md#d-003)) | |
| C3 | **Caption** = up to 2 short blocks right before a table. It's only in the table's chunk; prose leaves it out | ≤ 300 chars each |
| C4 | A long table splits into row groups. Each repeats the caption + **header rows** (rows before the first number or group label; the first row if there are no numbers) | 7,200 chars (never triggered; max 4,747) |
| C5 | A paragraph over the target splits at sentence ends (`.` or `;` + space), **not after an abbreviation** (U.S., Inc., No., e.g. …) | |
| C6 | Paragraphs are packed greedily up to the target | 600 tokens |
| C7 | A new chunk repeats the previous chunk's last paragraph, if it's short and still fits | ≤ 600 chars |
| C8 | `years_covered` (`20[0-3]\d`) from the table's top 6 lines | |
| C10 | **A heading always starts a new chunk.** C6 and C7 never cross one. Every later piece of a subsection starts with its heading line | |

**Why C10:** without it, C6 packs several risk factors into one chunk.

**Details**
- Headings with nothing between them (a category, then its first risk factor) open one subsection together.
- A heading over only tables makes no prose chunk; it's in the tables' captions.
- Prose before and after a table is packed separately.
- Long paragraphs split to leave room for the heading lines.

### 3.2 Parent-child

Search matches small **children**; the model reads and cites the larger **parent**.

**Why LlamaIndex, not LangChain:** LlamaIndex's parser runs on its own and keeps metadata and links at every level. LangChain's `ParentDocumentRetriever` needs a vector store and a docstore just to produce chunks.

| | Parents | Children |
|---|---|---|
| Size | ≤ 1,280 tokens: holds every AAPL risk factor whole | ≤ 256 tokens (5:1, criterion 3) |
| Splitter | `SentenceSplitter`, no overlap | `SentenceSplitter`, 20-token overlap |
| Boundary | Never crosses a subsection | Within the parent |
| Tables | Each table is its own parent | A `table_title` + one `row` per data row |

**Children**

| `kind` | Text | Example |
|---|---|---|
| `prose` | A sentence-split window of the parent | — |
| `table_title` | Caption + header rows | `Segment Operating Performance / The following table shows… / \| 2025 \| Change \| 2024 \|…` |
| `row` | One row, naming its columns | `Americas: 2025 = $178,353; Change = 7%; 2024 = $167,045; …` |

- A value-less row ("Federal:") is a group label. It prefixes the rows below it (`Federal › Current: …`) until a "Total" row.
- Every child carries its subsection's `heading_path`. Most children's text has no heading, so the embedder needs it.

**Details**
- Both splitters count Qwen3 tokens and cut sentences as C5 does. The default punkt splitter cuts at "U.S. Tariffs".
- A parent's `block_ids` and pages are the blocks its text spans.
- Captions (C3) are left out of prose parents.
- `AutoMergingRetriever` (return the parent when enough children match) runs at query time, not here.

**Scoring:** criteria 1 and 2 on parents (what gets cited); 2d on children (what gets embedded).

### 3.3 Agentic

An LLM reads the text and picks where each chunk ends.

**How Slumber works**
1. Split each Item's Markdown (§2.4) into candidates: paragraphs, then sentences, then words, each ≤ 128 tokens.
2. Show the LLM a window of candidates, ~600 tokens, labelled `ID n`.
3. The LLM returns `split_index`: the first passage of the next chunk. The window is the most a chunk can hold.
4. The next window starts there.
5. Chunk offsets map back to blocks; each `[TABLE n]` becomes its table's chunk (C2).

**Two rules in code** (the LLM can't break them)

| Rule | Without it |
|---|---|
| Each risk factor, with the category heading directly above it, is chunked on its own. One ≤ 600 tokens stays whole, without an LLM call | The LLM merged RF24 + RF25 (both on credit risk) |
| No chunk ends on a heading. A cut right after a heading moves back before it | 13 heading-only chunks ("General Risks", "Note 2 – Revenue") |

**Settings**
- `OpenAIGenie` subclass: temperature 0, thinking off, schema-constrained JSON. The stock genie can't set these.
- `min_characters_per_chunk=1`. The default 24 merges short headings into the previous candidate.
- Slumber's own prompt. A 10-K-specific prompt was tried and rejected (§5).

---

## 4. Evaluation

- A method is good when it passes every criterion that applies to it.
- Each criterion can be scored on any filing with a reviewed ground truth (§4.4).

| # | Criterion | Applies to |
|---|---|---|
| 1 | Every chunk knows where it came from | All |
| 2 | One risk factor per chunk | All |
| 3 | Parent-child token ratio ≈ 5:1 | P |

### 4.1 Criterion 1: every chunk knows where it came from

Each chunk carries `item`, `item_title`, and a `heading_path` down to every level, e.g. `Notes › Note 4 – Financial Instruments › Derivative Instruments and Hedging › Interest Rate Risk`.

| Metric | Definition | Good |
|---|---|---|
| 1a. Item | % of chunks whose `item` matches the ground truth for all their text | **100%** (less fails) |
| 1b. Path | Each chunk is exact, merged, coarse or wrong (below) | ↑ exact + merged, 0 wrong |
| 1c. Headings | Document headings vs ground truth: precision / recall / % right level. Checks preprocessing | 1.0 / 1.0 / 100% |

| 1b class | Meaning |
|---|---|
| **Exact** | All text sits under one deepest heading, and the path names it |
| **Merged** | Whole sibling subsections (all of *iPhone* and *Mac*); the path names their parent. Correct |
| **Coarse** | Part of a subsection mixed with other text; the path is their common ancestor |
| **Wrong** | The path is missing or names the wrong place |

### 4.2 Criterion 2: one risk factor per chunk

**Units of Item 1A** (ground truth `risk_factors`)

| Unit | Definition (any 10-K) | AAPL |
|---|---|---|
| Risk factor | A heading naming one risk (sentence or title; bold, italic or run-in) + its body | 27; 129–1,241 tokens, median 331 |
| Category | A heading grouping risk factors ("Business Risks"). Part of the path, not a unit | 5 |
| Summary | A "Summary of risk factors" list (META, NVDA). Its own unit | none |
| Preamble | Text before the first heading. Its own unit | 180 tokens |

| Metric | Definition | Good |
|---|---|---|
| 2a. Purity | % of Item 1A chunks with text from **one** unit | **100%** |
| 2b. Intact | % of risk factors within the target that sit in one chunk | 100% |
| 2c. Fragmentation | Mean of chunks used ÷ ⌈tokens / target⌉ | 1.0 |
| 2d. Heading carried | % of a risk factor's later chunks that carry its heading (text or `heading_path`) | 100% |

- **Target:** 600 tokens (22 of 27 risk factors fit); 1,280 for parent-child parents (all 27 fit).
- **Parent-child:** 2a–2c on parents, 2d on children (parents never split a risk factor, children do).

### 4.3 Criterion 3: parent-child ratio ≈ 5:1 (P only)

Boundaries win over the ratio: a parent never crosses a subsection, even if that leaves it small.

| Metric | Definition | Good |
|---|---|---|
| 3a. Target | Parent cap ÷ child cap (config) | 5.0 |
| 3b. Full parents | Prose parents ≥ 80% of the cap: parent tokens ÷ mean child tokens | Median 4–6; ≥ 80% of them in 4–6 |
| 3c. Small parents | % of prose parents under 80% of the cap. Reported, not scored | |

- Tables are exempt: their children are rows.
- No AAPL risk factor reaches 1,280 tokens, so 3b is measured on Items 1, 7 and 8.

### 4.4 Ground truth

A hand-checked answer key for one filing, independent of any method. It records:
- the Items
- every heading, with its level and path
- each risk factor
- each table and its units
- the page furniture

Without it, "is this path right?" and "does this chunk mix two risk factors?" are opinions.

**How it's used** (matched **by text, not block id**, so it scores any method)

| Criterion / check | Compares | Against |
|---|---|---|
| 1a, 1b | Each chunk's `item`, `heading_path` | `items[].headings[].path` |
| 1c | The document's headings and levels | `items[].headings` |
| 2a–2d | Which units each 1A chunk's text comes from | `risk_factors.units` |
| §4.5 checks | Tables, footers, coverage | `tables`, `furniture`, Item `ends_with` |

**How the scorer matches** ([score.py](v0/score.py))
- The text stream is rebuilt with the ground-truth builder's block reader, not the preprocessing.
- Each stream block gets its Item, path and risk-factor unit from the reviewed headings.
- Text is compared as alphanumeric-only keys: `$1,234` = `$ | 1,234`.
- Each chunk is pinned by its rarest line; every other line takes the nearest occurrence. Repeated rows and titles land in the right place.
- Lines under 8 key characters ("2024", "Level 1:") are too ambiguous and don't count toward coverage.
- A short label repeated over tables ("Apple Inc.") is path-neutral in 1b.

**How it's built**
1. **Draft:** [v0_ground_truth.py](V0_testdata/v0_ground_truth.py) reads any 10-K HTML. It uses styling rules on purpose and shares no code with the preprocessing, so it can't inherit the agent's mistakes.
2. **Review:** a person checks the draft against the rendered filing. Fix levels, drop false headings, add missed ones. Record each fix in `review`, then set `"status": "reviewed"`.

```bash
python -I experiments/V0_testdata/v0_ground_truth.py experiments/V0_testdata/aapl-fy25-excerpt.html \
    --out experiments/V0_testdata/aapl-fy25-excerpt.gt.json
```

**Definitions (any 10-K).** [system.md](v0_structure/system.md) gives the LLM the same ones.

| Thing | Is | Is not |
|---|---|---|
| Item | A Part I–IV Item heading outside the TOC | "Item 5.02" cross-references; TOC rows |
| Heading | A line titling the text after it: fully emphasized, or the emphasized lead of a run-in paragraph; ≤ 300 chars | "Apple Inc." above each statement; row labels; long emphasized body text; furniture |
| Level | Nesting by meaning, not style | "Bold = level 1" |
| Risk factor, category, summary, preamble | §4.2 | |
| Data table | A table with a cell that is only a number | Layout tables; the "Index to … Financial Statements" |
| Units | The "(in millions)" phrase in caption or header; else inherited from the previous table in the same note | |
| Furniture | Page footers, bare page numbers, "Table of Contents" | |

**Item 8 levels** (the same in every 10-K)
- Level 1: statements, "Notes to … Financial Statements", auditor reports
- Level 2: each "Note N"
- Level 3+: subheadings inside a note
- Critical audit matter topics sit under "Critical Audit Matter(s)"

**What the file holds**

| Key | Per entry | AAPL |
|---|---|---|
| `items` | `item`, `title`, `headings` (`level`, `style`, `text`, `path`), `chars`, `tokens`, `ends_with` | 5 Items, 156 headings |
| `risk_factors` | `preamble`; `units` (`kind`, `id`, `heading`, `category`, `path`, `ends_with`, `chars`, `tokens`) | 27 risk factors |
| `tables` | `id`, `item`, `kind`, `path`, `rows`, `first_row`, `units`, `units_from` | 38 data + 1 index |
| `furniture` | `count`, examples | 47 footers |

**Status:** AAPL excerpt reviewed; its `review` field lists what was checked.

**Drafts of other filers** (FY2025, unreviewed)

| Filer | Draft |
|---|---|
| AAPL, AMZN, GOOGL, TSLA | Right |
| MSFT | Run-in risk factors found |
| NFLX | Italic intro becomes a false level-1 heading |
| NVDA | Same-style categories and risk factors land on one level |

NFLX, NVDA and MSFT need hand review first. Styling rules fail there, so they're the real test of the structure agent.

### 4.5 Sanity checks (recorded, not criteria)

A method can't pass by dropping or duplicating text, or breaking tables.

| Check | Definition | Expect |
|---|---|---|
| Coverage | % of the ground truth's text in some chunk | ~100% |
| Duplication | Chunk tokens ÷ text tokens | ~1.0–1.3 |
| Noise | Furniture lines in any chunk | 0 |
| Tables | Each of the 38 data tables whole (or in C4 row groups), with units | 38 / 38 |
| Size | Chunk count; mean / p95 / max tokens | |

### 4.6 Manual review (optional, not in the results)

| ID | What | How |
|---|---|---|
| M1 | Blind chunk review | Item 1A's chunks from each method, unlabeled. Score each 1–3: one topic? Sensible start and end? Heading present? |

### 4.7 Sanity cases (small enough to check by hand)

No person has read all of the §4.4 ground truth (156 headings, 27 risk factors, 39 tables). So 21 cases cover each kind of chunk once; a person can check them in ~20 minutes.

**File:** [aapl-fy25-sanity.yaml](V0_testdata/aapl-fy25-sanity.yaml). Each case quotes a few unique words (`find`) and the printed page, and says what the chunk holding them must carry.

| Cases | Kind of chunk | Checks |
|---|---|---|
| S01–S08 | Prose under headings: levels 1, 2, 4; same heading text under different parents; an italic intro that isn't a heading; an auditor report; an Item boundary | `item`, `heading_path` |
| S09–S12 | Item 1A: the preamble; a short risk factor; two adjacent risk factors on one topic; a long one across a page break | Path; text it must and mustn't share a chunk with |
| S13–S19 | Tables: MD&A; no units; a full statement; units stated once; a continuation; thousands; text cells | `kind`, `units`, whole table, path |
| S20–S21 | Dropped: a page footer; the index table | In no chunk |

**Grades:** `ok`; `coarser` (the path is an ancestor of the expected one); `fail`.

**Run:** `python experiments/v0/sanity.py` (writes `sanity.json` next to the chunks). [test_v0_sanity.py](../tests/test_v0_sanity.py) checks that every quote is in the filing.

---

## 5. Results

The three methods on the **same preprocessing**, scored against the ground truth. Speed and cost aren't measured yet.

**Run it** (vLLM serving `Qwen/Qwen3.6-27B-FP8`: `scripts/serve_vllm.sh qwen3.6-27b-fp8`)

```bash
uv sync --all-extras --all-groups --link-mode copy                      # once (see below)
taskset -c 16-31 .venv/bin/python experiments/v0/run.py                 # preprocess → chunk → score
taskset -c 16-31 .venv/bin/python experiments/v0/run.py --stage chunk --method rule   # one stage / method
```

- **Outputs** in `output/chunking/<label>/`: `blocks.json`, `labels.json` (cached; `--relabel` to redo), `document.json`, `chunks-<method>.json`, `scores.json`.
- **`--link-mode copy`:** NLTK refuses LlamaIndex's stopwords when uv hard-links them.
- **Always `--all-extras --all-groups`:** a plain `uv sync --group experiments` removes the project's extras.

### Scores

| Method | 1a | 1b exact / merged / coarse / wrong | 2a | 2b | 2c | 2d | 3b (median · % in 4–6) | Coverage · noise · tables | Sanity ok / coarser / fail |
|---|---|---|---|---|---|---|---|---|---|
| Rule-based | 100% | 93.7 / 0.5 / 1.6 / 4.2 | 100% (35) | 100% (22/22) | 1.01 | 100% (7) | — | 99.3% · 0 · 38/38 | 20 / 1 / 0 |
| Parent-child | 100% | 93.2 / 0.6 / 2.3 / 4.0 | 100% (28) | 100% (27/27) | 1.00 | 100% (35) | 5.5 · 100% (2 full) | 99.4% · 0 · 38/38 | 20 / 1 / 0 |
| Agentic | 100% | 83.3 / 4.3 / 11.6 / 0.7 | 100% (38) | 100% (22/22) | 1.06 | 100% (10) | — | 99.3% · 0 · 38/38 | 15 / 6 / 0 |

Parentheses: chunks or risk factors scored. Sanity cases are a draft (0/21 checked by a person).

| Method | Chunks | Mean / p95 / max tokens | Notes |
|---|---|---|---|
| Rule-based | 190 | 203 / 547 / 1,036 | Prose ≤ 592 |
| Parent-child | 177 parents | 216 / 671 / 1,241 | 195 prose, 38 table-title, 309 row children; all ≤ 256 |
| Agentic | 138 | 286 / 580 / 1,036 | Prose ≤ 581 |

The 1,036-token maximum is a table, kept whole (C2).

**Shared preprocessing** (the same input for every row)
- **1c headings:** precision 94.0% · recall 100% · level 99.4% (166 found, 156 true).
- **Items:** 5 of 5. Dropped: 47 footers, 1 index table. Validator: 0 violations, empty review list.

### Agentic: what fixed it

| Variant | 2a | 2b | 2c | 1b exact + merged | Heading-only chunks | Prose median tokens |
|---|---|---|---|---|---|---|
| Slumber as shipped | 97.4% | 95.5% | 1.15 | 88.4% | 13 | 244 |
| 10-K prompt only | 90.9% | — | 1.06 | 86.9% | 5 | — |
| **Two rules in code (adopted)** | **100%** | **100%** | 1.06 | **87.6%** | **0** | 256 |
| Rules in code + 10-K prompt | 100% | 100% | 1.04 | 85.9% | 0 | 306 |

- **The 10-K prompt** explained the heading markup and added rules: keep headings with their text, a new chunk per risk factor, group sibling subsections.
- **On its own,** the LLM still merged risk factors it was told to keep apart.
- **With the code rules,** it only made chunks larger and paths less precise, with or without the grouping rule.

### Takeaways

**Basics: all three are solid**
- Every chunk cites the right Item.
- All 38 tables are whole, with units.
- No footers in any chunk; coverage ≥ 99.3%.

**Risk factors (criterion 2): all three are perfect**
- Rule-based and parent-child cut at every labeled heading; the agent found all 27 risk factors.
- Agentic gets there only because code sets the risk-factor boundaries.

**Paths (criterion 1): the errors come from preprocessing**
- R and P cut at the agent's 10 false headings: "Apple Inc." above each statement, "First/…/Fourth Quarter 2025:" in Item 7.
- One level error ("Segment Reporting") adds to that.
- Leaving captions out of prose (C3) dropped rule-based from 6.1% to 4.2% wrong.
- Agentic skips weak headings, so it's almost never wrong (0.7%). It skips some real ones too, so it's coarse more often (11.6%).

**5:1 ratio (criterion 3): holds, but rarely applies**
- The 2 full parents are 5.5:1.
- 99% of parents are cut short by a subsection; 101 of 139 prose parents have one child.
- On a 10-K, most parent-child pairs are the same text.

**Verdict on this excerpt**
- **Rule-based and parent-child are level.** Parent-child adds the 5:1 split where it applies, and row children for every table.
- **Agentic** costs an LLM call per chunk outside Item 1A and gives coarser paths. Its strength, fewer tiny chunks in Items 7 and 8, isn't scored by any criterion.

**Next**
- **Preprocessing is the biggest lever.** The prompt already says the company-name label isn't a heading; the model still made it one. A validator check could catch short headings repeated ≥ 3 times over tables.
- **A second filer** with harder styling (NVDA, NFLX, MSFT), once its ground truth is reviewed.

**Ground-truth fix found while scoring:** T04 (gross-margin percentages) had borrowed T03's "in millions". Corrected and recorded in `review`.

---

## 6. 10-K traits

Facts about the filings themselves.

### Known (now in the D-rules or system.md)

- The TOC is a multi-row table of "Item N." lines, usually links (`toc`, D7 `link`).
- Some filers put each Item heading in its own **one-row** table (`layout_table` rows).
- One number can span 2–3 `<td>`s: `$` | `1,234`, `(3` | `)%` (D6).
- "Item 5.02"-style cross-references look like headings.
- Headers, page numbers and "Form 10-K | 22" footers repeat on every page (`furniture`).
- Item 8 can be a one-line pointer. NVIDIA and Netflix put the statements under Item 15 (A3).
- Units differ by filer (Netflix in thousands; per-share in dollars). They sit in the caption or the preceding paragraph.
- Table columns are dates, not fiscal-year labels. Alphabet and Amazon run oldest → newest.
- The same figure appears in many places: "Total net sales" is in 13 AAPL chunks.
- **0 of 24** filings use `<h1–h6>`; headings are styled `<div>` / `<span>`.

### New

**Risk-factor headings by filer (FY2025)**

| Filer | Categories | Risk factors | Extra |
|---|---|---|---|
| AAPL, AMZN, GOOGL, TSLA | bold | bold-italic | AMZN headings are Title Case |
| META | bold | bold-italic | "Summary of Risk Factors" list |
| MSFT | bold, then bold-italic subcategories | **run-in**: bold first sentence of the body | three levels |
| NFLX | — | bold | italic intro sentence |
| NVDA | bold | bold (same as categories) | summary list; italic intro |

**Headings and styling (AAPL)**
- Risk-factor headings are full sentences (58–284 chars, ending in ".").
- Items 1 and 7: **bold** subsections, *italic* below them.
- Item 8: note titles and their subheadings are both bold.

**Tables and units (AAPL)**
- Units are sometimes stated once per note (Note 6), or only on a continued table's first part (Note 13).
- The critical audit matter is prose in a 2-column table. The old P4 rule called it a data table.
- Items 1 and 1A have no data tables.

**Markup**
- **Inline XBRL:** every note is block-tagged, but not one tag per note.
  - AAPL: 16 note tags for 13 notes, plus 11 policy and 21 table text blocks.
  - The `dei:` cover tags give ticker, fiscal year, period end and registrant.
- **Page breaks aren't universal,** so they're a feature (`pb`), not a rule:
  - AAPL: one per page (47, matching its 47 footers)
  - MSFT: 101
  - NVDA, NFLX: 1

### Not covered by the AAPL excerpt

- The NVDA, NFLX and MSFT risk-factor styles (drafts need review)
- TSLA's long risk-factor lists
- NVIDIA's statements under Item 15
- Netflix's thousands
- Alphabet's oldest → newest columns
- MSFT segment tables with group headers
