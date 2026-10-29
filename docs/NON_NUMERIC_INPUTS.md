# Non-numeric inputs

Which filing text reaches the research note, and who chooses it. Numbers are in [FINANCIAL_MODELING.md](FINANCIAL_MODELING.md).

**Key point:** no prompt or code names a filing section (Item 1A, MD&A). Prompts give a *topic*; the LLM writes its own `search_filings` queries.

## Where text is used

| Step | Reads | Told what to look for | Who picks passages | Lands in |
|---|---|---|---|---|
| **KPI + guidance extraction** | Earnings release | Labels per company in `facts/kpis.yaml` | Code (tables first, within a size budget) | Numbers only |
| **Events** | 10-K, 10-Q, release | Event types listed in `events.md` | LLM, via `search_filings` | Section 8 |
| **Debates** | 10-K, 10-Q, release | "management's explanations and risk factors" (`debates.md`) | LLM, via `search_filings` | Section 3 |
| **Driver proposals** | 10-K, 10-Q, release | Reasons to look for, in `propose.md` | LLM, via `search_filings` | Assumptions. **Off by default** (`proposer.method: defaults`) |
| **Note prose** | Nothing | A list of registered facts | n/a | All prose |

## Per step

**Extraction** (`extract.md`). One LLM call per release, with no search. Every value needs a quote copied verbatim from a given chunk, and code rejects any quote not found there. It extracts no prose.

**Events** (`events.md`). Asked "what changed for TICKER in PERIOD?". Topics named: results, guidance, definition changes, business model, acquisitions/disposals, buybacks and debt, regulation, catalysts. Returns up to 8 events with evidence ids. Events with no resolvable id are dropped. **Section 8 prints only `catalyst`, `regulation` and `m&a`.** The rest (`results`, `guidance`, `capital`, `definition_change`, `business_model`, `other`) stay in `events.json`.

**Debates** (`debates.md`). One per scenario driver, three per company. Each returns bull, bear, hypothesis, trigger, data gap and evidence. A debate without resolvable evidence is dropped. Its numbers become traceable facts.

**Note prose** (`section.md`). The writer sees only facts, with no filings and no tools. Every number must be one of the facts, and the number trace drops any that isn't.

## `search_filings`

| | |
|---|---|
| **Corpus** | 10-K, 10-Q, earnings releases; from Q1 2023; filed before the as-of |
| **Method** | Keyword + dense, RRF, rerank, round-robin per company. No LLM |
| **Chunks** | By Item and heading path; each risk factor is its own chunk |
| **Inputs** | `query`, plus optional `companies`, `periods`, `forms`. A company is required |

Each prompt also gets a coverage table (latest filing per form, per company). It is the LLM's only guide to what exists.

## Gaps

- **Risk factors** reach the note only through debate bull/bear lines. Section 7 is code-computed shocks.
- **Nothing checks recall.** A missed acquisition or regulatory event is silent.
- **Not used:** transcripts, news, anything outside filings, releases, Form 4 and market data.

## Decisions

| Question | Decision |
|---|---|
| Print the other event types (`results`, `guidance`, `capital`, `definition_change`, `business_model`, `other`)? | Open. Some overlap with section 2 (`results`, `guidance`) and section 9 (`capital`), so leaving them out of Catalysts may be deliberate. `definition_change` and `business_model` look the most worth showing, because they affect the model's inputs. |
| Query Item 1A and MD&A explicitly? | **Hold off** until we measure a miss rate. If many material items are missed because queries are free-form, revisit. |

*Based on reading the code, prompts and `research.yaml`; no report was run.*
