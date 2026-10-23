# Getting started

Install, build the corpus, ask questions, run the experiments. How each stage works: [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

| | Minimum | Tested |
|---|---|---|
| OS | Linux or macOS | Ubuntu 24.04 |
| Python | 3.12 + [uv](https://docs.astral.sh/uv/) | 3.12 |
| GPU | ~40 GB for the default 27B model on vLLM; the embedder and reranker also run on CPU, slower | L40S, 46 GB |
| Model | An open model on vLLM, or `ANTHROPIC_API_KEY` (Claude) for answers | Qwen3.6-27B-FP8; judge gpt-oss-20b |
| Disk | 10 GB (filings 150 MB, local models 3.5 GB, venv 6 GB) | |

All commands run from the repo root.

## 1. Install

```bash
git clone <this repo> && cd 10k-agent
uv sync --extra local --extra chunking     # Python deps + local embedder / reranker + the chunkers
source .venv/bin/activate
export SEC_USER_AGENT="Your Name you@example.com"    # SEC requires a contact
```

Every setting is in [`config.yaml`](../config.yaml), one section per stage; `TENK_CONFIG=<file>` or `--config` uses another.

## 2. Serve a model

The structure agent (V0), the agentic chunker and the V2 answers use an open model on vLLM, in its own terminal ([SERVING.md](SERVING.md) has the presets and the model layer):

```bash
scripts/serve_vllm.sh qwen3.6-27b-fp8      # the default; ready at "Application startup complete"
```

Rule-based and parent-child chunking and `tenk search` need no model server. To answer with Claude instead, set `ANTHROPIC_API_KEY` and pass `--model anthropic:<id>`.

## 3. Build the corpus

Pin heavy jobs to the E-cores ([I-001](DECISIONS.md#i-001)).

```bash
tenk fetch                                                               # the 24 10-Ks from EDGAR (cached)
taskset -c 16-31 tenk build --stage chunk --jobs 8                       # V0 with the LLM: ~35–60 min a filing, 8 at once
taskset -c 16-31 tenk build --stage load --stage embed                   # V1's database and vectors; stop vLLM first (embedder on the GPU)
```

| Variation | Command |
|---|---|
| One filing | `tenk build --stage chunk --filing NVDA/FY2025` |
| Other chunkings, side by side | `tenk build --method rule --method parent_child` |

Where files land and what each holds: [V0 · Data](stages/V0_PARSING_CHUNKING.md#data). `--stage load` rebuilds the database from scratch, so run `embed` again afterwards.

## 4. Ask

```bash
tenk search "How much did NVIDIA spend on R&D in fiscal 2025?"          # V1: sub-questions, then the top chunks with rerank scores
tenk search "Compare the net income of Apple, Amazon, Microsoft and NVIDIA in fiscal 2025." -k 8
tenk ask "What were Apple's total net sales in fiscal 2025?"            # V2: chunks, answer, claims with their status
tenk ask "Should I buy NVIDIA stock?"                                   # should decline
tenk ask "How did Amazon's net income change from 2023 to 2025?" --json
tenk chat                                                               # type questions; models loaded once
tenk chat --model openai:Qwen/Qwen3.5-9B                                # another served model
tenk gold show aapl-net-sales-fy2025                                    # a gold question with its source text
tenk gold --gold evaluation/gold/retrieval-gold-fy2025.jsonl render   # review PDFs (card per question + summary) next to the set; git-ignored
```

## 5. Run the experiments

Each experiment runs and scores one version; results go to `output/` and are written up in `experiments/V*.md` ([experiments/README.md](../experiments/README.md)).

```bash
taskset -c 16-31 python experiments/v0/run.py        # V0: preprocess an excerpt, chunk it three ways, score vs the ground truth
python experiments/v0/sanity.py                      # V0: the 21 hand-checkable cases
taskset -c 16-31 python experiments/v1/run.py        # V1: Recall@k and MRR on 46 gold questions
taskset -c 16-31 python experiments/v2/run.py        # V2: 54 questions × three contexts (V1's chunks · perfect retrieval · none)
taskset -c 16-31 python experiments/v2/judge.py      # V2: judge the text answers, with the judge served instead
tenk report qwen3.6-27b-fp8                          # a finished V2 run → evaluation/results/<run>/report.md (per-question tables)
```

| Option | Does |
|---|---|
| `v1/run.py --db data/v0-chunking/rule.db --label v0-rule` | V1 on another chunking |
| `v1/run.py --question aapl-net-sales-fy2025` | One question |
| `v1/build_corpus.py --stage load --stage embed` | The three corpora V1 compares |
| `v2/run.py --dry-run --question nvda-rd-fy2025` | The exact prompts, no model call |
| `v2/run.py --context retrieved --question aapl-tariffs-fy2025` | One context for one question |

The V2 judge (`gpt-oss-20b`) doesn't fit beside the 27B: generate first, stop the 27B, serve the judge, then run `judge.py`.

## 6. Use it from Python

```python
from pathlib import Path
from tenk_agent.config import load
from tenk_agent.generation.generate import build_prompt, generate
from tenk_agent.models import get_model
from tenk_agent.retrieval.search import SubQuestionRetriever
from tenk_agent.store import Store

settings = load()
store = Store(Path(settings["db"]))
retriever = SubQuestionRetriever.from_settings(store, settings["retrieval"])

result = retriever.search("Which company spent the most on R&D in fiscal 2025?", k=8)
result.chunks  # the top 8 ChunkRecords (parents, on a parent-child database)
result.searches  # per sub-question: its text, filters, keyword and dense candidates, rerank scores

system, user = build_prompt(question, result.chunks, store)  # prompts/answer.md + the excerpts
answer = generate(get_model("openai:Qwen/Qwen3.6-27B-FP8"), system, user, store)
answer.answer["claims"]  # each with status and status_reason
```

V0 on one filing by hand:

```python
from pathlib import Path
from tenk_agent.preprocess import preprocess
from tenk_agent.chunking import run_all
from tenk_agent.config import load

settings = load()
html = next(Path("data/raw/filings/AAPL/FY2025").glob("*.htm"))
document = preprocess(
    html, html, Path("output/aapl"), settings["llm"], settings["labeling"]
)  # labels cached in output/aapl/
chunks = run_all(document, settings["chunking"], **settings["llm"], methods=["parent_child"])[
    "parent_child"
]
```

## 7. Things to try

| Try | Question |
|---|---|
| `experiments/v0/run.py --stage chunk --stage score` after changing `chunking.target` | What happens to path exactness and one-risk-factor-per-chunk? |
| Read `review` in a `document.json` | Which blocks did the structure agent leave uncertain, and why? |
| `--relabel` with `labeling.window: 50` | More calls; fewer violations? |
| Compare `chunks-rule.json` and `chunks-parent_child.json` for one table | What do the row children add for search? |
| `tenk search "Which company spent the most on property and equipment in fiscal 2025?"` | What does each company's sub-question look like? (No filer calls it "capital expenditures") |
| `slot_search: false` in a copy of the V1 config | Which question kinds lose recall? ([V1 §6](../experiments/V1_RETRIEVAL.md#6-without-slot-search)) |
| `reranker: none` | How far do the gold chunks fall? |
| `experiments/v2/run.py --dry-run --question nvda-revenue-growth-fy2024-2025` | What exactly does the model know about fiscal years? |
| `tenk ask "Which company reported the highest revenue in fiscal 2025?"` | Are all four companies in the chunks? Does it name a winner anyway? ([V2 §5](../experiments/V2_GENERATION.md#where-generation-fails)) |
| `tenk ask "What will Apple's revenue be in 2027?"` | Does it decline, with a scope notice? |
| `generation.thinking: null` | How much slower, and is it better? |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Python segfaults at random | Run with `taskset -c 16-31` ([I-001](DECISIONS.md#i-001)) |
| GPU out of memory | Stop vLLM before `--stage embed`, or set `retrieval.device: cpu` ([I-002](DECISIONS.md#i-002)) |
| vLLM out of memory or a missing `nvcc` | [I-003](DECISIONS.md#i-003) |
| Tests fail on collection | `env -u PYTHONPATH uv run pytest` ([I-010](DECISIONS.md#i-010)) |
| Answers take minutes | Leave `generation.thinking: false` |
| "labeled with … not …" in a V0 run | Labels are cached by their settings: `--relabel`, or another `run.label` |

Stage-specific gotchas are at the end of each stage spec in [stages/](stages/).
