# V1 · Retrieval

**Goal:** how often the retrieval pipeline puts the chunk that answers a question at the top, for each of V0's three chunkings.

**Result:** parent-child is best on every metric. It misses 3 of 46 answerable questions; the other two chunkings miss 9 each.

| Chunking | Recall@1 | Recall@5 | MRR | Missed |
|---|---|---|---|---|
| Rule-based | 33.9% | 62.5% | 0.541 | 9 |
| **Parent-child** | **44.2%** | **82.1%** | **0.670** | **3** |
| Agentic | 39.3% | 61.6% | 0.580 | 9 |

**Contents:** [1 Test data](#1-test-data) · [2 Corpora](#2-corpora) · [3 Pipeline](#3-pipeline) · [4 Metrics](#4-metrics) · [5 Results](#5-results)

---

## 1. Test data

**File:** [retrieval-gold-fy2025.jsonl](v1/v1_testdata/retrieval-gold-fy2025.jsonl). To read it: [PDF](v1/v1_testdata/retrieval-gold-fy2025.pdf) (one card per question, evidence highlighted) and [summary](v1/v1_testdata/retrieval-gold-fy2025-summary.pdf).

54 questions on the FY2025 10-Ks of AAPL, AMZN, MSFT and NVDA, with 80 gold sources.

| Kind | Questions | Sources | Asks for | Example |
|---|---|---|---|---|
| `figure` | 11 | 11 | One number from a financial statement | "What were Apple's total net sales in fiscal 2025?" |
| `risk` | 4 | 9 | Risk-factor prose (Item 1A) | "What does NVIDIA say about the risks from U.S. export controls?" |
| `drivers` | 4 | 4 | MD&A prose explaining a change (Item 7) | "How fast did AWS sales grow in 2025, and what drove the growth?" |
| `compare_company` | 10 | 30 | The same fact for 2–4 named companies | "Compare the net income of Apple, Amazon, Microsoft and NVIDIA in fiscal 2025." |
| `compare_year` | 9 | 9 | One company's fact across years, from the FY2025 filing's columns | "How did Amazon's net income change from 2023 to 2025?" |
| `open` | 8 | 17 | No company named | "Which company spent the most on property and equipment in fiscal 2025?" |
| `decline` | 8 | — | Something the four filings can't answer | "What was Oracle's revenue in fiscal 2025?" |

**Gold chunks:** each source quotes one verbatim line from the filing. A chunk is gold if it contains that line. Every source resolves on every corpus.

**Decline questions** cover a company outside the four (Oracle, Tesla), a forecast, a year not filed, a stock price, executive pay (in the proxy, not the 10-K), a figure the 10-K doesn't give, and investment advice. They have no gold, so they get no Recall or MRR.

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
| Slots | One search per (company, filing) the question names; one unfiltered search if it names none | |
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
| **Top score** | The reranker's best score for the question (0–1). Reported for `decline`, where there's no gold |

---

## 5. Results

### By kind

Recall@1 / Recall@5 / MRR

| Kind | Rule-based | Parent-child | Agentic |
|---|---|---|---|
| figure (11) | 36% / 73% / 0.52 | **45% / 100% / 0.66** | 36% / 64% / 0.50 |
| risk (4) | **71% / 92% / 1.00** | 58% / 92% / 0.88 | **71%** / 79% / **1.00** |
| drivers (4) | 75% / 100% / 0.88 | 75% / 100% / 0.88 | 75% / 100% / 0.88 |
| compare_company (10) | 5% / 43% / 0.41 | **18% / 56% / 0.56** | 10% / 44% / 0.46 |
| compare_year (9) | 11% / 33% / 0.23 | **33% / 89% / 0.55** | 22% / 44% / 0.34 |
| open (8) | 53% / **72%** / 0.69 | **66%** / 69% / **0.75** | **66% / 72% / 0.75** |

### Where retrieval fails

The 3 questions parent-child misses, and the weak spots behind the lower scores:

| Pattern | Questions | Why |
|---|---|---|
| **No company named, several sources** | `open-highest-revenue`, `open-largest-capex`: 0 of 4 sources on every chunking | With no company named there's one unfiltered search. Its top 8 can't hold one statement from each of four filings |
| **Capex across companies** | `cmp-capex-all4`: 0 of 4 on every chunking | Each filer names the line differently ("payments for acquisition of…", "additions to…", "purchases related to…") |
| **Several companies at once** | `compare_company`: Recall@5 56% | Each company's search gets 2–4 of the 8 places. Within it, the gold statement row often loses to an MD&A table or note that restates the figure (R&D in MD&A's operating-expense table, EPS in Note 3) |

### Decline questions

The reranker's top score (0–1), parent-child:

| Question | Top score |
|---|---|
| Tesla automotive revenue | 0.00 |
| Oracle revenue | 0.18 |
| Should I buy NVIDIA stock? | 0.49 |
| Apple's stock price, Amazon's CEO pay | 0.85, 0.88 |
| NVIDIA FY2027 forecast, Microsoft FY2026, Azure dollar revenue | 0.96–0.98 |
| *Answerable questions (mean)* | *0.93* |

Only companies outside the corpus score low. For the rest, retrieval finds closely related text with the same confidence as for answerable questions, so declining has to happen when the answer is written (V2).

### Takeaways

- **Parent-child wins figures and years through its row children.** Each table row is a child that names its line item in words; for `msft-capex` the top match is the cash-flow row itself. It leads on `compare_year` by the widest margin (Recall@5 89% vs 33–44%).
- **Rule-based and agentic win risks.** Each risk factor is its own chunk, so a risk question matches one whole risk factor (MRR 1.00).
- **Agentic adds nothing over rule-based.** Its scores are level or lower, and it costs an LLM call per chunk.
- **Questions that span several filings are the weak spot for every chunking.** The no-company and cross-company misses come from the pipeline's slots and its 8 places, not from chunking.
