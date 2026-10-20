# V1 · Retrieval

**Goal:** how often the retrieval pipeline puts the chunk that answers a question at the top, for each of V0's three chunkings.

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

**Contents:** [1 Test data](#1-test-data) · [2 Corpora](#2-corpora) · [3 Pipeline](#3-pipeline) · [4 Metrics](#4-metrics) · [5 Results](#5-results) · [6 Decomposition and restatements](#6-decomposition-and-restatements) · [7 Without slot search](#7-without-slot-search)

---

## 1. Test data

**File:** [retrieval-gold-fy2025.jsonl](v1/v1_testdata/retrieval-gold-fy2025.jsonl). To read it: [PDF](v1/v1_testdata/retrieval-gold-fy2025.pdf) (one card per question, evidence highlighted) and [summary](v1/v1_testdata/retrieval-gold-fy2025-summary.pdf).

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

**Restatements:** a figure is often stated twice, in the Item 8 statement and again in an MD&A table or a note (`Net income | $72,880 | $29,760 | Up 145%`). 25 such lines on 13 questions are `acceptable_sources`, each tied to the source it restates (`restates`). A chunk holding one counts for that source.

---

## 2. Corpora

The four filings went through V0's preprocessing and each of its three chunkers ([V0](V0_PARSING_CHUNKING.md) §2–3). Each chunking was loaded into its own database and embedded.

| Chunking | Database | Chunks |
|---|---|---|
| Rule-based | `data/v0-chunking/rule.db` | 1,134 |
| Parent-child | `data/v0-chunking/parent_child.db` | 1,030 parents, 3,288 children |
| Agentic | `data/v0-chunking/agentic.db` | 957 |

- **One SQLite file per chunking.** Chunk text and metadata (ticker, fiscal year, Item) are ordinary tables. The BM25 index (FTS5) and the vectors (sqlite-vec) are virtual tables on the same rows, so a search filters on the metadata in the same query that ranks.
- **Parent-child:** search runs over the children, and each child is swapped for its parent. A child's text starts with its heading path, since most children have no heading of their own.
- **Built by** [build_corpus.py](v1/v1_src/build_corpus.py). V0's outputs for each filing are in `data/v0-chunking/<TICKER>-FY2025/`.

---

## 3. Pipeline

```
question ─► QueryDecomposer ─► sub-questions ─► HybridSearch each: keyword + dense ─► RRF ─► rerank ─► merge ─► top 8
```

`SubQuestionRetriever` runs the whole pipeline ([retrieve.py](v1/v1_src/retrieve.py)). Settings: [config.yaml](v1/v1_src/config.yaml).

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

## 4. Metrics

| Metric | Definition |
|---|---|
| **Recall@k** | Share of a question's gold sources with a gold chunk (or a restatement) in the top k, averaged over questions. Recall@8 covers everything the answer step reads |
| **MRR** | Mean of 1 / rank of the first gold chunk in the top 8; 0 if none |

---

## 5. Results

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

| Pattern | Questions | Why |
|---|---|---|
| **The question's words aren't the filing's** | `cmp-capex-all4` (0 of 4) and `open-largest-capex` (0 of 4), on every chunking | No filer calls the line "capital expenditures": each names it differently ("payments for acquisition of…", "additions to…", "purchases related to…") |
| **Fan-out spreads a one-company answer** | `open` MRR 0.67 for parent-child (0.75 without fan-out) | "Which company says tariff costs lowered its margin?" now searches all four companies. The answer is still in the top 8, but other companies' best chunks can rank above it |
| **Merge order moves a source past rank 5** | `cmp-supply-risk`: 1 → 0 of 2 sources in the top 5, still 1 of 2 in the top 8 | The score-ordered merge (§3) puts the other company's chunk first; Apple's gold moves from rank 5 to 6 |

### Takeaways

- **Parent-child wins figures and years through its row children.** Each table row is a child that names its line item in words; for `msft-capex` the top match is the cash-flow row itself. It finds every `figure` and `compare_year` source in the top 8.
- **Row children matter most when several companies share the 8 places.** In `cmp-net-income-all4`, dense search ranks the gold statement 1st–5th with parent-child (`Net income: 2025 = $112,010 …` is its own child) but 9th–33rd with rule-based, where it's one line of a whole income statement.
- **Rule-based and agentic win risks.** Each risk factor is its own chunk, so a risk question matches one whole risk factor (MRR 1.00).
- **Agentic adds nothing over rule-based** that's worth an LLM call per chunk.
- **Vocabulary is the main remaining gap.** Both remaining parent-child misses are capex questions.

---

## 6. Decomposition and restatements

Each change added in turn, parent-child. A is the pipeline before both changes, with strict gold.

| Variant | Recall@1 | Recall@5 | Recall@8 | MRR | Missed |
|---|---|---|---|---|---|
| A. Before | 44.2% | 82.1% | 88.6% | 0.670 | 3 |
| B. + restatements in the gold | 55.3% | 85.5% | 90.0% | 0.762 | 3 |
| C. + one rewritten sub-question per company | **56.0%** | 86.2% | 90.0% | **0.773** | 3 |
| D. + fan-out when no company is named (current) | 53.3% | **87.3%** | **91.7%** | 0.758 | **2** |

B–D use the score-ordered merge (§3); with it, A gives 82.6% / 87.9% / 0.654.

| Recall@8, A → D | Rule-based | Parent-child | Agentic |
|---|---|---|---|
| All | 74.3% → 81.9% | 88.6% → 91.7% | 71.7% → 83.5% |
| `compare_company` | 54% → 77% | 76% → 82% | 48% → 77% |

- **Restatements change the measurement, not retrieval.** They lift MRR most (0.670 → 0.762): the retrieved MD&A tables and notes were already stating the right figure.
- **Rewriting helps the weaker chunkings most.** `compare_company` Recall@8 for rule-based and agentic rises from 54% and 48% to 77%. Parent-child gains less because its row children already matched well.
- **Fan-out finds `open-highest-revenue`** (0 → 2 of 4 sources) at the cost of MRR on one-company `open` questions.
- **The gains sit mostly in `dev`.** Parent-child's `test` questions (14) keep Recall@8 at 80.4%; two improve and one (`cmp-supply-risk`, through the merge order) drops out of the top 5. The restatements happen to fall mostly on `dev` questions.

---

## 7. Without slot search

The same 46 questions with `slot_search: false`: one unfiltered search with the question as asked.

| Chunking | Recall@5 / Recall@8 / MRR, current | Without slot search | `compare_company` Recall@8 | `open` Recall@8 |
|---|---|---|---|---|
| Rule-based | 71.9% / 81.9% / 0.648 | 67.8% / 73.0% / 0.611 | 77% → 48% | 75% → 72% |
| **Parent-child** | **87.3% / 91.7% / 0.758** | 83.9% / 87.7% / 0.759 | 82% → 72% | 78% → 69% |
| Agentic | 76.8% / 83.5% / 0.672 | 67.9% / 74.6% / 0.626 | 77% → 51% | 78% → 72% |

- **Single-company questions barely move.** `figure`, `risk` and `drivers` are unchanged for parent-child and agentic; rule-based's `figure` shifts slightly.
- **Why:** every chunk is embedded and reranked with its company and fiscal year, so an unfiltered search for "Apple's net income" already ranks Apple's chunks first.
- **Decomposition matters when several companies share the 8 places.** Without it, one or two companies take most of the places.
