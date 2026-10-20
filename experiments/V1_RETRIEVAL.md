# V1 · Retrieval

**Goal:** how often the retrieval pipeline puts the chunk that answers a question at the top, for each of V0's three chunkings.

**Result:** parent-child is best on every metric. It misses 3 of 46 questions; the other two chunkings miss 9 each.

| Chunking | Recall@1 | Recall@5 | Recall@8 | MRR | Missed |
|---|---|---|---|---|---|
| Rule-based | 33.9% | 62.5% | 74.3% | 0.541 | 9 |
| **Parent-child** | **44.2%** | **82.1%** | **88.6%** | **0.670** | **3** |
| Agentic | 39.3% | 61.6% | 71.7% | 0.580 | 9 |

**Single vs several sources.** Parent-child finds every single-source question in the top 8, but only 69% of the sources of multi-source questions.

| Recall@5 / Recall@8 | Rule-based | Parent-child | Agentic |
|---|---|---|---|
| One source (29 questions) | 69.0% / 82.8% | **96.6% / 100%** | 69.0% / 82.8% |
| Several sources (17 questions) | 51.5% / 59.8% | **57.4% / 69.1%** | 49.0% / 52.9% |

**Contents:** [1 Test data](#1-test-data) · [2 Corpora](#2-corpora) · [3 Pipeline](#3-pipeline) · [4 Metrics](#4-metrics) · [5 Results](#5-results) · [6 Without slot search](#6-without-slot-search)

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
| Slots | One search per (company, filing) the question names; one unfiltered search if it names none | `slot_search: true` |
| Keyword | Question terms against the FTS5 index, BM25 order | 40 candidates |
| Dense | Question vector against chunk vectors, cosine. Each chunk is embedded with a header line (company, fiscal year, Item) | `Qwen/Qwen3-Embedding-0.6B`, 40 candidates |
| Fuse | Reciprocal rank fusion of the two lists, cut to 40 | `rrf_k: 60` |
| Rerank | A cross-encoder scores each (question, header + chunk) pair | `BAAI/bge-reranker-v2-m3` |
| Merge | Each slot's best chunk first, then each slot's second, … | `k: 8` |

Code: [retrieve.py](v1/v1_src/retrieve.py). Settings: [config.yaml](v1/v1_src/config.yaml). Both models are off-the-shelf, not trained here.

---

## 4. Metrics

| Metric | Definition |
|---|---|
| **Recall@k** | Share of a question's gold sources with a gold chunk in the top k, averaged over questions. Recall@8 covers everything the answer step reads |
| **MRR** | Mean of 1 / rank of the first gold chunk in the top 8; 0 if none |

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
| **No company named, several sources** | `open-highest-revenue`, `open-largest-capex`: 0 of 4 sources on every chunking | With no company named there's one unfiltered search. Its top 8 can't hold one statement from each of four filings: the revenue lines rank 10, 11 and 17 |
| **The question's words aren't the filing's** | `cmp-capex-all4`: 0 of 4 on every chunking | No filer calls the line "capital expenditures": each names it differently ("payments for acquisition of…", "additions to…", "purchases related to…") |
| **Several companies at once** | `compare_company`: Recall@5 56% | Each company's search gets 2–4 of the 8 places, and is run with the whole question, which names the other companies too. The gold statement row often loses to an MD&A table or note that restates the figure |

### Takeaways

- **Parent-child wins figures and years through its row children.** Each table row is a child that names its line item in words; for `msft-capex` the top match is the cash-flow row itself. It leads on `compare_year` by the widest margin (Recall@5 89% vs 33–44%).
- **Row children matter most when several companies share the 8 places.** In `cmp-net-income-all4`, dense search ranks the gold statement 1st–5th with parent-child (`Net income: 2025 = $112,010 …` is its own child) but 9th–33rd with rule-based, where it's one line of a whole income statement. Rule-based gets 1 of 4 sources into the top 5; parent-child gets 3, and all 4 into the top 8.
- **Rule-based and agentic win risks.** Each risk factor is its own chunk, so a risk question matches one whole risk factor (MRR 1.00).
- **Agentic adds nothing over rule-based.** Its scores are level or lower, and it costs an LLM call per chunk.
- **Questions that span several filings are the weak spot for every chunking.** The misses come from how a question becomes searches (slots, the shared 8 places, the query's wording), not from chunking.

---

## 6. Without slot search

The same 46 questions with `slot_search: false`: one unfiltered search per question.

| Chunking | Recall@5 / MRR, with slots | Without | `compare_company` Recall@5, with → without |
|---|---|---|---|
| Rule-based | 62.5% / 0.541 | 57.4% / 0.531 | 43% → 20% |
| **Parent-child** | **82.1% / 0.670** | **80.4% / 0.648** | 56% → 48% |
| Agentic | 61.6% / 0.580 | 54.7% / 0.538 | 44% → 22% |

- **Single-company questions barely move.** Parent-child's `figure`, `risk`, `drivers`, `compare_year` and `open` scores are unchanged. Rule-based and agentic shift by ±0.01 MRR, except agentic's `compare_year` (MRR 0.34 → 0.24).
- **Why:** every chunk is embedded and reranked with its company and fiscal year, so an unfiltered search for "Apple's net income" already ranks Apple's chunks first.
- **Slots matter when several companies share the 8 places.** Without them, one or two companies take most of the places.
