# V0 · Parsing and chunking

**Goal:** find the best way to turn a 10-K into chunks for retrieval, judged by criteria that work for any filer, not just Apple.

| Stage | What happens | Section |
|---|---|---|
| Preprocessing | HTML → one structured document: code for what's universal, an LLM agent for the rest | §2 |
| Chunking | One of three options on that document: rule-based, parent-child, agentic | §3 |
| Evaluation | Three criteria, scored against a hand-reviewed ground truth | §4 |

**Status:** implemented and run on the AAPL excerpt. Code: [experiments/v0/](v0/) (§5 has the commands); results in §5.

**Tokens** are counted with the `Qwen/Qwen3-Embedding-0.6B` tokenizer (the embedder, since children are what it embeds). On this excerpt, 1 token ≈ 4.3 chars.

**Contents:** [1 Test data](#1-test-data) · [2 Preprocessing](#2-preprocessing-html--structured-document) · [3 Chunking](#3-chunking) · [4 Evaluation](#4-how-do-we-know-the-doc-is-chunked-well) · [5 Results](#5-results) · [6 10-K traits](#6-10-k-traits)

---

## 1. Test data

**File:** [aapl-fy25-excerpt.html](V0_testdata/aapl-fy25-excerpt.html)

- 1.15 MB HTML, ~166k chars, 38.5k tokens
- Two verbatim, unedited slices of the AAPL FY2025 10-K (accession 0000320193-25-000079), in filing order
- Each slice runs from one Item heading to the next (exclusive)
- Items 1B–6 are left out, so Item 1A runs straight into Item 7

| Slice | Items | Pages | Tokens | Headings | Tables | Footers | Structure inside |
|---|---|---|---|---|---|---|---|
| 1 | 1, 1A | 1–16 | 2,813 + 11,455 | 21 + 32 | 0 | 16 | Item 1: 12 bold subheadings, 9 italic below them. Item 1A: a preamble, 5 bold categories, 27 bold-italic risk factors |
| 2 | 7, 7A, 8 | 21–51 | 4,742 + 610 + 18,849 | 37 + 2 + 64 | 38 data + 1 index | 31 | MD&A subheadings, the five financial statements, Notes 1–13, both auditor reports |

**Tables:** the HTML has 41 `<table>`s.
- 38 data tables
- 1 "Index to Consolidated Financial Statements" (page numbers)
- 2 that hold the auditor's critical audit matter as prose

**Cover tags:** the excerpt has no cover page, so its `dei:` cover tags (§2.1) come from the full filing.

**Ground truth:** [aapl-fy25-excerpt.gt.json](V0_testdata/aapl-fy25-excerpt.gt.json), reviewed. What it is and how it's used: §4.4.

---

## 2. Preprocessing: HTML → structured document

```
HTML ─► D1–D7 (code) ─► blocks + features ─► LLM labels per window ─► validate ─► repair ─► assemble ─► document (JSON)
                                                     ▲                      │                                 │
                                         v0_structure/system.md      violations → repair template   adapters per chunker (§2.4)
```

Two steps, run in order:

1. **Code (§2.1):** only rules that hold for every 10-K. It splits the HTML into blocks and reads features.
2. **LLM structure agent (§2.2):** labels every block (Item, heading, table, noise…). Code checks the labels, the LLM repairs mistakes, and code assembles the document (§2.3).

### 2.1 Deterministic layer (code)

A rule belongs here only if it follows from one of these, never from a filer's styling or wording:
- the HTML spec
- how EDGAR stores filings
- an SEC rule that makes the filer put something in the markup

| # | Rule | Why it's universal |
|---|---|---|
| D1 | Parse with BeautifulSoup + lxml; drop `display:none` elements | The inline-XBRL spec puts the hidden facts header (`ix:header`) in a hidden element |
| D2 | **Inline XBRL:** unwrap facts, record text-block spans (details below) | SEC Rule 405 (Reg S-T) requires inline XBRL, with every note, policy and note table block-tagged |
| D3 | **Filing metadata** from the `dei:` cover tags | Cover-page tagging is required; fixes the fiscal year without guessing ([D-004](../docs/DECISIONS.md#d-004)) |
| D4 | Text: decode entities, `&nbsp;` → space, collapse whitespace, strip | HTML |
| D5 | **Blocks:** split the page into paragraphs and tables (details below) | HTML structure |
| D6 | **Split cells:** `$` / `(` / `$(` join the next cell; `)`, `%`, `)%` join the previous one. `( 7,763 )` → `(7,763)` | Lossless (only cell boundaries move), and every filing agent's HTML does it |
| D7 | **Features per block** for the LLM, then drop all styles (details below) | Reading the markup, not deciding anything |

**D2 details (inline XBRL)**
- Every `ix:*` fact is unwrapped: the text stays, the tag goes.
- Before unwrapping, each `*TextBlock` (`ix:nonNumeric`, followed through `ix:continuation`) is recorded as a span: note, policy or table, by element name.
- Each block gets `xbrl` = the text block it sits in. The block where one starts gets the feature `xbrl=note/policy/table`.
- These are **hints, not boundaries:** names vary by filer, and a note can carry two tags (AAPL: 16 note tags for 13 notes).

**D3 details (cover tags):** `TradingSymbol`, `DocumentFiscalYearFocus`, `DocumentPeriodEndDate`, `EntityRegistrantName`, and `DocumentType` (must be `10-K`). Accession and URL come from the EDGAR manifest.

**D5 details (blocks)**
- A tag with no block-level descendants (`div p li ul ol h1–h6 table center`) is one paragraph.
- Loose text next to block tags is its own paragraph.
- Every `<table>` is one table block (rows × cells, empty cells dropped). Whether it holds data or is layout is the LLM's call.
- Ids `b0001…` in reading order.

**D7 details (features)**

| Feature | Read from |
|---|---|
| `emph` | `font-weight` ≥ 600, `<b>`, `<strong>` (bold); `font-style:italic`, `<i>`, `<em>` (italic); `runin:N` when only a leading run of N chars is emphasized |
| `chars` | Text length |
| `item_label` | Text starts with a Form 10-K Item label (`^items?\s+\d{1,2}[a-c]?\b`) |
| `xbrl` | D2 |
| `pb` | A page break follows |
| `link` | The text is an `<a href="#…">` |

Emphasis is the main heading signal (0 of 24 filings use `<h1–h6>`), but deciding that it *means* "heading" is the LLM's job.

**Where today's `parse.py` rules went**

| Old rule | Now |
|---|---|
| P1 (blocks), P2 (hidden), P3 (whitespace), P6 (split cells) | D5, D1, D4, D6 |
| P4 (data vs layout table), P5 (TOC rows can't be headings) | LLM: `data_table` / `layout_table` / `index_table`, `toc` |
| P7 (Item headings), P8 (drop cover), P10 (duplicate Items) | LLM: `item_heading`, `cover`, `toc`; validator: valid Items, in order, once each |
| P9 (noise) | LLM: `furniture`, `part_heading` |
| P11 (statements outside Item 8) | Assembly rule A3 |
| P0, P12 (proposed emphasis rules) | D7 features + LLM: `heading`, `level`, `kind`, `run_in` |

**Libraries considered** (2026-10-18; run on the excerpt against the ground truth and on the 8 FY2025 filings)

| | [sec-parser](https://github.com/alphanome-ai/sec-parser) 0.58.1 | [edgartools](https://github.com/dgunning/edgartools) 5.62.0 (`parse_html`, `form="10-K"`) |
|---|---|---|
| Status | "No longer maintained" (last release June 2024); 10-Q parser only; pins `lxml<6` (project has 6.1) | Active (450 releases); MIT; `lxml>=6.1`; heavy dependencies (pandas, pyarrow, httpx, pydantic, …) |
| Items | Only Items 1–6 (10-Q layout): no 1A, 7, 7A, 8 | **All 23 Items in 8/8 filings.** Excerpt: 5/5 plus a false "9A". Doesn't move NVDA/NFLX statements into Item 8 |
| Headings | 113/140; Item 8 levels upside-down | 56 found out of 156; levels nearly all 3 |
| Risk factors | 0/27 | 0/27 |
| Tables | 38/38 + index | 41 (every `<table>`) |
| Footers | 47/47 dropped | 0/47 dropped |

**Verdict:** both fail where the criteria are decided (headings, levels, risk factors), so the custom layer stays. edgartools' Item splitting works, but the agent's `item_heading` labels and validator V2–V3 cover the same ground.

### 2.2 Structure agent (LLM)

**Stack**
- Plain `openai` client → vLLM `Qwen/Qwen3.6-27B-FP8` (`config.yaml` → `model:`, `base_url`)
- Temperature 0, thinking off (`chat_template_kwargs`, as in `models.py`)
- Output forced into the labels schema by vLLM guided decoding
- No LangChain or LlamaIndex: one schema-constrained call per window plus a validator loop needs no framework

**Prompt and schema** (saved separately; the code reads them and never builds prompt text inline)

| File | What |
|---|---|
| [v0_structure/system.md](v0_structure/system.md) | **The prompt.** Above the `END SYSTEM PROMPT` marker: the system prompt (labels, Form 10-K Items, heading levels and kinds, tables and units, output format), using the same definitions as the ground truth (§4.4). Below it: two message templates the code fills, **window** (one call) and **repair** (violations → fixed labels) |
| [v0_structure/labels.schema.json](v0_structure/labels.schema.json) | The output format in machine-readable form. vLLM enforces it, so every reply is valid JSON with known fields |

**Two formats, on purpose**
- **The LLM outputs labels,** one per block: `{"id": "b0412", "role": "heading", "level": 2, "kind": "risk_factor"}`.
- **Code builds the document** (§2.3) by applying those labels to the filing's own text. That document is what every chunker reads (via §2.4).
- **Why not have the LLM write the document?**
  - *Exact text:* citations and verification need the filing's own words and figures; an LLM rewriting ~166k chars could change them.
  - *Cost:* a label is a few output tokens per block; a rewrite would be tens of thousands.

**Input and windows**
- One line per block: `id | features | text`.
- Text over 300 chars is shown as its first 200 + last 60 chars.
- Tables with ≤ 6 rows are shown whole; longer ones as their first 3 rows + last row.
- ~150 blocks per call. Each call also gets the open Item, the open heading path, and the previous window's last 10 labeled blocks as context (not relabeled).
- The window template ends with a short reminder of the output rules, after the blocks, so the rules sit next to where the model starts writing (a long window would otherwise bury them).

**Labels**

| `role` | Extra fields |
|---|---|
| `item_heading` | `item` |
| `heading` | `level`, `kind`, `run_in` |
| `data_table` | `units`, `units_from` |
| `layout_table` | `rows` (one label per row) |
| `body`, `cover`, `toc`, `part_heading`, `index_table`, `furniture` | — |

- **Heading `kind`:** `risk_summary`, `risk_category`, `risk_factor`, `statement`, `notes_header`, `note`, `auditor_report`, `critical_audit_matter`, `other`.
- **Any label** can add `uncertain: true`, which sends it to the review list.

**Validate → repair** (the agent loop, like V4's verify + repair)
- Code checks every window's labels.
- The LLM fixes violations, at most 2 rounds per window.
- Blocks still failing keep their last label and go into the document's `review` list.
- Fields that don't belong to a label's role are dropped, not repaired (V4).
- V3 needs a full filing, so it isn't checked on an excerpt.

| # | Check | Source |
|---|---|---|
| V1 | One label per block, same order, exact ids | Output format |
| V2 | `item_heading` Items valid, in Form 10-K order, each once | SEC Form 10-K |
| V3 | A full filing has every required Item (1, 1A, 1B, 1C from FY2023, 2–9C, 10–15) | SEC Form 10-K |
| V4 | Fields match the role (`level` only on headings, …) | Output format |
| V5 | Heading levels start at 1 in each Item and never jump by more than 1 | system.md |
| V6 | `risk_*` kinds only in Item 1A; `statement` / `note` / `notes_header` only in Item 8 (or where A3 moves them) | system.md |
| V7 | Each block where an XBRL note text block starts is, or directly follows, a `note` heading | SEC Rule 405 tagging |

**Assembly** (code, from blocks + labels)

| # | Rule |
|---|---|
| A1 | A `run_in` heading splits at D7's N into `b0412.h` (heading) + `b0412.b` (body). Text unchanged |
| A2 | Sections start at each `item_heading`. `cover`, `toc`, `part_heading`, `furniture`, `index_table` leave the sections (counted in `dropped`). `layout_table` rows become headings or paragraphs per their `rows` labels |
| A3 | If Item 8 has no `statement`, the span from the first `statement` / `notes_header` / `auditor_report` elsewhere, up to the next Item heading, moves into Item 8 (NVIDIA, Netflix: under Item 15) |
| A4 | Every block gets a `heading_path` from the heading levels (a heading's path includes itself) |

**Question:** does one prompt work on every filer's styling (AAPL, and the hard cases NVDA, NFLX, MSFT in §6)?

### 2.3 Output: the structured document

One JSON file per filing, built by code (A1–A4), never written by the LLM.

| Field | What |
|---|---|
| `filing` | `ticker`, `registrant`, `fiscal_year`, `period_end` (D3); `accession`, `source_url` (EDGAR manifest) |
| `sections[]` | One per Item, in filing order: `item`, `title`, `blocks[]` |
| `dropped` | Counts of blocks left out of sections, by role |
| `review` | Ids the LLM marked `uncertain` or the validator couldn't repair |

| Block field | On | What |
|---|---|---|
| `id` | all | From D5; a run-in split adds `.h` / `.b` |
| `kind` | all | `heading`, `paragraph` or `table` |
| `text` | all | The filing's own text; tables as `\| a \| b \|` lines |
| `heading_path` | all | Headings above the block; a heading includes itself |
| `xbrl` | all | The inline-XBRL text block it sits in, or null |
| `level`, `heading_kind` | headings | From the labels |
| `rows`, `units`, `units_from` | tables | Cells, and units from the labels |

Example:

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

Block ids carry through, so every chunk maps back to blocks and every claim to the filing.

### 2.4 From the document to each chunker

| Chunker | Reads | Adapter |
|---|---|---|
| R (rule-based) | `Section` / `Block` (`parse.py` dataclasses + `heading_path`, `level`) | One function, both directions |
| P (LlamaIndex) | One `Document` per leaf subsection, `metadata` = T1; each data table its own `TextNode` | Group blocks by `heading_path` |
| A (agentic) | Markdown: Item as `#`, heading level n as `#`×(n+1), tables as `[TABLE n]` placeholders | Renderer that records each block's character span; chunk offsets → spans → blocks |

The Markdown also works with other Markdown-aware splitters (LlamaIndex `MarkdownNodeParser`, LangChain `MarkdownHeaderTextSplitter`), should one be added later.

### 2.5 Shared tagging: chunks → metadata

Every chunker's output goes through the same step, so criterion 1 is computed the same way for all.

| # | Rule |
|---|---|
| T1 | Every chunk records `ticker`, `fiscal_year`, `item`, `item_title`, `heading_path`, `kind` (prose / table); tables also `units` (from the label) and `years_covered` |
| T2 | A chunk's `heading_path` is the deepest path shared by **all** of its blocks. Inside one risk factor: `[category, risk factor]`. Spanning two risk factors: only `[category]` |
| T3 | IDs: `<TICKER>-FY<year>-<item>-<seq>`, e.g. `AAPL-FY2025-1A-007` |

---

## 3. Chunking

- **Input:** the §2.3 document, through §2.4's adapters.
- **Size target** (rule-based and agentic): **~600 tokens**. 5 of the 27 risk factors are longer (max 1,241), so they have to be split.

| ID | Option | Stack | LLM? |
|---|---|---|---|
| R | Rule-based, subsection-aware | `chunking.py` + C10 | no |
| P | Parent-child | LlamaIndex `HierarchicalNodeParser` + `AutoMergingRetriever` | no |
| A | Agentic | Chonkie `SlumberChunker` + `Qwen3.6-27B-FP8` | yes |

### 3.1 Rule-based

- **Stack:** no framework; today's `chunking.py` rules (C1–C8) plus one new rule, C10, so chunks follow headings.
- Table units come from the structure labels instead of a regex.

| # | Rule | Default |
|---|---|---|
| C1 | Chunks never cross sections: every chunk cites exactly one Item | |
| C2 | Each data table is **one chunk**, rendered as `\| cell \| cell \|` lines. Never split mid-row ([D-003](../docs/DECISIONS.md#d-003)) | |
| C3 | Table caption = up to 2 paragraphs right before it, each ≤ 300 chars (title, "(In millions)") | |
| C4 | A table over `table_chars` splits into row groups; each repeats the caption + first 2 rows (header) | 7,200 chars (never triggered; max in corpus 4,747) |
| C5 | A paragraph over the size target is split at sentence ends (`.` or `;` + whitespace) | |
| C6 | Prose paragraphs are packed greedily up to the size target | 600 tokens (today 2,400 chars) |
| C7 | Overlap: a new prose chunk starts with the previous chunk's last paragraph if it's ≤ `overlap_chars` | 600 chars |
| C8 | `years_covered` (`20[0-3]\d`) from the table's top 6 lines | |
| C10 *(new)* | A heading always starts a new chunk; packing (C6) and overlap (C7) never cross one. A subsection over the target splits at paragraph ends, then sentence ends; every piece after the first starts with the heading line | |

**Why C10:** without it, C6 only knows Items and packs several risk factors into one chunk, which fails criterion 2 by design.

**As implemented** ([chunkers.py](v0/chunkers.py) `rule_based`): the same rules, counted in Qwen3 tokens instead of `chunking.py`'s characters.
- Headings with no text between them (a category, then its first risk factor) open one subsection together.
- A heading over only tables makes no prose chunk of its own; it is already the tables' caption (C3).
- Prose before and after a table in the same subsection is packed separately.
- Long paragraphs split to leave room for the heading lines, so prose chunks stay ≤ 600 tokens.
- C7 carries a paragraph over only when it still fits.

### 3.2 Parent-child

Small **children** are matched at search time; the larger **parent** is what the model reads and cites.

**Stack:** LlamaIndex (`llama-index-core`).

| Piece | Use |
|---|---|
| Input | One `Document` per leaf subsection (e.g. one risk factor), `metadata` = T1. Each data table is its own `TextNode`, never split |
| `HierarchicalNodeParser.from_defaults(chunk_sizes=[1280, 256], ...)` | **5:1** (criterion 3), counted with the Qwen3 tokenizer. 1,280 holds every AAPL risk factor whole (max 1,241). Children inherit the parent's metadata and `PARENT` / `CHILD` links |
| `get_leaf_nodes` → index; `AutoMergingRetriever` | Search the children; when enough of one parent's children match, return the parent |

**Why LlamaIndex, not LangChain**
- LlamaIndex nodes carry metadata and parent/child links at every level, and the parser runs on its own.
- LangChain's `ParentDocumentRetriever` splits inside a retriever, which needs a vector store and a docstore just to produce chunks.

| | Parents | Children | Ratio |
|---|---|---|---|
| P | ≤ 1,280 tokens; never cross a leaf subsection | ≤ 256 tokens, the parser's sentence splits | 5:1 |

Criteria 1 and 2 are scored on **parents** (what gets cited); children are only checked for metadata.

**As implemented** ([chunkers.py](v0/chunkers.py) `parent_child`):
- Two `SentenceSplitter`s (1,280 / 256 tokens, overlap 0 / 20) in `HierarchicalNodeParser`'s `node_parser_map`, both counting with the Qwen3 tokenizer.
- Metadata is attached to every node but excluded from the size count.
- Each data table is a parent without children.
- `AutoMergingRetriever` is only used at query time, so this evaluation doesn't run it.

**Question:** do parents keep every risk factor whole where R has to cut five of them?

### 3.3 Agentic

An LLM reads the text and chooses where each chunk ends.

**Stack:** Chonkie `SlumberChunker`
- It proposes candidate split points (paragraph ends) and asks the LLM ("genie") which one ends the current chunk.
- Its `OpenAIGenie` points at the local vLLM (`http://localhost:8001/v1`, `Qwen/Qwen3.6-27B-FP8`).
- Fallback if the genie can't target vLLM: a hand-rolled loop with the same prompt (a few dozen lines).

| Piece | Use |
|---|---|
| Input | One Item at a time, as Markdown (§2.4). Each table becomes a `[TABLE n]` placeholder and is put back whole afterwards (C2 still holds) |
| Headings | Markdown headings: `## Business Risks`, `### The Company depends on …` |
| Size | `chunk_size` ≈ 600 tokens; a candidate window just big enough to show the next few paragraphs |
| Metadata | Chunk offsets → block character spans → T1/T2 |

**Order:** Items 1 + 1A first (no tables, ~14k tokens), then Items 7–8.

**As implemented** ([chunkers.py](v0/chunkers.py) `agentic`):
- `OpenAIGenie` subclass for temperature 0, thinking off and schema-constrained JSON. The stock genie can't set either.
- Candidates: paragraphs, then sentences, then words, at most 128 tokens each.
- `min_characters_per_chunk=1`: the default 24 merged short headings into the previous candidate.
- No anchors in the text; block character spans map chunks back to blocks.

**Question:** with the same headings as R, does an LLM place better boundaries than one rule?

---

## 4. How do we know the doc is chunked well?

- **Three criteria.** A method is good when it passes every criterion that applies to it.
- **Any 10-K.** Each criterion can be scored on any filing that has a reviewed ground truth (§4.4).

| # | Criterion | Applies to |
|---|---|---|
| 1 | Every chunk knows where it came from | All |
| 2 | One risk factor per chunk | All |
| 3 | Parent-child token ratio ≈ 5:1 | P |

### 4.1 Criterion 1: every chunk knows where it came from

Each chunk carries the structure it was cut from (T1, T2):
- `ticker`, `fiscal_year`, `item`, `item_title`
- `heading_path` down to **every heading level**, e.g. `Item 8 › Notes › Note 4 – Financial Instruments › Derivative Instruments and Hedging › Interest Rate Risk`

| Metric | Definition | Good |
|---|---|---|
| 1a. Item | % of chunks whose `item` matches the ground-truth Item of all their text | **100%** (anything less fails) |
| 1b. Path | Each chunk is one of: **exact**, **merged**, **coarse**, **wrong** (see below) | ↑ exact + merged, 0 wrong |
| 1c. Headings | Headings in the document vs ground truth: precision / recall, % with the right level (checks preprocessing) | 1.0 / 1.0 / 100% |

**1b path classes**
- **Exact:** all its text sits under one deepest heading, and the path names it.
- **Merged:** it holds whole sibling subsections (e.g. all of *iPhone* and *Mac*), and the path names their parent. Counts as correct.
- **Coarse:** it mixes part of a subsection with other text; the path is their common ancestor.
- **Wrong:** the path is missing or names the wrong place.

### 4.2 Criterion 2: one risk factor per chunk (semantic boundaries)

**Units of Item 1A** (ground truth `risk_factors`):

| Unit | Definition (any 10-K) | AAPL |
|---|---|---|
| Risk factor | A heading that names one risk (sentence or title; bold, italic or run-in) + its body up to the next heading | 27; 129–1,241 tokens, median 331 |
| Category | A heading that groups risk factors ("Business Risks"). Part of the path, not a unit | 5 |
| Summary | A "Summary of risk factors" list (META, NVDA). Its own unit; may not share a chunk with a risk factor | none |
| Preamble | Text before the first heading. Its own unit | 180 tokens |

| Metric | Definition | Good |
|---|---|---|
| 2a. Purity | % of Item 1A chunks whose text comes from **one** unit | **100%** |
| 2b. Intact | % of risk factors within the size target (600 tokens; 22 of 27) that sit in exactly one chunk | 100% |
| 2c. Fragmentation | Mean over risk factors of chunks used ÷ the minimum ⌈tokens / target⌉ | 1.0 |
| 2d. Heading carried | % of a risk factor's later chunks that still carry its heading (in the text or `heading_path`) | 100% |

For parent-child, 2a–2c are scored on parents, so the target is 1,280.

### 4.3 Criterion 3: parent-child token ratio ≈ 5:1 (P only)

**Boundaries win over the ratio:** a parent never crosses a leaf subsection (e.g. a risk factor), even if that leaves it small.

| Metric | Definition | Good |
|---|---|---|
| 3a. Target | Parent size cap ÷ child size cap, in tokens (config) | 5.0 |
| 3b. Full parents | For prose parents ≥ 80% of the cap: parent tokens ÷ mean child tokens | Median 4–6; ≥ 80% of full parents in 4–6 |
| 3c. Small parents | % of prose parents under 80% of the cap (cut short by a boundary). Reported, not scored | |

- **Tables are exempt:** their children are rows, not windows.
- **AAPL Item 1A:** no risk factor reaches 1,280 tokens, so every one is a "small" parent. 3b is measured on Items 1, 7 and 8.

### 4.4 Ground truth

**What it is:** a hand-checked answer key for one filing. It records where each part of the 10-K really begins and ends, independent of any method:
- the Items
- every heading, with its level and path
- each risk factor
- each table and its units
- the page furniture

**Why it exists:** the criteria compare each method's output to this key. Without it, "is this chunk's path right?" and "does this chunk mix two risk factors?" are opinions.

**How it's used**

| Criterion / check | Compares | Against the ground truth's |
|---|---|---|
| 1a, 1b | Each chunk's `item` and `heading_path` | `items[].headings[].path` |
| 1c | The document's headings and levels | `items[].headings` |
| 2a–2d | Which units each Item 1A chunk's text comes from | `risk_factors.units` (anchored by `heading` and `ends_with`) |
| 4.5 Tables, Noise, Coverage | Tables whole with units; no footers; all text present | `tables`, `furniture`, Item `ends_with` |

Matching is **by text, not by block id**, so the same key scores any method, even one that never saw our blocks.

**How the scorer matches** ([score.py](v0/score.py))
- The filing's text stream is rebuilt with the ground-truth builder's block reader, not the preprocessing.
- Each stream block gets its Item, heading path and risk-factor unit by walking the reviewed headings in order.
- Text is compared as alphanumeric-only keys, so `$1,234` and `$ | 1,234` match.
- Each chunk is pinned by its rarest line. Every other line takes the occurrence nearest its expected position. So rows shared by sibling tables, statement titles that also appear in the index, and heading words inside paragraphs land in the right place.
- Lines under 8 key characters ("2024", "Level 1:") are too ambiguous to locate and don't count toward a block's coverage.
- A label (a short line repeated over tables, e.g. "Apple Inc.") is path-neutral in 1b, like an ancestor heading.

**Status:** AAPL excerpt reviewed → [aapl-fy25-excerpt.gt.json](V0_testdata/aapl-fy25-excerpt.gt.json). Its `review` field lists what was checked by hand.

**How it's built**
1. **Draft:** [v0_ground_truth.py](V0_testdata/v0_ground_truth.py) reads any 10-K HTML.
   - It uses styling rules on purpose, and shares no code or prompt with the preprocessing.
   - So it can't inherit the structure agent's mistakes.
2. **Review:** a person checks the draft against the rendered filing.
   - Fix levels, drop false headings, add missed ones.
   - Record each fix in `review`, then set `"status": "reviewed"`.
   - A draft is never ground truth on its own.

```bash
python -I experiments/V0_testdata/v0_ground_truth.py experiments/V0_testdata/aapl-fy25-excerpt.html \
    --out experiments/V0_testdata/aapl-fy25-excerpt.gt.json
```

**Definitions (any 10-K).** [system.md](v0_structure/system.md) gives the LLM the same ones.

| Thing | Is | Is not |
|---|---|---|
| Item | A Part I–IV Item heading ("Item 1A. Risk Factors") outside the TOC | "Item 5.02" cross-references; TOC rows |
| Heading | A line that titles the text after it: fully emphasized, or the emphasized lead sentence of a run-in paragraph; ≤ 300 chars | A short line repeated over tables ("Apple Inc." above each statement); table row labels; long emphasized body text (AAPL's 581-char italic MD&A intro); page furniture |
| Level | Nesting by meaning, not by style (Item 8 rules below) | "Bold = level 1" |
| Risk factor, category, summary, preamble | §4.2 | |
| Data table | A table with a cell that is only a number | Layout tables (headings, bullets, prose in cells); the "Index to … Financial Statements" (kind `index`) |
| Units | The table's "(in millions)" phrase in its caption or header. If none: inherited from the previous table under the same note (continuation tables; notes that state units once) | |
| Furniture | Page footers ("Apple Inc. \| 2025 Form 10-K \| 22"), bare page numbers, "Table of Contents" | |

**Item 8 levels** (the same parts in every 10-K)
- Level 1: statements, "Notes to … Financial Statements", auditor reports
- Level 2: each "Note N"
- Level 3 and below: subheadings inside a note
- Critical audit matter topics sit under "Critical Audit Matter(s)"

**What the file holds** (text anchors, so any chunker's output can be matched)

| Key | Per entry | AAPL |
|---|---|---|
| `items` | `item`, `title`, `headings` (`level`, `style`, `text`, `path`), `chars`, `tokens`, `ends_with` | 5 Items, 156 headings |
| `risk_factors` | `preamble`; `units` (`kind`, `id`, `heading`, `category`, `path`, `ends_with`, `chars`, `tokens`) | 27 risk factors |
| `tables` | `id`, `item`, `kind`, `path`, `rows`, `first_row`, `units`, `units_from` | 38 data + 1 index |
| `furniture` | `count`, examples | 47 footers |

**Drafts of other filers** (FY2025, unreviewed)

| Filer | Draft |
|---|---|
| AAPL, AMZN, GOOGL, TSLA | Right |
| MSFT | Run-in risk factors found |
| NFLX | Italic intro becomes a false level-1 heading |
| NVDA | Same-style categories and risk factors land on one level |

NFLX, NVDA and MSFT need hand review before they count as ground truth. They're also where styling rules fail, so they're the real test of the structure agent.

### 4.5 Sanity checks (recorded, not criteria)

A method can't pass by dropping or duplicating text, or by breaking tables.

| Check | Definition | Expect |
|---|---|---|
| Coverage | % of the ground truth's text found in some chunk | ~100% (minus furniture) |
| Duplication | Chunk tokens ÷ text tokens | ~1.0–1.3 |
| Noise | Furniture lines in any chunk (47 in the file) | 0 |
| Tables | Each of the 38 data tables in one chunk (or C4 row groups), with its units | 38 / 38 |
| Size | Chunk count; mean / p95 / max tokens | |

### 4.6 Manual review (me, optional; not in the results table)

| ID | What | How |
|---|---|---|
| M1 | Blind chunk review | Item 1A's chunks from each method, shown unlabeled. Score each chunk 1–3: one topic? Sensible start and end? Heading present? |

---

## 5. Results

One table: the three chunking methods on the **same preprocessing**, scored against the ground truth (§4.4). Speed and cost are not measured for now.

**Run it** (vLLM serving `Qwen/Qwen3.6-27B-FP8`: `scripts/serve_vllm.sh qwen3.6-27b-fp8`)

```bash
uv sync --all-extras --all-groups --link-mode copy                      # once; copy mode, see below
taskset -c 16-31 .venv/bin/python experiments/v0/run.py                 # preprocess → chunk → score
taskset -c 16-31 .venv/bin/python experiments/v0/run.py --stage chunk --method rule   # one stage / method
```

- **Outputs:** `output/chunking/<label>/`: `blocks.json`, `labels.json` (cached; `--relabel` to call the LLM again), `document.json`, `chunks-<method>.json`, `scores.json`.
- **Install:** LlamaIndex's bundled NLTK stopwords are refused by NLTK's path check when uv hard-links them (`st_nlink=2`). Install with `--link-mode copy`.
- **`uv sync` scope:** a plain `uv sync --group experiments` removes the project's extras. Always sync with `--all-extras --all-groups`.

| Method | 1a Item | 1b Path exact / merged / coarse / wrong | 2a Purity | 2b Intact | 2c Frag. | 2d Heading | 3b Ratio (median · % in 4–6) | Coverage · noise · tables (§4.5) |
|---|---|---|---|---|---|---|---|---|
| Rule-based | 100% | 92.0 / 0.5 / 1.4 / 6.1 | 100% (34) | 100% (22/22) | 1.01 | 100% (7) | — | 99.3% · 0 · 38/38 |
| Parent-child | 100% | 93.4 / 0.5 / 2.2 / 3.8 | 100% (28) | 100% (27/27) | 1.00 | n/a (none split) | 5.5 · 100% (2 full parents) | 99.4% · 0 · 38/38 |
| Agentic | 100% | 83.6 / 4.8 / 11.0 / 0.7 | 97.4% (39) | 95.5% (21/22) | 1.15 | 100% (12) | — | 99.3% · 0 · 38/38 |

- **Parentheses:** the number of chunks or risk factors scored.
- **2b targets:** 600 tokens for rule-based and agentic, 1,280 for parent-child (parents), so every AAPL risk factor counts for parent-child.

**Shared preprocessing**, recorded once (the same input for every row)
- **1c headings:** precision 94.0% · recall 100% · level 99.4% (166 found, 156 in the ground truth).
- **Items:** 5 of 5. Dropped: 47 footers, 1 index table. Validator: 0 violations, no repairs, empty review list.

**Chunks**

| Method | Chunks | Mean / p95 / max tokens | Notes |
|---|---|---|---|
| Rule-based | 213 | 188 / 546 / 1,036 | Prose ≤ 592 |
| Parent-child | 182 parents, 204 children | 217 / 664 / 1,241 | Children ≤ 256 |
| Agentic | 146 | 270 / 557 / 1,036 | Prose ≤ 563 |

The 1,036-token maximum is a table (C2 keeps tables whole).

**Ground-truth fix found during scoring:** T04 (gross-margin percentages) had borrowed T03's "in millions" caption. Corrected to no units and recorded in the file's `review` field.

### Takeaways

**All three are solid on the basics**
- Every chunk cites the right Item.
- All 38 tables are whole, with their units.
- No page footers in any chunk; coverage ≥ 99.3%.

**Criterion 2 (risk factors): rule-based and parent-child are perfect, agentic isn't**
- Rule-based and parent-child cut at every heading the structure agent labeled, and it found all 27 risk factors.
- Agentic merged two adjacent risk factors (RF24 + RF25, both on credit risk) into one chunk. It also split some more than needed (fragmentation 1.15).

**Criterion 1 (paths): the remaining errors are preprocessing errors, and the methods inherit them differently**
- Rule-based and parent-child cut at every heading, so they carry over the agent's 10 false headings: "Apple Inc." above each statement, "First/Second/Third/Fourth Quarter 2025:" in Item 7. One level error ("Segment Reporting") adds to that.
- Rule-based suffers most (6.1% wrong), since it makes a small prose chunk on each side of every statement table.
- Agentic ignores weak headings, so it's almost never wrong (0.7%), but it's coarse more often (11%), because it also ignores some real boundaries.

**Criterion 3 (5:1) holds where it can, but rarely matters**
- The 2 full parents are 5.5:1.
- 99% of parents are cut short by a subsection boundary; 103 of 144 have a single child.
- The median ratio across all prose parents is 1.0. On a 10-K, "boundaries win" means most parent-child pairs are the same text.

**Verdict on this excerpt**
- **Parent-child** scores best overall: the most exact paths, perfect risk-factor separation, and the 5:1 target where it applies. Rule-based is a close second.
- **Agentic** costs an LLM call per chunk and does worse on the criterion that motivated it.

**Next**
- The biggest lever is preprocessing, not chunking. The prompt already says the company-name label isn't a heading, and the model still made it one. A validator check could catch it: short headings repeated ≥ 3 times over tables, sent back for repair.
- The real test is a second filer with harder styling (NVDA, NFLX, MSFT; §4.4 drafts) once their ground truth is reviewed.

---

## 6. 10-K traits

Facts about the filings themselves.

### Known (now in the D-rules or system.md)

- The table of contents is a multi-row table of "Item N." lines, usually hyperlinks (`toc`, D7 `link`).
- Some filers put each Item heading in its own **one-row** table (`layout_table` rows).
- One number spans 2–3 `<td>`s: `$` | `1,234`, `(3` | `)%` (D6).
- "Item 5.02"-style cross-references look like headings (`item_heading` "is not").
- Running headers, page numbers, "Form 10-K | 22" footers repeat on every page (`furniture`).
- Item 8 can be a one-line pointer; NVIDIA and Netflix put the statements under Item 15 / after the signatures (A3).
- Units differ by filer (Netflix in thousands; per-share in dollars) and sit in the caption or at the end of the preceding paragraph.
- Table columns are dates, not fiscal-year labels; Alphabet and Amazon run oldest → newest.
- The same figure appears in many chunks (MD&A, notes, segments, statements, next year's 10-K): "Total net sales" is in 13 AAPL chunks.
- **0 of 24** filings use `<h1–h6>`; headings are styled `<div>` / `<span>` (bold, font size).

### New

**Risk-factor headings by filer (FY2025)**

| Filer | Categories | Risk factors | Extra |
|---|---|---|---|
| AAPL, AMZN, GOOGL, TSLA | bold | bold-italic | AMZN headings are Title Case |
| META | bold | bold-italic | "Summary of Risk Factors" list |
| MSFT | bold, then bold-italic subcategories | **run-in**: bold first sentence inside the body paragraph | three levels |
| NFLX | — | bold | italic intro sentence |
| NVDA | bold | bold (same as categories) | summary list; italic intro |

**Headings and styling (AAPL)**
- Risk-factor headings are full sentences (58–284 chars, ending in ".").
- Item 1 uses **bold** for subsections and *italic* below them (iPhone, Mac, …); Item 7 the same.
- Item 8 note titles and their subheadings are both bold.

**Tables and units (AAPL)**
- Units are sometimes stated once per note (Note 6), or only on the first part of a continued table (Note 13's third geographic table).
- The auditor's critical audit matter is prose laid out in a 2-column table; the old "digit in a non-first cell" rule (P4) called it a data table.
- Items 1 and 1A have no data tables.

**Markup**
- **Inline XBRL** (required): every note is block-tagged (`*TextBlock`, continued across pages via `ix:continuation`), but not one tag per note.
  - AAPL: 16 note-level tags for 13 notes (Leases: operating + finance; PP&E: note + table), plus 11 policy and 21 table text blocks.
  - The `dei:` cover tags give ticker, fiscal year, period end and registrant.
- **Page breaks are not universal,** so they're a feature (`pb`), not a rule:
  - AAPL: one `<hr style="page-break-after:always">` per page (47, matching its 47 footers)
  - MSFT: 101
  - NVDA, NFLX: 1

### Not covered by the AAPL excerpt

- The NVDA, NFLX and MSFT risk-factor styles above (drafts exist, need review)
- TSLA's long risk-factor lists
- NVIDIA's statements under Item 15
- Netflix's thousands
- Alphabet's oldest → newest columns
- MSFT segment tables with group headers
