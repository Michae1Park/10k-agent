# Contributing to 10k-agent

10k-agent is an agentic research assistant that answers grounded, cited questions over SEC 10-K filings, measured by an automated evaluation suite.

- What and why: [docs/PRD.md](docs/PRD.md)
- How (architecture, tools, roadmap): [docs/project-plan.md](docs/project-plan.md)
- Gold dataset rules: [docs/eval/gold-set-guide.md](docs/eval/gold-set-guide.md)

**Status:** V0 built (fetch, ingestion, 30 draft gold questions); V0 completes when the gold questions are verified.

## Development

```bash
uv sync                                   # install (or: python -m venv .venv && .venv/bin/pip install -e . pytest ruff)
uv run pytest                             # tests

# Download the 24 corpus 10-Ks into data/ (git-ignored). SEC requires a contact in the User-Agent.
export SEC_USER_AGENT="10k you@example.com"
uv run tenk fetch                         # all companies; cached, safe to re-run
uv run tenk fetch --tickers AAPL --refresh

uv run tenk ingest                        # parse + chunk into data/tenk.db (~30 s)
uv run tenk gold                          # per question: verified?, evidence found, XBRL match
uv run tenk gold show <id>                # a question with its evidence, EDGAR link and XBRL check
uv run tenk gold verify <id>... --by XX   # record your sign-off after checking the filing
uv run tenk check                         # V0 exit criteria

uv run ruff format && uv run ruff check   # formatting and lint (100-character lines)
```

Downloads go to `data/raw/filings/<TICKER>/FY<year>/` (primary HTML + `metadata.json`) with `data/raw/manifest.json`; the database is `data/tenk.db`.

## Product rules

These are invariants. Changes that break them are bugs.

- Every number in an answer comes from a tool result. The model never states a figure from memory.
- All arithmetic goes through the `calculator` tool, never the model.
- Fiscal years come from filing metadata (`filings.fiscal_year`), never inferred from text.
- Every claim cites chunk IDs. Unverifiable claims are labeled unverified, not dropped or hidden.
- If the corpus can't answer, say so and state what it covers (FY2023–FY2025, 8 companies).
- No stock prices, forecasts or investment advice.

## Engineering decisions

- Python 3.12 with uv; FastAPI; Next.js frontend.
- Storage: one SQLite file (FTS5 now, sqlite-vec from V1). All access goes through `Store`; nothing else touches the database directly.
- The agent is a hand-written tool-use loop. Don't add agent frameworks.
- All model calls go through the model layer (`complete(messages, tools)`), never a provider SDK directly. Claude is the baseline; open-source models served with vLLM are the target.
- The LLM judge stays the same fixed model across all runs, whatever model is under test.
- Keep the toolset to the four MVP tools unless the plan changes.
- EDGAR: always send a declared User-Agent and stay at or below 10 requests/s. Cache raw filings; don't re-download.

## Evaluation and training data

- Tune prompts and retrieval on the `dev` split only. `test` is for reported results.
- Never edit the gold set to make a run pass. Gold fixes are separate `fix:` commits (see the guide).
- Report metrics as measured. Never estimate or round up.
- Never train or fine-tune on gold-set questions or eval filings.
- Never use the baseline model's inputs or outputs (trajectories, generated questions, answers) as training data. Anthropic's Usage Policy prohibits it without prior authorization. Training data comes from open models only.

## Docs

- When implementation changes the design, update `docs/project-plan.md` in the same commit.
- Record decisions with real alternatives as `docs/decisions/NNN-title.md`, one page each.
- The PRD holds what and why; the plan holds how. Don't duplicate between them.

## Commits

- One concise line with a type prefix: `feat`, `fix`, `docs`, `chore`, `refactor`, `style`, `test`.
- Commits are signed.
