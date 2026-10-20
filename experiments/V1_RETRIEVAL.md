# V1 · Retrieval

**Goal:** how often the retrieval pipeline puts the chunk that answers a question at the top, for each of V0's three chunkings.

**Result:** parent-child is best on every metric and misses no question.

| Chunking | Recall@1 | Recall@5 | MRR | Missed |
|---|---|---|---|---|
| Rule-based | 55.2% | 85.4% | 0.725 | 1 |
| **Parent-child** | **58.3%** | **97.9%** | **0.776** | **0** |
| Agentic | 55.2% | 76.0% | 0.712 | 2 |

**Contents:** [1 Test data](#1-test-data) · [2 Corpora](#2-corpora) · [3 Pipeline](#3-pipeline) · [4 Metrics](#4-metrics) · [5 Results](#5-results)

---

## 1. Test data

**File:** [retrieval-gold-fy2025.jsonl](v1/v1_testdata/retrieval-gold-fy2025.jsonl). To read it: [PDF](v1/v1_testdata/retrieval-gold-fy2025.pdf) (one card per question, evidence highlighted) and [summary](v1/v1_testdata/retrieval-gold-fy2025-summary.pdf).

16 questions on the FY2025 10-Ks of AAPL, AMZN, MSFT and NVDA, with 21 gold sources.

| Kind | Questions | Sources | Asks for | Example |
|---|---|---|---|---|
| `figure` | 10 | 10 | One number from a financial statement | "What were Apple's total net sales in fiscal 2025?" |
| `risk` | 4 | 9 | Risk-factor prose (Item 1A) | "What does NVIDIA say about the risks from U.S. export controls?" |
| `drivers` | 2 | 2 | MD&A prose explaining a change (Item 7) | "How fast did AWS sales grow in 2025, and what drove the growth?" |

**Gold chunks:** each source quotes one verbatim line from the filing. A chunk is gold if it contains that line. All 16 questions resolve on every corpus.

---

## 2. Corpora

The four filings went through V0's preprocessing and each of its three chunkers ([V0](V0_PARSING_CHUNKING.md) §2–3). Each chunking was loaded into its own database and embedded.

| Chunking | Database | Chunks |
|---|---|---|
| Rule-based | `data/v0-chunking/rule.db` | 1,134 |
| Parent-child | `data/v0-chunking/parent_child.db` | 1,030 parents, 3,288 children |
| Agentic | `data/v0-chunking/agentic.db` | 957 |

- **Parent-child:** search runs over the children, and each child is swapped for its parent. A child's text starts with its heading path, since most children have no heading of their own.
- **Built by** [build_corpus.py](v1/v1_src/build_corpus.py). V0's outputs for each filing are in `data/v0-chunking/<TICKER>-FY2025/`.

---

## 3. Pipeline

```
question ─► slots ─► per slot: keyword (BM25) + dense ─► RRF ─► rerank ─► round-robin ─► top 8
```

| Step | What happens | Setting |
|---|---|---|
| Slots | One search per (company, filing) the question names | |
| Keyword | Question terms against the FTS5 index, BM25 order | 40 candidates |
| Dense | Question vector against chunk vectors, cosine | `Qwen/Qwen3-Embedding-0.6B`, 40 candidates |
| Fuse | Reciprocal rank fusion of the two lists, cut to 40 | `rrf_k: 60` |
| Rerank | A cross-encoder scores each (question, chunk) pair | `BAAI/bge-reranker-v2-m3` |
| Merge | Each slot's best chunk first, then each slot's second, … | `k: 8` |

Code: [retrieve.py](v1/v1_src/retrieve.py). Settings: [config.yaml](v1/v1_src/config.yaml). Both models are off-the-shelf, not trained here.

---

## 4. Metrics

| Metric | Definition |
|---|---|
| **Recall@k** | Share of a question's gold sources with a gold chunk in the top k, averaged over questions |
| **MRR** | Mean of 1 / rank of the first gold chunk in the top 8; 0 if none |

---

## 5. Results

### By kind

Recall@1 / Recall@5 / MRR

| Kind | Rule-based | Parent-child | Agentic |
|---|---|---|---|
| figure (10) | 40% / 80% / 0.56 | **50% / 100% / 0.69** | 40% / 70% / 0.54 |
| risk (4) | **71% / 92% / 1.00** | 58% / 92% / 0.88 | **71%** / 79% / **1.00** |
| drivers (2) | 100% / 100% / 1.00 | 100% / 100% / 1.00 | 100% / 100% / 1.00 |

### By question

The rank of the first gold chunk. "—" means not in the top 8. For multi-source questions, the parentheses show how many sources are in the top 5.

| Question | Kind | Rule-based | Parent-child | Agentic |
|---|---|---|---|---|
| aapl-net-sales | figure | 1 | 2 | 1 |
| nvda-rd | figure | 4 | 3 | 7 |
| amzn-operating-income | figure | 5 | 3 | 4 |
| msft-net-income | figure | 1 | 1 | 1 |
| aapl-net-income | figure | 2 | 1 | 2 |
| msft-capex | figure | 7 | **1** | — |
| amzn-capex | figure | — | **4** | — |
| aapl-operating-cash-flow | figure | 1 | 1 | 1 |
| nvda-total-assets | figure | 1 | 1 | 1 |
| aapl-diluted-eps | figure | 2 | 2 | 2 |
| msft-datacenter-risk | risk | 1 (2/3) | 1 (2/3) | 1 (2/3) |
| nvda-export-controls | risk | 1 (2/2) | 2 (2/2) | 1 (2/2) |
| aapl-tariffs | risk | 1 (2/2) | 1 (2/2) | 1 (1/2) |
| msft-ai-risks | risk | 1 (2/2) | 1 (2/2) | 1 (2/2) |
| aapl-products-gross-margin | drivers | 1 | 1 | 1 |
| amzn-aws-growth | drivers | 1 | 1 | 1 |

### Takeaways

- **Parent-child wins figures through its row children.** Each table row is a child that names its line item in words. For `msft-capex`, the top match is the cash-flow row itself: `Additions to property and equipment: 2025 = (64,551) …`. Parent-child is the only chunking that finds both capex questions.
- **Rule-based and agentic win risks.** Each risk factor is its own chunk, so a risk question matches one whole risk factor (MRR 1.00). Parent-child is close (0.88).
- **Agentic adds nothing over rule-based.** Its scores are level or lower, and it costs an LLM call per chunk.
- **The set is small.** With 16 questions, one `figure` question moves that kind's Recall@5 by 10 points.
