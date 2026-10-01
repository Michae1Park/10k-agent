# Evaluation: gold set, metrics, runs

## Commands

```bash
source .venv/bin/activate
tenk gold                                          # per question: verified?, evidence found, XBRL match
tenk gold show aapl-rd-fy2024                      # question, expected answer, evidence + EDGAR link, XBRL check
tenk gold verify aapl-rd-fy2024 --by MP            # your sign-off, after checking the filing
tenk check                                         # V0 exit criteria (filings + 30 verified questions)

tenk eval run retrieval --split test               # V1 · verified questions only, unless --include-unverified
tenk eval run ask --split test                     # V2
tenk eval run research --split test --no-repair    # V3
tenk eval run research --split test                # V4
tenk eval run research --split test --structured-data --label v5-xbrl    # V5 experiment
tenk eval rescore <label>                          # recompute deterministic metrics, no model calls
tenk eval report V1=<label> V2=<label> V3=<label> V4=<label> --out eval/results.md
tenk eval compare V4=<label> V7=<label> [V6=<label>]     # baseline first: intervals, paired tests, failure causes, charts
tenk eval calibrate export <label>                 # judge calibration: fill in "human", then
tenk eval calibrate score <label>
scripts/eval_all.sh                                # V1–V4 + report in one command
```

| Flag | Effect |
|---|---|
| `--split dev \| test \| all` | Tune on `dev` only; report `test` |
| `--gold <file>` | Another question file, e.g. V7's held-out training companies ([FINETUNE.md](FINETUNE.md)) |
| `--include-unverified` | Also run draft questions; results say so |
| `--no-judge` | Skip LLM-judge metrics (shown as n/m) |
| `--workers N` | Questions in parallel (vLLM batches them) |
| `--resume` | Continue an interrupted run ([I-001](DECISIONS.md#i-001)) |
| `--model`, `--mode`, `--reranker`, `--no-slot-search` | Override `config.yaml` for this run |

## Data

| Data | Where |
|---|---|
| **Gold questions** | `eval/gold/questions.jsonl`, one per line |
| Reference trajectories (unnecessary-calls metric) | `eval/gold/reference_trajectories.json` |
| **Runs** | `eval/runs/<label>/`: `config.json`, `results.jsonl` (one scored row per question), `summary.json` |
| Results table | `eval/results.md` |
| Comparison reports | `eval/reports/<name>.{md,html,json}` |
| Judge calibration | `eval/calibration/<label>.jsonl` |

**Gold set now:** 100 questions, all `drafted`, none verified ([I-011](DECISIONS.md#i-011)).

| Category | Count | Tests |
|---|---|---|
| retrieval | 30 | one reported value |
| grounding | 15 | narrative answer, required points |
| multi_document | 15 | values from several filings or companies |
| tool_use | 15 | a calculated value |
| agent_workflow | 10 | figures + calculations + management's explanations |
| failure_case | 15 | the seven failure modes below, ≥ 2 each |

## Metrics

| Metric | How it's scored | Judge? |
|---|---|---|
| Recall@5, MRR | Gold chunks vs retrieved chunks, per primary source | no |
| Evidence recall (agent) | Gold chunks among everything the agent's tools returned | no |
| Answer correctness | Numbers: a claim matches each expected value (unit-normalized, `tolerance`, right company and year). Text: share of `required_points` covered. Abstain: declined | text only |
| Citation accuracy | Numeric claims verified or calculated (stage 5) | no |
| Faithfulness | Every claim supported by its cited chunks | yes |
| Context relevance | Retrieved chunks that help answer | yes |
| Task completion | All expected values right / all points covered / declined when it should | text only |
| Tool selection | `expected_tools` ⊆ tools used | no |
| Tool-call correctness | Calls with valid arguments; over-budget calls excluded ([D-016](DECISIONS.md#d-016)) | no |
| Unnecessary calls | Calls beyond the reference trajectory, else duplicates; plus over-budget calls | no |
| Unsupported-answer rate | Answered although `should_abstain` (completed questions only, [D-017](DECISIONS.md#d-017)) | no |
| Latency, tokens, cost | From traces; self-hosted cost = latency × GPU rate ([D-020](DECISIONS.md#d-020)) | no |

The judge is fixed (`eval.judge_model`, Claude Haiku 4.5) whatever model is under test ([D-014](DECISIONS.md#d-014)). Calibrate it once: hand-grade ~30 answers, report agreement and Cohen's kappa.

## Comparing runs

`tenk eval compare BASE=<label> NAME=<label> ...` answers "is the new version better, and where?". The first run is the baseline; paired statistics use the questions both runs answered. It writes Markdown (for the repo), a self-contained HTML page with charts, and the numbers as JSON.

| Section | What it shows | Why |
|---|---|---|
| Metrics | Every metric with a **95% bootstrap interval** over questions | 31 questions → about ±10 points; a single percentage hides that |
| Differences | Candidate − baseline per metric: bootstrap interval + **paired sign-flip permutation p** | Paired: same questions, so question difficulty cancels out. "Better/worse" only at p < 0.05 ([D-027](DECISIONS.md#d-027)) |
| Question-level changes | Fixed / regressed / both pass / both fail, **exact McNemar p** | Only questions that changed carry information; regressions are listed by ID to read first |
| Failure causes | Each failure's first broken stage: error · format · false abstention · unsupported · retrieval miss · over budget · wrong value · citation | Error analysis: tells you which stage to improve next |
| By category | Task completion per gold category, with intervals | Where a version helps or hurts |
| Quality vs latency | Task completion against seconds per question | Cost of a gain |
| Sample size | Questions needed for a ±5-point interval | Whether the gold set can detect the change you care about |

**Charts** (HTML report): differences as a dot-and-interval (forest) plot, quality vs latency as a scatter, failure causes and categories as grouped bars; hover for exact values. Every chart has a table with the same numbers.

**Reading it.**
- "No clear difference" means the test set can't tell, not that the versions are equal. With ≤ 5 changed questions no result can reach p < 0.05.
- Decide on one primary metric before looking (task completion). The rest are diagnostics; with ~10 metrics, one "significant" by chance is expected.
- Read the regressed questions' traces before trusting an aggregate gain.
- Temperature 0 still varies slightly run to run on vLLM; for a close call, repeat both runs and compare again.

**Common tools elsewhere**, for reference: [Inspect](https://inspect.aisi.org.uk/) and [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) (eval harnesses), [RAGAS](https://docs.ragas.io/) and [DeepEval](https://deepeval.com/) (RAG and LLM-judge metrics), [promptfoo](https://www.promptfoo.dev/) (prompt regression tests), [Langfuse](https://langfuse.com/) and [Phoenix](https://phoenix.arize.com/) (traces; Langfuse export is built in). This harness stays custom because its checks are domain-specific (fiscal years, unit normalization, chunk citations) and deterministic.

## Gold question format

```json
{
  "id": "aapl-rd-fy2024", "split": "test", "category": "retrieval", "failure_mode": null,
  "question": "How much did Apple spend on research and development in fiscal 2024?",
  "answer_type": "number", "expected_answer": {"value": 31370, "unit": "USD millions"},
  "tolerance": 0.005, "required_points": [], "should_abstain": false,
  "sources": [{"company": "AAPL", "fiscal_year": 2024, "accession_no": "0000320193-24-000123",
               "item": "8", "section": "Consolidated Statements of Operations",
               "evidence": "Research and development | 31,370 | 29,915 | 26,251"}],
  "acceptable_sources": [{"company": "AAPL", "fiscal_year": 2025, "accession_no": "0000320193-25-000079",
               "item": "8", "section": "Consolidated Statements of Operations",
               "evidence": "Research and development | 34,550 | 31,370 | 29,915"}],
  "expected_tools": ["search_filings"],
  "notes": "", "origin": "drafted", "verified_by": "", "verified_on": null
}
```

| Field | Rule |
|---|---|
| `id` | `<ticker>-<topic>-<year or range>`, lowercase; never reused |
| `split` | `dev` (~70%) or `test` (~30%) |
| `answer_type` | `number`, `number_set` (list, each with `fiscal_year`, `company` if several), `text` (2–5 `required_points`), `abstain` |
| `expected_answer` | **The table's own unit**, as printed: 31,370 in a table "in millions" → `31370`, `USD millions`. Negative as negative. Percent as `12.5` |
| Units | `USD millions`, `USD thousands`, `USD`, `percent`, `shares millions`, `count`, `count thousands` |
| `tolerance` | 0.005 (0.5%); 0.01 for calculated percentages |
| `sources` | **That fiscal year's 10-K** is the primary source. `evidence` is copied verbatim: a sentence, or a table row as `Label \| cell \| cell` |
| `acceptable_sources` | The same value in a later filing (not if restated: note it instead) |
| `expected_tools` | The minimum a correct answer needs |
| `origin` | `manual` or `drafted` (generated, then reviewed) |

- **Fiscal years** use the filer's label (NVIDIA's year ending January 2025 is FY2025). A bare calendar year for a non-December filer is a failure case; `notes` says how to read it.
- **Corpus range** is FY2023–FY2025. Any other year is `unsupported`, even if it appears as a comparative column ([I-005](DECISIONS.md#i-005)).
- **Calculated answers** are computed by script from verified inputs; inputs go in `notes`.

**Failure modes** (each ≥ 2 questions): `ambiguous_fiscal_year` · `shifted_fiscal_year` · `similar_numbers` · `unit_normalization` · `entity_naming` · `unsupported` · `false_premise`. Required behavior: [STAGE3_ANSWER.md](STAGE3_ANSWER.md#how-it-works).

## Verifying a question

1. `tenk gold show <id>`: the question, expected answer, each evidence quote with its EDGAR link and matched chunk, and the XBRL check.
2. Open the filing on EDGAR and find the answer yourself.
3. Check the value, unit, fiscal year and evidence; for segment figures no XBRL match is expected.
4. Add correct alternatives you notice to `acceptable_sources` (e.g. MD&A tables, [I-004](DECISIONS.md#i-004)).
5. `tenk gold verify <id> --by <initials>`. Re-check a random ~20% a few days later.

## Rules

- **Never edit the gold set to make a run pass.** A wrong gold answer is fixed in its own `fix:` commit, and every version's results are re-run.
- Retire a question with `"retired": true` and a reason in `notes`; never delete it or reuse its id.
- Write questions from the filing, never from what the retriever returned.
- Every company in ≥ 6 questions, none in more than 25%.

## Gotchas

- **Draft results are provisional.** Reports mark runs that include unverified questions.
- **Correctness without the judge covers only numeric and abstention questions** (21 of 31 on `test`); the report says how many.
- `eval/runs/` keeps every run; labels are directory names, so reusing a label overwrites it (use `--resume` to continue instead).
