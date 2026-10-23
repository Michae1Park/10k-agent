# V1 · Retrieval

**Goal:** how often the retrieval pipeline puts the chunk that answers a question at the top, for three ways of chunking the same 10-Ks.

**Result:** parent-child is best overall; rule-based and agentic lead only on `risk` and `open` questions. Parent-child misses 2 of 46 questions; rule-based misses 7 and agentic 5.

| Chunking | Recall@1 | Recall@5 | Recall@8 | MRR | Missed |
|---|---|---|---|---|---|
| Rule-based | 43.3% | 71.9% | 81.9% | 0.648 | 7 |
| **Parent-child** | **53.3%** | **87.3%** | **91.7%** | **0.758** | **2** |
| Agentic | 45.5% | 76.8% | 83.5% | 0.672 | 5 |

**Single vs several sources.** Parent-child finds every single-source question in the top 8, and 78% of the sources of multi-source questions.

| Recall@5 / Recall@8 | Rule-based | Parent-child | Agentic |
|---|---|---|---|
| One source (29 questions) | 75.9% / 86.2% | **96.6% / 100%** | 82.8% / 89.7% |
| Several sources (17 questions) | 65.2% / 74.5% | **71.6% / 77.5%** | 66.7% / 73.0% |

**Contents:** [1 Test data](#1-test-data) · [2 Corpora](#2-corpora) · [3 Pipeline](#3-pipeline) · [4 Results](#4-results) · [5 Query decomposition](#5-query-decomposition) · [6 Without slot search](#6-without-slot-search)

---

## 1. Test data

**File:** [retrieval-gold-fy2025.jsonl](../evaluation/gold/retrieval-gold-fy2025.jsonl). To read it, run `tenk gold --gold <file> render`: a PDF with one card per question (evidence highlighted) and a summary, written next to the file and not committed.

46 questions on the FY2025 10-Ks of AAPL, AMZN, MSFT and NVDA, with 80 gold sources. All are reviewed by a person.

| Kind | Questions | Sources | Asks for | Example |
|---|---|---|---|---|
| `figure` | 11 | 11 | One number from a financial statement | "What were Apple's total net sales in fiscal 2025?" |
| `risk` | 4 | 9 | Risk-factor prose (Item 1A) | "What does NVIDIA say about the risks from U.S. export controls?" |
| `drivers` | 4 | 4 | MD&A prose explaining a change (Item 7) | "How fast did AWS sales grow in 2025, and what drove the growth?" |
| `compare_company` | 10 | 30 | The same fact for 2–4 named companies | "Compare the net income of Apple, Amazon, Microsoft and NVIDIA in fiscal 2025." |
| `compare_year` | 9 | 9 | One company's fact across years, from the FY2025 filing's columns | "How did Amazon's net income change from 2023 to 2025?" |
| `open` | 8 | 17 | No company named | "Which company spent the most on property and equipment in fiscal 2025?" |

**Gold chunks:** each source quotes one verbatim line from the filing. A chunk is gold if it contains that line. Every source resolves on every corpus.

**Repeated figures:** a 10-K often states the same figure twice: in the Item 8 financial statement, and again in an MD&A table or a note.

| Where | NVIDIA's FY2025 net income |
|---|---|
| Item 8, income statement (the gold source) | `Net income \| $72,880 \| $29,760 \| $4,368` |
| Item 7, MD&A summary table | `Net income \| $72,880 \| $29,760 \| Up 145%` |

Either line answers the question, so both count. The gold set lists 25 such repeated lines on 13 questions (`acceptable_sources`), each tied to the source whose figure it repeats.

---

## 2. Corpora

Each filing was parsed into a structured document (Items, headings, tables) and split into chunks three ways. Each chunking was loaded into its own database and embedded.

| Chunking | How it splits |
|---|---|
| Rule-based | A new chunk at every heading; prose packed up to 600 tokens; each table one chunk |
| Parent-child | Parents up to 1,280 tokens, never crossing a heading; small children (≤ 256 tokens) are searched. Each table row is its own child, naming its columns: `Net income: 2025 = $112,010; 2024 = $93,736; …` |
| Agentic | An LLM picks where each chunk ends (up to 600 tokens); each risk factor kept whole |

| Chunking | Database | Chunks |
|---|---|---|
| Rule-based | `data/v0-chunking/rule.db` | 1,134 |
| Parent-child | `data/v0-chunking/parent_child.db` | 1,030 parents, 3,288 children |
| Agentic | `data/v0-chunking/agentic.db` | 957 |

- **One SQLite file per chunking.** Chunk text and metadata (ticker, fiscal year, Item) are ordinary tables. The BM25 index (FTS5) and the vectors (sqlite-vec) are virtual tables on the same rows, so a search filters on the metadata in the same query that ranks.
- **Parent-child:** search runs over the children, and each child is swapped for its parent. A child's text starts with its heading path, since most children have no heading of their own.
- **Built by** [build_corpus.py](v1/build_corpus.py). Each filing's parsed document and chunks are in `data/v0-chunking/<TICKER>-FY2025/`.

---

## 3. Pipeline

```
question ─► QueryDecomposer ─► sub-questions ─► HybridSearch each: keyword + dense ─► RRF ─► rerank ─► merge ─► top 8
```

`SubQuestionRetriever` runs the whole pipeline ([retrieval/](../src/tenk_agent/retrieval/)). Settings: [config.yaml](v1/config.yaml).

**1. Decompose** (`QueryDecomposer`, rule-based, no LLM)

| Question | Sub-questions |
|---|---|
| Names one company | One, the question as asked, filtered to that company's filing |
| Names several | One per company, **rewritten to name only that company**: "Compare the net income of Apple, Amazon, Microsoft and NVIDIA" → "Compare the net income of Apple in fiscal 2025.", … |
| Names none, asks which company ("which company…", "whose…") | **One per company in the corpus**, prefixed with its name: "Amazon.com, Inc. (AMZN): Which company spent the most…" |
| Names none otherwise | One unfiltered search |

Companies are found by ticker, name or alias ("Google" → GOOGL). A bare calendar year also takes the fiscal year that mostly covers it ("2024" → NVDA FY2025).

**2. Search each sub-question** (`HybridSearch`)

| Step | What happens | Setting |
|---|---|---|
| Keyword | Sub-question terms against the FTS5 index, BM25 order | 40 candidates |
| Dense | Sub-question vector against chunk vectors, cosine. Each chunk is embedded with a header line (company, fiscal year, Item) | `Qwen/Qwen3-Embedding-0.6B`, 40 candidates |
| Fuse | Reciprocal rank fusion of the two lists, cut to 40 | `rrf_k: 60` |
| Rerank | A cross-encoder scores each (sub-question, header + chunk) pair | `BAAI/bge-reranker-v2-m3` |

**3. Merge:** round-robin, each sub-question's best chunk first, then each one's second, … Each sub-question gets ⌈8 / sub-questions⌉ places (at least 2). Within a round, the sub-question whose best chunk scored highest goes first.

Both models are off-the-shelf, not trained here.

---

## 4. Results

### By kind

Recall@1 / Recall@5 / Recall@8 / MRR

| Kind | Rule-based | Parent-child | Agentic |
|---|---|---|---|
| figure (11) | 45% / 73% / 91% / 0.59 | **73% / 100% / 100% / 0.83** | 45% / 82% / 82% / 0.58 |
| risk (4) | **71% / 92% / 100% / 1.00** | 58% / 92% / 92% / 0.88 | **71%** / 79% / 88% / **1.00** |
| drivers (4) | 75% / 100% / 100% / 0.88 | 75% / 100% / 100% / 0.88 | 75% / 100% / 100% / 0.88 |
| compare_company (10) | 18% / 64% / 77% / 0.63 | **22% / 75% / 82% / 0.72** | 18% / 69% / 77% / 0.63 |
| compare_year (9) | 22% / 56% / 67% / 0.39 | **56% / 89% / 100% / 0.70** | 33% / 67% / 89% / 0.51 |
| open (8) | **66%** / 75% / 75% / 0.75 | 50% / 75% / **78%** / 0.67 | **66% / 78% / 78% / 0.78** |

### Where retrieval still fails

Parent-child misses 2 questions, both about capex: `cmp-capex-all4` and `open-largest-capex` (0 of 4 sources on every chunking). No filer calls the line "capital expenditures"; each names it differently:

| Company | The statement line |
|---|---|
| Apple | Payments for acquisition of property, plant and equipment |
| Amazon | Purchases of property and equipment |
| Microsoft | Additions to property and equipment |
| NVIDIA | Purchases related to property and equipment and intangible assets |

### Takeaways

- **Parent-child wins figures and years through its row children.** Each table row is a child that names its line item in words; for `msft-capex` the top match is the cash-flow row itself. It finds every `figure` and `compare_year` source in the top 8.
- **Row children matter most when several companies share the 8 places.** In `cmp-net-income-all4`, dense search ranks the gold statement 1st–5th with parent-child (`Net income: 2025 = $112,010 …` is its own child) but 9th–33rd with rule-based, where it's one line of a whole income statement.
- **Rule-based and agentic win risks.** Each risk factor is its own chunk, so a risk question matches one whole risk factor (MRR 1.00).
- **Agentic adds nothing over rule-based** that's worth an LLM call per chunk.

---

## 5. Query decomposition

Does rewriting the question per company, and searching every company for "which company" questions, help? The same 46 questions with both turned off: each company's search gets the question as asked, and a question naming no company gets one unfiltered search.

Recall@8, with → without

| Chunking | All | `compare_company` | `open` | `figure`, `risk`, `drivers`, `compare_year` |
|---|---|---|---|---|
| Rule-based | 81.9% → 79.7% | 77% → 69% | 75% → 72% | unchanged |
| **Parent-child** | **91.7% → 90.0%** | 82% → 82% | 78% → 69% | unchanged |
| Agentic | 83.5% → 78.6% | 77% → 59% | 78% → 72% | unchanged |

| Part | Example | Effect |
|---|---|---|
| **Rewrite per company** | "Which spent the most on R&D in fiscal 2025: Apple, Microsoft or NVIDIA?" → NVIDIA's search is run with "…in fiscal 2025: NVIDIA?" and its R&D line moves from 3rd to 1st | Helps rule-based and agentic on `compare_company`. Parent-child doesn't need it: its row children already match |
| **Search every company** | "Which of the companies covered reported the highest revenue?": one unfiltered search returned only Microsoft and NVIDIA chunks (0 of 4 revenue lines); four searches return 2 chunks per company (2 of 4) | Finds sources across companies. Cost: when one company is the answer ("Which company says tariff costs lowered its margin?"), the others now compete with it; parent-child's `open` MRR is 0.67 instead of 0.75 |

---

## 6. Without slot search

Is filtering by company doing the work? The same 46 questions with `slot_search: false`: no decomposition, one unfiltered search with the question as asked.

Recall@8, with → without

| Chunking | All | `compare_company` | `open` | `figure`, `risk`, `drivers` |
|---|---|---|---|---|
| Rule-based | 81.9% → 73.0% | 77% → 48% | 75% → 72% | almost unchanged |
| **Parent-child** | **91.7% → 87.7%** | 82% → 72% | 78% → 69% | unchanged |
| Agentic | 83.5% → 74.6% | 77% → 51% | 78% → 72% | unchanged |

**Why only multi-company questions change**

- **One company named: the filter adds nothing.** Every chunk is embedded with a header line naming its company and year (`Apple Inc. (AAPL) fiscal 2025 10-K, Item 8 Financial Statements`). So an unfiltered search for "Apple's net income" already ranks Apple's chunks first.
- **Several companies: one search can't share its 8 places fairly.** The one or two best-matching companies take most of them. With slot search, each company gets its own search and its own places.
