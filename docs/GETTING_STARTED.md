# Getting started

Install, build the V4 corpus, write a research report, ask questions, run the evaluations. V0–V3 (the 10-K regression corpus) is at the end. How it is built: [ARCHITECTURE.md](ARCHITECTURE.md) and the [V4 design doc](V4_TECHNICAL_DESIGN_DOC.md).

## Requirements

| | Minimum | Tested |
|---|---|---|
| OS | Linux or macOS | Ubuntu 24.04 |
| Python | 3.12 + [uv](https://docs.astral.sh/uv/) | 3.12 |
| GPU | ~40 GB for the default 27B model on vLLM; the embedder and reranker also run on CPU, slower | L40S, 46 GB |
| Model | An open model on vLLM, or `ANTHROPIC_API_KEY` (Claude) | Qwen3.6-27B-FP8; judge gpt-oss-20b |
| Export | LibreOffice (`soffice`) and Chrome or Chromium on `PATH` (workbook parity, note PDF) | |
| Keys | Your own SEC User-Agent, Tiingo and FRED API keys (free) | |
| Disk | 10 GB, plus the V4 data (`data/v4/`; NYC TLC is ~540 MB a month, aggregated at fetch) | |

All commands run from the repo root. Heavy jobs run pinned to the E-cores: `taskset -c 16-31` ([I-001](DECISIONS.md#i-001)).

## 1. Install

```bash
git clone <this repo> && cd 10k-agent
uv sync --extra local --extra chunking     # Python deps + local embedder / reranker + the chunkers
source .venv/bin/activate
tenk setup                                 # your SEC User-Agent, Tiingo and FRED keys → credentials.yaml (gitignored)
export TENK_COMPANIES=companies.yaml       # the V4 corpus (default: companies-v3.yaml)
```

Credentials are yours alone: `tenk setup` prompts for each and writes `credentials.yaml` (mode 600, gitignored; template [`credentials.example.yaml`](../credentials.example.yaml)). Environment variables `SEC_USER_AGENT`, `TIINGO_API_KEY` and `FRED_API_KEY` take precedence. A command that needs a missing one stops with an error naming it. Downloaded data (`data/`) and run outputs (`output/`) stay local and are never committed; Tiingo prices are for internal use only.

Settings: [`config.yaml`](../config.yaml) (retrieval, agent, generation), [`companies.yaml`](../companies.yaml) (coverage), [`research.yaml`](../research.yaml) (forecast, valuation, LLM budgets). All knobs: [design doc Appendix A](V4_TECHNICAL_DESIGN_DOC.md#appendix-a-config).

## 2. Serve a model

KPI extraction, the Report's LLM steps and Ask and Research use an open model on vLLM, in its own terminal:

```bash
scripts/serve_vllm.sh qwen3.6-27b-fp8      # the default; ready at "Application startup complete"
```

Presets, the judge, other models and GPU memory: [SERVING.md](SERVING.md). Fetching, the rule-based text build, facts, market data and `--no-llm` reports need no model server.

## 3. Build the V4 corpus

```bash
tenk companies verify                                        # CIKs, December year-ends, filing counts vs SEC
tenk fetch                                                   # 10-K, 10-Q, earnings releases, DEF 14A from 2023 (cached)
tenk fetch --market                                          # prices, macro (ALFRED), ERP, cross-checks
taskset -c 16-31 tenk build --stage chunk --stage load       # rule-based Items and chunks (no LLM, D-039) → data/v4/build/parent_child.db
taskset -c 16-31 tenk build --stage facts --stage market     # XBRL and inline XBRL facts; market series
taskset -c 16-31 tenk build --stage embed                    # vectors; stop vLLM first or set retrieval.device: cpu
taskset -c 16-31 python experiments/v4/extract_pilot.py --releases all   # KPI and guidance extraction (model served)
tenk build --stage kpis                                      # load the validated rows
```

| Variation | Command |
|---|---|
| New filings since the last build | `tenk fetch`, then `tenk build --stage append --stage embed` (keeps facts, KPIs, versions) |
| Some forms only | `tenk fetch --forms 10-Q ER` |
| Extract with Claude | `extract_pilot.py --model anthropic:<id>` |

What each table holds: [design doc §3](V4_TECHNICAL_DESIGN_DOC.md#3-data-layer). Every read sees only data filed **before** a run's as-of date.

## 4. Write a report

```bash
taskset -c 16-31 tenk research report UBER --as-of 2026-08-06              # S1–S11, under 30 min
taskset -c 16-31 tenk research report UBER --as-of 2026-08-06 --resume     # after a crash: reuse the saved LLM steps
tenk research report UBER LYFT DASH CART --as-of 2026-08-06 --no-llm       # code-only drafts, no model server
tenk research approve UBER:v2 --by MP                                      # mark a version reviewed
```

Output: `output/reports/<TICKER>/<as_of>/` (`note.md`, `note.pdf`, `workbook.xlsx`, the JSON artifacts), saved as a version (`UBER:v1`, `UBER:v2`, …) with its parent. A finished example: [examples/](examples/). Steps and artifacts: [design doc §9](V4_TECHNICAL_DESIGN_DOC.md#9-report-pipeline).

Parts on their own:

```bash
python -m tenk_agent.research.model.history UBER 2026-08-06    # history model and layer-2 checks
python -m tenk_agent.research.forecast UBER 2026-08-06         # forecast
python -m tenk_agent.research.valuation UBER 2026-08-06        # valuation; --all adds relative ratings
```

## 5. Ask and Research

Ask and Research run through their gold runners, each question at its own as-of (model served):

```bash
taskset -c 16-31 python experiments/v4/ask_run.py --question <id>          # Ask: cited answers from filed data
taskset -c 16-31 python experiments/v4/research_run.py --question <id>     # Research: model tools, assumptions shown
tenk tool --list                                                           # the tool schemas (design doc §5)
```

Questions: `evaluation/gold/ask-gold.jsonl`, `research-gold.jsonl`. Results: `output/generation/<label>/`.

## 6. Evaluate

The five layers and their frozen bars: [design doc §12](V4_TECHNICAL_DESIGN_DOC.md#12-evaluation). Results: [V4_RESULTS.md](V4_RESULTS.md).

```bash
taskset -c 16-31 python -m tenk_agent.evaluation.backtest --label defaults        # layer 3: walk-forward backtest, baselines
taskset -c 16-31 python -m tenk_agent.evaluation.backtest --proposer llm          # ablation: the LLM proposer
python -m tenk_agent.evaluation.note_eval output/reports/UBER/2026-08-06 --seeds 5  # layer 5: trace, sections, seeded errors
taskset -c 16-31 python experiments/v4/ask_run.py                                  # all Ask gold, then --score
taskset -c 16-31 python experiments/v4/research_run.py                             # all Research gold, then --score
```

The judge (`gpt-oss-20b`) doesn't fit beside the 27B: generate first, stop the 27B, serve the judge, then judge and `--score` ([SERVING.md](SERVING.md#gotchas)). Ablation configs: `experiments/v4/knobs/`.

## 7. V0–V3: the 10-K regression corpus

V3 answers questions about 24 10-Ks and stays runnable as a regression check. How it works: [V3 architecture](stages/V3_ARCHITECTURE.md). Unset `TENK_COMPANIES` (or set `companies-v3.yaml`) first.

```bash
tenk fetch                                                               # the 24 10-Ks from EDGAR (cached)
taskset -c 16-31 tenk build --stage chunk --jobs 8                       # V0 with the LLM: ~35–60 min a filing, 8 at once
taskset -c 16-31 tenk build --stage load --stage embed                   # V1's database and vectors; stop vLLM first
tenk search "How much did NVIDIA spend on R&D in fiscal 2025?"          # V1: sub-questions, then the top chunks
tenk ask "What were Apple's total net sales in fiscal 2025?"            # V2: answer, claims with their status
tenk agent "Which of the four companies had the highest operating margin?"   # V3: the agent with tools
tenk chat                                                               # type questions; models loaded once
python experiments/v4/v3_regress.py                                     # byte identity of the shared pipeline
```

Experiments, each scoring one version ([experiments/README.md](../experiments/README.md)):

```bash
taskset -c 16-31 python experiments/v0/run.py        # V0: chunk an excerpt three ways, score vs the ground truth
taskset -c 16-31 python experiments/v1/run.py        # V1: Recall@k and MRR on 46 gold questions
taskset -c 16-31 python experiments/v2/run.py        # V2: 54 questions × three contexts
taskset -c 16-31 python experiments/v2/judge.py      # V2: judge the text answers, with the judge served instead
tenk report qwen3.6-27b-fp8                          # a finished V2 run → evaluation/results/<run>/report.md
```

| Option | Does |
|---|---|
| `tenk build --stage chunk --filing NVDA/FY2025` | One filing |
| `tenk build --method rule --method parent_child` | Other chunkings, side by side |
| `v1/run.py --db data/v0-chunking/rule.db --label v0-rule` | V1 on another chunking |
| `v2/run.py --dry-run --question nvda-rd-fy2025` | The exact prompts, no model call |
| `tenk gold show aapl-net-sales-fy2025` | A gold question with its source text |

From Python:

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
question = "Which company spent the most on R&D in fiscal 2025?"
result = retriever.search(question, k=8)                     # .chunks, .searches
system, user = build_prompt(question, result.chunks, store)
answer = generate(get_model("openai:Qwen/Qwen3.6-27B-FP8"), system, user, store)
answer.answer["claims"]                                      # each with status and status_reason
```

Things to try: change `chunking.target` and rerun V0's scoring; set `slot_search: false` or `reranker: none` in a copy of the V1 config ([V1 §6](../experiments/V1_RETRIEVAL.md#6-without-slot-search)); `tenk ask "What will Apple's revenue be in 2027?"` should decline with a scope notice.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Python segfaults at random | Run with `taskset -c 16-31` ([I-001](DECISIONS.md#i-001)) |
| GPU out of memory | [SERVING.md](SERVING.md#gotchas) ([I-002](DECISIONS.md#i-002)) |
| vLLM out of memory or a missing `nvcc` | [I-003](DECISIONS.md#i-003) |
| "Report mode needs the V4 corpus" | `export TENK_COMPANIES=companies.yaml` |
| A credential error naming a key | `tenk setup` |
| Export raises `RuntimeError` | Install LibreOffice and Chrome or Chromium; no version is saved until export works |
| A report crashed midway | Rerun with `--resume` |
| Tests fail on collection | `env -u PYTHONPATH uv run pytest` ([I-010](DECISIONS.md#i-010)) |
| Answers take minutes | Leave `generation.thinking: false` |
| "labeled with … not …" in a V0 run | Labels are cached by their settings: `--relabel`, or another `run.label` |

Stage-specific gotchas are at the end of each stage spec in [stages/](stages/).
