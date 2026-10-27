# Experiments

One folder per version, holding only what runs and scores its experiment: `run.py`, the version's own scoring, `config.yaml` (the settings the results were made with) and `testdata/`. The pipeline itself is in [`src/tenk_agent/`](../src/tenk_agent/), shared with the `tenk` CLI; how to run it: [`docs/GETTING_STARTED.md`](../docs/GETTING_STARTED.md), how it works: [`docs/stages/`](../docs/stages/). Each `V*.md` here is the version's design and results.

```
v0/  run.py · score.py, sanity.py (vs the ground truth) · config.yaml · testdata/ (excerpt, ground truth + its builder, sanity cases)
v1/  run.py · build_corpus.py (the three corpora) · config.yaml
v2/  run.py · judge.py (runs the LLM judge) · config.yaml
```

## V0 · Parsing and chunking

Full design, criteria and results: [here](V0_PARSING_CHUNKING.md).

Pipeline: [preprocess/](../src/tenk_agent/preprocess/), [chunking/](../src/tenk_agent/chunking/); experiment: [v0/](v0/). Run: `taskset -c 16-31 .venv/bin/python experiments/v0/run.py` (needs the vLLM server).

### Preprocessing: HTML → one structured document

Two steps, run in order:

1. **Step 1 · Rules** (BeautifulSoup + lxml): only what is the same in every 10-K
   - HTML mechanics: hidden elements, whitespace, paragraphs and tables, split cells
   - Inline XBRL: unwrap facts, record note / policy / table tags
   - Filing metadata from the `dei:` cover tags
2. **Step 2 · LLM structure agent** (Qwen3.6-27B on vLLM): everything else
   - Labels Items, headings and levels, risk factors, tables and units, page furniture
   - Validated against the Form 10-K Item rules, then repaired
   - Prompt: [structure.md](../src/tenk_agent/prompts/structure.md); output format it enforces: [structure.schema.json](../src/tenk_agent/prompts/structure.schema.json)

### Chunking: one of three options, compared on the same document

- **Option · Rule-based** (R): custom rules (`chunking/rule_based.py`), new chunk at every heading
- **Option · Parent-child** (P; 5:1 tokens): LlamaIndex `HierarchicalNodeParser`
- **Option · Agentic** (A): Chonkie `SlumberChunker` + Qwen3.6-27B

### Test data

- [AAPL FY2025 excerpt](v0/testdata/aapl-fy25-excerpt.html): Items 1, 1A, 7, 7A, 8 complete (27 risk factors, 38 data tables)
- [Ground truth](v0/testdata/aapl-fy25-excerpt.gt.json), built by [ground_truth.py](v0/testdata/ground_truth.py)
- [Sanity cases](v0/testdata/aapl-fy25-sanity.yaml): 21 hand-checkable cases, one per kind of chunk, run by [sanity.py](v0/sanity.py)

## V1 · Retrieval

Full design, metrics and results: [here](V1_RETRIEVAL.md).

Pipeline: [retrieval/](../src/tenk_agent/retrieval/); experiment: [v1/](v1/). Run: `taskset -c 16-31 .venv/bin/python experiments/v1/run.py` (GPU for the embedder and reranker; no vLLM).

- **Pipeline:** query decomposition (one sub-question per company) → keyword (BM25) + dense (`Qwen3-Embedding-0.6B`) → RRF → rerank (`bge-reranker-v2-m3`) → round-robin → top 8
- **Corpora:** V0's rule-based, parent-child and agentic chunks of the four FY2025 filings ([build_corpus.py](v1/build_corpus.py) → `data/v0-chunking/<method>.db`)
- **Result:** parent-child is best (Recall@5 87.3%, Recall@8 91.7%, MRR 0.758 on 46 questions). Questions spanning several filings are the weak spot for every chunking
- **Test data:** 46 FY2025 questions on AAPL, AMZN, MSFT, NVDA, reviewed, including cross-company, cross-year and no-company questions ([retrieval-gold-fy2025.jsonl](../evaluation/gold/retrieval-gold-fy2025.jsonl); readable: `tenk gold render`)
- **Metrics:** Recall@1, Recall@5, Recall@8, MRR, per question kind

## V2 · Generation

Full design, metrics and results: [here](V2_GENERATION.md).

Pipeline: [generation/](../src/tenk_agent/generation/), prompt [answer.md](../src/tenk_agent/prompts/answer.md); experiment: [v2/](v2/). Run: `taskset -c 16-31 .venv/bin/python experiments/v2/run.py` (needs the vLLM server), then `judge.py` with the judge served.

- **Pipeline:** V1's retrieval, unchanged (parent-child, top 8) → one model call (`Qwen3.6-27B`) → JSON answer with claims citing chunk IDs → each claim checked against the chunks it cites
- **Contexts:** the same questions three ways, to separate generation from retrieval:
  - **Retrieved**: V1's top 8, as in production
  - **Oracle**: V1's top 8 with every gold source it missed swapped in (differs from Retrieved only where V1 failed)
  - **None**: no excerpts; the model should decline everything
- **Result:** with V1's retrieval, 21 of 30 number questions right (25 of 30 with every gold source shown); all 8 declines declined; nothing answered from memory. Text questions await required points rewritten for answers
- **Test data:** 54 questions: V1's 46 answerable ones, unchanged, plus 8 to decline (companies, years and figures outside the filings, investment advice) ([generation-gold-fy2025.jsonl](../evaluation/gold/generation-gold-fy2025.jsonl); readable: `tenk gold render`)
- **Metrics:** correct (numbers by code, text by an LLM judge against required points), declined when it should / when it shouldn't, numeric claims grounded in a cited chunk, gold sources cited

## V3 · Agent

Full design, metrics and results: [here](V3_AGENT.md).

Agent: [agent/](../src/tenk_agent/agent/), prompt [agent.md](../src/tenk_agent/prompts/agent.md); experiment: [v3/](v3/). Run: `taskset -c 16-31 .venv/bin/python experiments/v3/run.py` (needs the vLLM server), then `v2/judge.py --config experiments/v3/config.yaml` with the judge served.

- **Agent:** LangGraph loop over `Qwen3.6-27B`; tools `search_filings` (V1's search), `calculator`, and with SEC data `xbrl_facts`, `edgar_filings` (cached, offline). Answer and claim check as in V2, claims may cite XBRL facts
- **Variants:** V2 rerun as the baseline; the agent with 10-K tools (`text`); with SEC tools too (`full`), to separate what tool use adds from what the data adds
- **Result:** 88.5% (`text`) and 87.2% (`full`) of 78 questions right against V2's 66.7%; held-out test split 18 and 19 of 24 against 14. The `get_section` tool was removed after an ablation showed it added nothing (§6 round 4). Year ranges, multi-step and "which company" questions are where it gains
- **Test data:** 78 questions in one self-contained set: V2's 54, copied in, plus 24 new, reviewed: calculation, multi-step, SEC data, tool-selection negatives ([agent-gold-fy2025.jsonl](../evaluation/gold/agent-gold-fy2025.jsonl))
- **Metrics:** V2's, plus calls per question, tool errors, repeats, budget stops, expected-tools recall and precision

