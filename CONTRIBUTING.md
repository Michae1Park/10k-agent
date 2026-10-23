# Contributing

Set up and run the project: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). How it is built: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Checks

```bash
uv run ruff format && uv run ruff check
env -u PYTHONPATH uv run pytest        # no GPU or model needed; the env -u is for this machine, I-010
```

## Rules

| Area | Rule |
|---|---|
| Product | Numbers only from cited passages · fiscal years from `filings.fiscal_year` · unverifiable claims labeled, never dropped · decline outside the corpus · no prices, forecasts or advice |
| Code | Pipeline code in `src/tenk_agent/`, experiments only run and score it · settings in `config.yaml` (experiments: their own `config.yaml`) · prompts in `prompts/` · database only through `Store` · EDGAR: declared User-Agent, ≤ 10 requests/s |
| Eval | Gold sets (`evaluation/gold/`) reviewed by a person · never edit a gold set to pass a run · results recorded in the version's `experiments/V*.md` |
| Docs | A pipeline change updates its `docs/stages/V*.md`; an experiment its `experiments/V*.md` · decisions and issues go in [docs/DECISIONS.md](docs/DECISIONS.md) · how-to goes in `docs/GETTING_STARTED.md`, not in the stage specs |
| Commits | One line with a type prefix: `feat`, `fix`, `docs`, `refactor`, `chore`, `style`, `test` |
