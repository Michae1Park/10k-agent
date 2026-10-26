# Archive: V3–V6 (reference only)

Old code kept only to rebuild V4–V6 from; not part of the project, and nothing outside this folder links to it. Delete each piece once its version is rebuilt, or the whole folder when no longer needed. **Not
maintained and not runnable as is:** nothing here is packaged, imported or tested, and it was
written against the old V0–V2 pipeline (`parse`, `chunking`, `ingest`, `retrieval.Retriever`,
`ask`) that the renewed pipeline in `src/tenk_agent/` replaced. Git history has everything else.

| Version | Code | Guide |
|---|---|---|
| V3 · Agent (Research mode) | `src/tenk_agent/agent.py`, `tools.py`, `tracing.py`; rebuilt in the project's `agent/`, only the old `tracing.py` (traces, Langfuse) has no successor | |
| V4 · Verified agent | `verify.py` (cell checks), `agent.py` (repair round), `evaluation/judge.py` | [V4_VERIFIED_AGENT.md](docs/V4_VERIFIED_AGENT.md) |
| V5 · Model comparison | `evaluation/compare.py`, `stats.py`, `analysis.py` | [V5_MODELS.md](docs/V5_MODELS.md) |
| V6 · Fine-tune + ablation | `finetune/`, `xbrl.py`, `scripts/finetune.sh` | [V6_FINETUNE_ABLATION.md](docs/V6_FINETUNE_ABLATION.md) |
| Evaluation harness | `evaluation/` (runner, report, calibrate), `scripts/eval_all.sh` | [EVAL.md](docs/EVAL.md) |
| API server | `api.py` (`tenk serve`) | |
| Decisions and issues of V3–V6 | | [DECISIONS.md](docs/DECISIONS.md) |

`cli.py`, `config.py` (with `config.yaml`), `ask.py`, `answers.py`, `retrieval.py`, `store.py` and
`verify.py` are the shared modules as V3–V6 used them. They still carry V7 (advanced retrieval)
code; V7 itself was folded into the renewed V0 (parent-child chunking) and V1 (hybrid search over
children) and is otherwise deleted.
