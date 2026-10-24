# V3 · Agent: the model picks the tools, then answers with cited claims

**Builds on** V1 (its search is now a tool) and V2 (the same answer format and claim check). **Code:** [`agent/`](../../src/tenk_agent/agent/), prompt [prompts/agent.md](../../src/tenk_agent/prompts/agent.md). **Design and results:** [experiments/V3_AGENT.md](../../experiments/V3_AGENT.md). **Why these tools:** [D-036](../DECISIONS.md#d-036). Overview: [ARCHITECTURE.md](../ARCHITECTURE.md).

## Commands

```bash
tenk tool --list                                                         # the schemas the model gets
tenk tool search_filings query="research and development" companies=AAPL,NVDA fiscal_years=2025 k=2
tenk tool get_section company=Amazon fiscal_year=2025 item=8 page=2
tenk tool calculator op=cagr inputs='{"start": 574785, "end": 716924, "years": 2}'
tenk tool xbrl_facts company=Tesla concept=us-gaap:Revenues fiscal_year=2024
tenk tool edgar_filings company=MSFT --live                              # --live: fetch what isn't cached (SEC_USER_AGENT)

tenk agent "Which of the four companies had the highest operating margin in its latest fiscal year?"
tenk agent "..." --tools text --max-calls 8 --json
```

## Data

| Data | Where |
|---|---|
| Chunks, sections, filings | `db` (V1's parent-child database) |
| XBRL company facts, EDGAR submissions | `data/raw/edgar/<TICKER>/` (`tenk fetch` writes the corpus companies'; `--live` adds others) |
| How to work, rules, answer format | [prompts/agent.md](../../src/tenk_agent/prompts/agent.md) |
| Tool schemas | [agent/tools.py](../../src/tenk_agent/agent/tools.py) (`SPECS`) |
| Gold questions | V2's set, plus [agent-gold-fy2025.jsonl](../../evaluation/gold/agent-gold-fy2025.jsonl) |

## Knobs

`config.yaml` → `agent:` (the model is `generation:`'s); the V3 experiment pins its own in [experiments/v3/config.yaml](../../experiments/v3/config.yaml).

| Knob | What it does | Default |
|---|---|---|
| `tools` | `full`, or `text` (no SEC tools: the ablation); `--tools` | `full` |
| `max_calls` | Tool-call budget; calls past it return an error, then one answer turn with tools off; `--max-calls` | 20 |
| `max_turns` | Model turns, a guard against loops of failed calls | 20 |
| `result_chars` | A tool result is cut to this many characters | 16,000 |
| `section_chunks` | Chunks per `get_section` page | 4 |

## Tools

| Tool | Does | Returns |
|---|---|---|
| `search_filings` | V1's hybrid search (keyword + dense → RRF → rerank), one search per company named, filtered to the fiscal years given | Chunks with `chunk_id`, company, year, Item, kind, units, pages, text |
| `get_section` | One Item of one 10-K in order, `section_chunks` at a time | The page's chunks, the Item's title, `page` of `pages` |
| `calculator` | [calculator.py](../../src/tenk_agent/generation/calculator.py): `pct_change`, `cagr`, `ratio`, `percent_of`, `difference`, `sum`, or an expression over named inputs | Result, formula, inputs |
| `xbrl_facts` | One concept of one 10-K from SEC company facts, any registrant; an unknown concept returns the closest names | Values for the filing's year and the prior years it shows, each with an `xbrl_id` to cite |
| `edgar_filings` | EDGAR filing metadata, any registrant | Form, accession, filing date, period end, link (no financial data) |

A tool never raises: bad arguments, a company outside the corpus, an uncached SEC file, return `{"error": …}` for the model to read. Object and array arguments written as JSON strings are parsed.

## How it works

```
question ─► model ──tool calls──► tools ─► model ─► … ─► JSON answer ─► check each claim ─► answer
```

| Step | Code | What happens |
|---|---|---|
| 1. First turn | `agent/loop.py` | The model must call a tool (`tool_choice: required`), so nothing is answered from memory |
| 2. Loop | LangGraph `model ⇄ tools` | The model picks tools and arguments; results go back as tool messages. A repeated call returns an error; so do calls past `max_calls` |
| 3. Answer | `model` | A turn without tool calls is the answer, in V2's JSON format; one "only the JSON" retry. Past the budget, one turn with tools off |
| 4. Check | `generation/answers.py`, `verify.py` | V2's claim check; a claim may also cite `xbrl:` ids, verified against the cached fact |

**Claims citing XBRL** ([D-036](../DECISIONS.md#d-036)): `xbrl:<TICKER>:<taxonomy>:<concept>:<accession>:<period end>`. The value must equal the fact (to the claim's written precision, any unit), and the fact must be the claim's company. Calculation inputs may come from cited facts too, any company's (a comparison spans companies). A percent claim also matches its calculation × 100 (8.3 percent from a `ratio` of 0.083); a sum or difference may be claimed in another unit than its inputs'; a value a cited source shows as is stays verified even with a needless calculation (a unit conversion). A claim about a filing (its date, its form) may cite the accession number `edgar_filings` returned, checked against the cached submissions.
