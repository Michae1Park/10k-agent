# V1 · Retrieval: question → the chunks that answer it

**Builds on** V0: its chunks, loaded into a database. No LLM. **Code:** [`retrieval/`](../src/tenk_agent/retrieval/). **Design and results:** [experiments/V1_RETRIEVAL.md](../experiments/V1_RETRIEVAL.md): with parent-child chunks, Recall@5 87.3% and MRR 0.758 on 46 questions.

## Commands

Workstation terminal, repo root. The embedder and reranker run on the GPU (`retrieval.device: cpu` works, slower).

```bash
source .venv/bin/activate
taskset -c 16-31 tenk build --stage load --stage embed                  # once after V0: database + vectors
tenk search "How much did NVIDIA spend on R&D in fiscal 2025?"          # sub-questions, then the top k with rerank scores
tenk search "Compare the net income of Apple, Amazon, Microsoft and NVIDIA in fiscal 2025." -k 8

# The V1 experiment: the gold questions, scored (Recall@1/5/8, MRR)
taskset -c 16-31 python experiments/v1/run.py                                                  # parent-child
taskset -c 16-31 python experiments/v1/run.py --db data/v0-chunking/rule.db --label v0-rule    # another chunking
taskset -c 16-31 python experiments/v1/run.py --question aapl-net-sales-fy2025                 # one question
taskset -c 16-31 python experiments/v1/build_corpus.py --stage load --stage embed              # the three corpora
```

In Python:

```python
from pathlib import Path
from tenk_agent.config import load
from tenk_agent.retrieval.search import SubQuestionRetriever
from tenk_agent.store import Store

settings = load()
store = Store(Path(settings["db"]))
retriever = SubQuestionRetriever.from_settings(store, settings["retrieval"])
result = retriever.search("Which company spent the most on R&D in fiscal 2025?", k=8)
result.chunks      # the top 8 ChunkRecords (parents, on a parent-child database)
result.searches    # per sub-question: its text, filters, keyword and dense candidates, rerank scores
```

## Data

| Data | Where |
|---|---|
| **Database** (`db`) | `data/v0-chunking/parent_child.db`; also `rule.db`, `agentic.db` |
| Chunks (parents), children, filings, sections; FTS5 index | tables in the database, written by `tenk build --stage load` |
| Vectors (1,024-d, cosine) | `chunks_vec`, `children_vec`, written by `--stage embed`; the model in `meta` |
| Embedding model, reranker | Hugging Face cache, downloaded on first use |
| Gold questions | [experiments/v1/testdata/retrieval-gold-fy2025.jsonl](../experiments/v1/testdata/retrieval-gold-fy2025.jsonl) ([format](#gold-questions)) |

| Table | Key fields |
|---|---|
| `companies` | ticker, cik, name, aliases ("Google", "Facebook") |
| `filings` | accession_no, ticker, fiscal_year, period_end_date, filing_date, source_url |
| `sections` | id (`AAPL-FY2025-7`), item, title |
| `chunks` | id (`AAPL-FY2025-8-002`), ticker, fiscal_year, item, seq, kind (prose / table), text, units, years_covered, pages, printed_pages |
| `children` (parent-child only) | id (`…-8-002-c03`), parent_id, ticker, fiscal_year, item, kind (prose / table_title / row), text (heading path in front) |

## Knobs

`config.yaml` → `retrieval:`; the V1 experiment pins its own copy in [experiments/v1/config.yaml](../experiments/v1/config.yaml).

| Knob | What it does | Default |
|---|---|---|
| `embedder` | Dense model; changing it needs `--stage embed` again (re-embeds everything) | `local:Qwen/Qwen3-Embedding-0.6B` |
| `reranker` | Cross-encoder over (sub-question, header + chunk); `none` to skip | `local:BAAI/bge-reranker-v2-m3` |
| `candidates` | Per retriever, per sub-question; the fused list is cut to this before reranking | 40 |
| `rrf_k` | Reciprocal rank fusion constant | 60 |
| `slot_search` | One search per (company, filing) the question names; off: one unfiltered search | true |
| `decompose.rewrite` | Each company's sub-question names only that company | true |
| `decompose.fan_out` | "Which company…" with no company named: one sub-question per company | true |
| `k` | Chunks returned (and given to the V2 model) | 8 |
| `device` | Where the local models run: `null` = GPU if any | null |

## Outputs

| Output | Where |
|---|---|
| Sub-questions with their company and fiscal-year filters; top k with rerank scores | terminal (`tenk search`) |
| V1 experiment: per question, Recall@k, reciprocal rank, first gold rank, every search's candidates | `output/retrieval/<label>/results.json` |
| V1 experiment: totals, by kind, missed and unscorable questions | terminal, `output/retrieval/<label>/scores.json` |

## How it works

```
question ─► QueryDecomposer ─► sub-questions ─► HybridSearch each: keyword + dense ─► RRF ─► rerank ─► round-robin ─► top k
```

| Step | Code | What happens | "Compare Apple's and NVIDIA's revenue in fiscal 2025" |
|---|---|---|---|
| 1. Names | `retrieval/decompose.py` | Companies by ticker, name or alias ("Google" → GOOGL); years, ranges and "last three years". A bare calendar year also takes the fiscal year that mostly covers it ("2024" → NVDA FY2025) | AAPL, NVDA; FY2025 |
| 2. Decompose | `QueryDecomposer` | One sub-question per (company, filing), rewritten to name only its company; "which company…" with none named: one per company in the corpus | "Compare Apple's revenue in fiscal 2025", "Compare NVIDIA's revenue in fiscal 2025" |
| 3. Search | `retrieval/search.py` `HybridSearch` | Keyword (BM25 over FTS5) and dense (cosine over sqlite-vec), each 40 candidates filtered to the sub-question's filing; fused by RRF; reranked. On a parent-child database the children are searched and each is swapped for its parent, best child first | 2 searches |
| 4. Merge | `SubQuestionRetriever` | Round-robin: every sub-question's best chunk, then every one's second, …; each gets ⌈k / sub-questions⌉ places (at least 2); the best-scoring sub-question goes first in each round | 8 chunks, 4 per company |

Each chunk and child is embedded and reranked under a header line naming its company, fiscal year and Item (`Apple Inc. (AAPL) fiscal 2025 10-K, Item 8 Financial Statements`, `retrieval/index.py`), so a chunk that never names its company still matches. About 0.9 s per question on the L40S.

## Gold questions

One JSON object per line ([`evaluation/gold.py`](../src/tenk_agent/evaluation/gold.py)); the V2 set adds 8 declines to V1's 46. `tenk gold --gold <file>` lists a set with its evidence resolved; `tenk gold show <id>` prints one with its source text; `tenk gold render` writes the review PDFs; `tenk gold verify <id> --by <initials>` records a reviewer's sign-off.

| Field | Meaning |
|---|---|
| `id`, `split`, `kind` | Unique id; `dev` / `test`; the question kind (`figure`, `risk`, `drivers`, `compare_company`, `compare_year`, `open`, `decline`) |
| `question` | As a user would ask it |
| `answer_type`, `expected_answer`, `tolerance` | `number` (`{value, unit}`), `number_set` (a list, each with `company`, `fiscal_year`), `text` or `abstain`; relative tolerance for numbers (0.5%) |
| `required_points` | What a text answer must state (V2's judge) |
| `should_abstain` | The filings can't answer: the system should decline |
| `sources` | Each: `company`, `fiscal_year`, `accession_no`, `item`, `section` and a verbatim `evidence` line; a chunk containing it is gold |
| `acceptable_sources` | The same figure restated elsewhere (an MD&A table, a note); `restates` is the index of the source it counts for |
| `category`, `failure_mode`, `notes`, `origin`, `verified_by`, `verified_on` | Bookkeeping and review |

Gold chunks are resolved from the evidence quotes at scoring time, so re-chunking never invalidates a set; a question whose quote no chunk contains is unscorable on that database.

## Things to try

| Try | Question |
|---|---|
| `tenk search "Which company spent the most on property and equipment in fiscal 2025?"` | What does each company's sub-question look like? Which lines come back? (No filer calls it "capital expenditures") |
| `slot_search: false` in a copy of the V1 config | Which question kinds lose recall? ([§6](../experiments/V1_RETRIEVAL.md#6-without-slot-search)) |
| `--db data/v0-chunking/rule.db` | Where does rule-based beat parent-child? (Risk factors) |
| `reranker: none` | How far do the gold chunks fall? |
| `"NVIDIA revenue in 2024"` vs `"… in fiscal 2024"` | Which filings does each search? |

## Gotchas

- **Embedder and database must match.** `from_settings` refuses a database whose vectors came from another model; `--stage embed` re-embeds when the model changes.
- **Recall is per source.** A question with four gold sources needs four different chunks in the top k for 100%.
- **Companies outside the corpus** (Oracle) give no sub-question; the search runs unfiltered and returns the closest corpus chunks. Declining is V2's job.
- **GPU memory.** With the 27B served, the embedder and reranker fit (~8 GB left); embedding a whole corpus is faster with vLLM stopped.
