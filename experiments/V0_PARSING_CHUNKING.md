# V0 · Parsing and chunking

---

## 1. Baseline (B0): rule-based, structure-preserving

No LLM, no layout model, no ML. Pure HTML-tree walking plus regexes.

### Libraries

| Library | Used for |
|---|---|
| `beautifulsoup4` | DOM traversal (`children`, `find_all`, `get_text`) |
| `lxml` | The parser behind BeautifulSoup (`BeautifulSoup(html, "lxml")`); fast, tolerant of EDGAR's inline-XBRL markup |
| `re` (stdlib) | Headings, noise lines, cell merging, units, years, sentence splits |
| `dataclasses` (stdlib) | `Block`, `Section`, `Chunk` |

Tokens are approximated as **4 chars = 1 token**; no tokenizer.

### Parse rules (`parse.py`): HTML → blocks → sections

| # | Rule | Why |
|---|---|---|
| P1 | Walk `<body>` recursively. A tag with no block-level descendants (`div p li ul ol h1–h6 table center`) becomes **one paragraph** (`get_text(" ")`). Loose text next to block tags is its own paragraph. | EDGAR HTML is nested `<div>`s; the innermost text container is the paragraph |
| P2 | Drop elements with `display:none`. | Hidden inline-XBRL header |
| P3 | Whitespace: `&nbsp;` → space, collapse runs, strip. | |
| P4 | A `<table>` is a **data table** if it has > 1 row and a digit in any non-first column → one `table` block (list of rows of cells). Otherwise a **layout table**: each row becomes a paragraph. | Filers use tables for bullets, headings and the TOC too |
| P5 | Layout-table rows from tables with > 2 rows are flagged `from_table` and can't be headings. | The table of contents is a multi-row table of "Item N." lines |
| P6 | Merge EDGAR's split cells: `$` / `(` / `$(` joins the next cell; `)`, `%`, `)%` joins the previous one. `( 7,763 )` → `(7,763)`. | One number is often spread over 2–3 `<td>`s |
| P7 | **Section heading** = paragraph, not `from_table`, ≤ 150 chars, matching `^item\s*(\d{1,2})\s*([a-c])?\s*\.\s*(.*)$`, item in the valid list (1, 1A–1C, 2–7, 7A, 8, 9, 9A–9C, 10–16), title non-empty and not starting with a digit. | "Item 5.02" is a cross-reference, not a heading |
| P8 | Everything before the first heading is dropped. | Cover page, TOC |
| P9 | Drop noise paragraphs: `Table of Contents`, `PART II`, running headers (`Item 1B, 1C`), bare page numbers, footers (`Apple Inc. \| 2025 Form 10-K \| 22`). | Page furniture repeats on every page |
| P10 | If an Item appears more than once, keep the occurrence with the most blocks. | TOC links, repeated headers |
| P11 | If Item 8 has no tables, move the financial statements into it, starting at "Index to Consolidated Financial Statements", else at the auditor's report that opines on the statements. | NVIDIA, Netflix put statements under Item 15 / after signatures |

### Chunk rules (`chunking.py`): sections → chunks

| # | Rule | Default |
|---|---|---|
| C1 | Chunks never cross sections: every chunk cites exactly one Item. | |
| C2 | Each data table is **one chunk**, rendered as `\| cell \| cell \|` lines. Never split mid-row ([D-003](../docs/DECISIONS.md#d-003)). | |
| C3 | Table caption = up to 2 paragraphs right before it, each ≤ 300 chars (title, "(In millions)"). | |
| C4 | A table over `table_chars` splits into row groups; each repeats the caption + first 2 rows (header). | 7,200 chars (never triggered in the corpus; max 4,747) |
| C5 | A paragraph over `chunk_chars` is split at sentence ends (`.` or `;` + whitespace). | |
| C6 | Prose paragraphs are packed greedily up to `chunk_chars`. | 2,400 chars ≈ 600 tokens |
| C7 | Overlap: a new prose chunk starts with the previous chunk's last paragraph if it's ≤ `overlap_chars`. | 600 chars |
| C8 | Table metadata from the top 6 lines: `units` (`in millions/thousands/billions` → `USD millions`), `years_covered` (`20[0-3]\d`). | |
| C9 | IDs: `<TICKER>-FY<year>-<item>-<seq>`, e.g. `AAPL-FY2025-7-012`. | |

**Baseline numbers (already known):** 24 filings → 6,299 chunks (1,571 tables), mean 1,320 chars; ingest ~30 s for all 24 (incl. SQLite writes). AAPL FY2025: 1.5 MB HTML, parse 0.3 s, 23 sections, 170 chunks, 48 tables.

### Existing variant (B1): V7 parent-child

Same parse (P1–P11). Parents: tables whole; prose packed to 6,000 chars, starting a new parent at a heading-like line (`Note N`, short capitalized line) once the current one is ⅓ full. Children: ~800-char sentence windows with one-sentence overlap; one child per table row (`group › label: FY2025 = …`); one table-title child. Search children, return parents. See [V7 guide](../docs/V7_ADVANCED_RETRIEVAL.md). Already +15 pts Recall@5 on dev, so it's the bar to beat, not just B0.

---

## 2. Methods to compare

Same input for all: the [AAPL FY2025 excerpt](V0_testdata/aapl-fy25-excerpt.html) (§3); the 24 cached filings only for Q6. Where a method has a size knob, target **~600 tokens** so size isn't the variable.

| ID | Method | Library | Parse input | LLM? | Notes |
|---|---|---|---|---|---|
| B0 | Rule-based structural (baseline) | bs4 + lxml | HTML | no | §1 |
| B1 | Rule-based parent-child | bs4 + lxml | HTML | no | V7 |
| N1 | Naive fixed-size (floor) | LangChain `RecursiveCharacterTextSplitter` | `soup.get_text()` | no | Lower bound: no structure at all |
| L1 | HTML header splitter | LangChain `HTMLHeaderTextSplitter` / `HTMLSemanticPreservingSplitter` | HTML | no | 0 of 24 filings use `<h1–h6>`, so header splitting should find nothing. Worth confirming |
| L2 | HTML node parser + sentence splitter | LlamaIndex `HTMLNodeParser` → `SentenceSplitter` | HTML | no | |
| L3 | Partition + chunk by title | Unstructured `partition_html` + `chunk_by_title` | HTML | no | Element-typed (Title, Table, NarrativeText) |
| L4 | Docling HTML | Docling `DocumentConverter` + `HybridChunker` | HTML | no | Table structure model not needed for HTML |
| S1 | Semantic chunking | LlamaIndex `SemanticSplitterNodeParser` or LangChain `SemanticChunker` | B0 sections (prose only; tables kept whole) | embeddings | Embedder: `Qwen3-Embedding-0.6B` (same as retrieval). Breakpoint percentile is the knob |
| S2 | Semantic on raw text | same | `get_text()` | embeddings | Isolates "semantic" from "our parse" |
| A1 | Agentic / propositional | LLM groups propositions into chunks (Chonkie `SlumberChunker` or hand-rolled) | B0 sections | yes | Most expensive. Run on a subset first |
| X1 | LLM-assisted structural | LLM labels each block: heading level / Item / table-vs-layout / noise; rules (C1–C9) then chunk | B0 blocks | yes | Replaces P4, P5, P7, P9 regexes with an LLM. Tests whether the rules are the bottleneck |
| X2 | LLM boundary refinement | B0 chunks, LLM decides merge / split at each boundary | B0 chunks | yes | Cheaper than A1: one decision per boundary |

LLM for A1/X1/X2: local `Qwen3.6-27B-FP8` via vLLM (cost = GPU-time × $0.86/h, `config.yaml`); optionally one API model for comparison.

---

## 3. How quality is measured

**Test data:** [aapl-fy25-excerpt.html](V0_testdata/aapl-fy25-excerpt.html), a verbatim, unedited slice of the AAPL FY2025 10-K from the `Item 7.` heading to the `Item 9.` heading (accession 0000320193-25-000079; pages 21–51). It holds Items 7, 7A and 8 complete: 41 tables, 31 page footers, the five financial statements, Notes 1–13 and both auditor reports. 1.0 MB of HTML but only ~82k characters of text (inline XBRL). Q1–Q5 run on this excerpt. Q6 runs on the full 24-filing corpus with the existing gold set ([EVAL.md](../docs/EVAL.md)).

**Ground truth: not written yet.** Q1–Q4 need one: questions with evidence, the tables that must stay whole, Item boundaries, the footers to drop. Until it exists, run the methods, save the chunks, and compare by hand (M1).

### Automatic (every method, every run)

| ID | Metric | Definition | Good |
|---|---|---|---|
| Q1 | **Answerable questions** | % of ground-truth questions where one chunk holds every `evidence` and `context` string · % where the pieces are split across chunks · % lost | ↑ answerable, 0 lost |
| Q2 | **Item and boundaries** | Each chunk maps to one Item (N/A if the method has no Item labels) · Item 7 / 7A / 8 boundary texts never share a chunk · headings stay with their body | ↑ |
| Q3 | **Table integrity** | For each ground-truth table: one chunk (or row groups that repeat caption + header), with its units phrase | ↑ |
| Q4 | **Coverage / noise** | % of the HTML's visible text found in some chunk · duplication ratio · none of the 31 `Apple Inc. \| 2025 Form 10-K \| N` footers in any chunk | ↑ / ~1.0–1.3 / 0 |
| Q5 | Size shape | chunk count, mean / p95 / max tokens | |
| Q6 | **Retrieval** (downstream) | `tenk eval run retrieval --split dev`: Recall@5, MRR. Embedder, reranker, k fixed | ↑ |

Write the ground truth from the HTML, not from B0's output, so it doesn't favor B0. It needs no reference chunking: prose boundaries have no single right answer, so they are judged by hand (M1).

### Manual (me)

| ID | What | How |
|---|---|---|
| M1 | Blind chunk review | The excerpt's chunks from each method, shown unlabeled. Score each chunk 1–3 on: self-contained? sensible boundaries? table intact with header + units? noise-free? |
| M2 | Boundary gold (only if Q1–Q6 don't separate methods) | Hand-mark ideal prose boundaries on the excerpt → boundary precision / recall |

**Not covered by the AAPL excerpt** (add a second excerpt if a method looks good): NVIDIA's statements under Item 15, Netflix's thousands, Alphabet's oldest → newest columns, MSFT segment tables with group headers, TSLA's long risk-factor lists. Item 1A (risk factors) is not in it at all.

### Speed and cost

| Metric | How |
|---|---|
| Parse + chunk time | s / filing, wall clock, CPU cores 16–31 (`taskset -c 16-31`, [I-001](../docs/DECISIONS.md#i-001)) |
| Embed time | s / filing (chunk count drives it) |
| LLM tokens | in / out per filing |
| $ / filing | API price, or GPU-hours × $0.86 |
| $ / corpus | × 24, and extrapolated to 1,000 filings |

---

## 4. Results

*Run label → `output/chunking/<label>/`. Fill as runs land.*

### Quality

| ID | Q1 contained / split / lost | Q2 Item | Q3 table | Q4 coverage / dup / noise | Q5 chunks · mean / p95 tok | Q6 R@5 · MRR | M1 |
|---|---|---|---|---|---|---|---|
| B0 | | | | | | | |
| B1 | | | | | | | |
| N1 | | | | | | | |
| L1 | | | | | | | |
| L2 | | | | | | | |
| L3 | | | | | | | |
| L4 | | | | | | | |
| S1 | | | | | | | |
| S2 | | | | | | | |
| A1 | | | | | | | |
| X1 | | | | | | | |
| X2 | | | | | | | |

### Speed and cost

| ID | Parse+chunk s/filing | Embed s/filing | LLM tok in / out per filing | $ / filing | $ / 1,000 filings |
|---|---|---|---|---|---|
| B0 | | | — | 0 | 0 |
| B1 | | | — | 0 | 0 |
| N1 | | | — | 0 | 0 |
| L1 | | | — | 0 | 0 |
| L2 | | | — | 0 | 0 |
| L3 | | | — | 0 | 0 |
| L4 | | | — | 0 | 0 |
| S1 | | | — | | |
| S2 | | | — | | |
| A1 | | | | | |
| X1 | | | | | |
| X2 | | | | | |

### Takeaways

-

---

## 5. 10-K traits

Facts about the filings themselves. The first block is what B0's rules already encode; new findings go below it.

**Known (baked into B0)**
- The table of contents is a multi-row table of "Item N." lines → headings outside tables only (P5, P7).
- Some filers put each Item heading in its own **one-row** table (P5 exempts ≤ 2-row tables).
- One number spans 2–3 `<td>`s: `$` | `1,234`, `(3` | `)%` (P6).
- "Item 5.02"-style cross-references look like headings (P7).
- Running headers, page numbers, "Form 10-K | 22" footers repeat on every page (P9).
- Item 8 can be a one-line pointer; NVIDIA and Netflix put the statements under Item 15 / after the signatures (P11).
- Units differ by filer: Netflix in thousands, per-share in dollars; units sit in the caption or at the end of the preceding paragraph.
- Table columns are dates, not fiscal-year labels; Alphabet and Amazon run oldest → newest.
- The same figure appears in many chunks (MD&A, notes, segments, statement; next year's 10-K again): "Total net sales" is in 13 AAPL chunks.
- **0 of 24** filings use `<h1–h6>`; headings are styled `<div>`/`<span>` (bold, font size).

**New**
-

---

## 6. Log

| Date | Entry |
|---|---|
| 2026-10-18 | Notebook started. Baseline rules written down; method list and metrics drafted. |
| 2026-10-18 | Test data: AAPL FY2025 Items 7, 7A, 8 (complete, unedited) saved as `V0_testdata/aapl-fy25-excerpt.html`. Ground truth not written yet. |
