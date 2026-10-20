# Experiments

## V0 · Parsing and chunking

Full design, criteria and results: [here](V0_PARSING_CHUNKING.md).

Code: [v0/v0_src/](v0/v0_src/); prompt in [v0/v0_structure/](v0/v0_structure/); test data in [v0/v0_testdata/](v0/v0_testdata/). Run: `taskset -c 16-31 .venv/bin/python experiments/v0/v0_src/run.py` (needs the vLLM server).

### Preprocessing: HTML → one structured document

Two steps, run in order:

1. **Step 1 · Rules** (BeautifulSoup + lxml): only what is the same in every 10-K
   - HTML mechanics: hidden elements, whitespace, paragraphs and tables, split cells
   - Inline XBRL: unwrap facts, record note / policy / table tags
   - Filing metadata from the `dei:` cover tags
2. **Step 2 · LLM structure agent** (Qwen3.6-27B on vLLM): everything else
   - Labels Items, headings and levels, risk factors, tables and units, page furniture
   - Validated against the Form 10-K Item rules, then repaired
   - Prompt: [system.md](v0/v0_structure/system.md); output format it enforces: [labels.schema.json](v0/v0_structure/labels.schema.json)

### Chunking: one of three options, compared on the same document

- **Option · Rule-based** (R): custom rules (`chunking.py`), new chunk at every heading
- **Option · Parent-child** (P; 5:1 tokens): LlamaIndex `HierarchicalNodeParser`
- **Option · Agentic** (A): Chonkie `SlumberChunker` + Qwen3.6-27B

### Test data

- [AAPL FY2025 excerpt](v0/v0_testdata/aapl-fy25-excerpt.html): Items 1, 1A, 7, 7A, 8 complete (27 risk factors, 38 data tables)
- [Ground truth](v0/v0_testdata/aapl-fy25-excerpt.gt.json), built by [v0_ground_truth.py](v0/v0_testdata/v0_ground_truth.py)
- [Sanity cases](v0/v0_testdata/aapl-fy25-sanity.yaml): 21 hand-checkable cases, one per kind of chunk, run by [sanity.py](v0/v0_src/sanity.py)

## V1 · Retrieval

Full design, metrics and results: [here](V1_RETRIEVAL.md).

Code: [v1/v1_src/](v1/v1_src/); test data in [v1/v1_testdata/](v1/v1_testdata/). Run: `taskset -c 16-31 .venv/bin/python experiments/v1/v1_src/run.py` (GPU for the embedder and reranker; no vLLM).

- **Pipeline:** slots → keyword (BM25) + dense (`Qwen3-Embedding-0.6B`) → RRF → rerank (`bge-reranker-v2-m3`) → round-robin → top 8
- **Corpora:** V0's rule-based, parent-child and agentic chunks of the four FY2025 filings ([build_corpus.py](v1/v1_src/build_corpus.py) → `data/v0-chunking/<method>.db`)
- **Result:** parent-child is best (Recall@5 82.1%, MRR 0.670 on 46 questions). Questions spanning several filings are the weak spot for every chunking
- **Test data:** 46 FY2025 questions on AAPL, AMZN, MSFT, NVDA, reviewed, including cross-company, cross-year and no-company questions ([retrieval-gold-fy2025.jsonl](v1/v1_testdata/retrieval-gold-fy2025.jsonl); readable: [PDF](v1/v1_testdata/retrieval-gold-fy2025.pdf), [summary](v1/v1_testdata/retrieval-gold-fy2025-summary.pdf))
- **Metrics:** Recall@1, Recall@5, MRR, per question kind
