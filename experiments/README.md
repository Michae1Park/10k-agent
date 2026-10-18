# Experiments

## V0 · Parsing and chunking

Full design, criteria and results: [here](V0_PARSING_CHUNKING.md).

Code: [v0/](v0/). Run: `taskset -c 16-31 .venv/bin/python experiments/v0/run.py` (needs the vLLM server).

### Preprocessing: HTML → one structured document

Two steps, run in order:

1. **Step 1 · Rules** (BeautifulSoup + lxml): only what is the same in every 10-K
   - HTML mechanics: hidden elements, whitespace, paragraphs and tables, split cells
   - Inline XBRL: unwrap facts, record note / policy / table tags
   - Filing metadata from the `dei:` cover tags
2. **Step 2 · LLM structure agent** (Qwen3.6-27B on vLLM): everything else
   - Labels Items, headings and levels, risk factors, tables and units, page furniture
   - Validated against the Form 10-K Item rules, then repaired
   - Prompt: [system.md](v0_structure/system.md); output format it enforces: [labels.schema.json](v0_structure/labels.schema.json)

### Chunking: one of three options, compared on the same document

- **Option · Rule-based** (R): custom rules (`chunking.py`), new chunk at every heading
- **Option · Parent-child** (P; 5:1 tokens): LlamaIndex `HierarchicalNodeParser`
- **Option · Agentic** (A): Chonkie `SlumberChunker` + Qwen3.6-27B

### Test data

- [AAPL FY2025 excerpt](V0_testdata/aapl-fy25-excerpt.html): Items 1, 1A, 7, 7A, 8 complete (27 risk factors, 38 data tables)
- [Ground truth](V0_testdata/aapl-fy25-excerpt.gt.json), built by [v0_ground_truth.py](V0_testdata/v0_ground_truth.py)
