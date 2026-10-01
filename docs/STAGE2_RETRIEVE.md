# Stage 2 — Retrieve: question → the chunks that answer it

## Commands

Workstation terminal, repo root. Local models on the GPU (`--device cpu` works, slower).

```bash
source .venv/bin/activate
tenk embed                                                              # once after ingest: vectors for every chunk (~2 min)
tenk search "Apple R&D expense fiscal 2024"                             # quick look, config.yaml defaults

python playground/retrieve.py "How much did Apple spend on R&D in fiscal 2024?"
python playground/retrieve.py --gold aapl-msft-rd-fy2023-2025           # gold question: gold chunks marked ★, recall printed
python playground/retrieve.py --gold aapl-msft-rd-fy2023-2025 --no-slot-search --tag noslots
python playground/retrieve.py "Tesla competition" --mode hybrid --reranker none --k 5
python playground/retrieve.py "net sales by category" --tickers AAPL --years 2025 --items 7
python playground/retrieve.py --help

tenk eval run retrieval --split dev --include-unverified --no-judge     # Recall@5 / MRR over the gold set
```
The playground prints the slots and the final top k, and saves `output/retrieve/<tag>.md` with every search's candidates, their dense / keyword ranks and rerank scores.

## Data

| Data | Where |
|---|---|
| Chunks + FTS5 index | `data/tenk.db` (stage 1) |
| Vectors (1,024-d, cosine) | `chunks_vec` in `data/tenk.db`, written by `tenk embed` |
| Embedding model, reranker | Hugging Face cache, downloaded on first use |
| Gold questions (for `--gold`) | `eval/gold/questions.jsonl` |

## Knobs

Defaults: `config.yaml` → `retrieve:`. Flags override per run.

| Knob | Flag | What it does | Try |
|---|---|---|---|
| `mode` | `--mode` | `dense` (vectors), `keyword` (FTS5 BM25), `hybrid` (both, reciprocal rank fusion) | keyword alone: Recall@5 17.6% (D-010) |
| `reranker` | `--reranker` | Cross-encoder that re-scores the candidates; `none` to skip | none vs `local:BAAI/bge-reranker-base` vs v2-m3 |
| `candidates` | `--candidates` | Chunks per search before reranking | 10 = faster, misses more |
| `slot_search` | `--no-slot-search` | One search per (company, filing) the question names | off: comparisons lose filings |
| `embedder` | (config / `TENK_EMBEDDER`) | Changing it needs `tenk embed` again (re-embeds everything) | `local:BAAI/bge-base-en-v1.5` |
| `device` | `--device` | Where local models run | `cpu` when vLLM fills the GPU |
| `answer.k` | `--k` | Chunks returned | 5 vs 8 vs 20 |

| Step | Time (L40S) |
|---|---|
| Embed the question | 0.03 s |
| Rerank 40 candidates | 0.4 s |
| One search, end to end | 0.4–0.7 s; six slots 2.7 s |

## Outputs

| Output | Where |
|---|---|
| Companies and years found in the question, the slots | terminal |
| Top k: chunk, kind, ★ if gold, text | terminal, report |
| Recall@5, Recall@k, MRR, gold chunks not retrieved (`--gold`) | terminal, report |
| Each search: filters, candidates with dense / keyword rank and rerank score | `output/retrieve/<tag>.md` |

## How it works

`tenk_agent.retrieval.Retriever.search()`:

```
question ─► slots ─► per slot: dense (+ keyword) candidates ─► rerank ─► round-robin merge ─► top k
```

| Step | What happens | Here ("Compare Apple's and Microsoft's R&D, 2023–2025") |
|---|---|---|
| 1. Names | Companies by ticker, name or alias ("Google" → GOOGL); years, ranges ("2023–2025") and "last three years" | AAPL, MSFT; 2023, 2024, 2025 |
| 2. Slots | One (company, filings) pair each. A bare calendar year also takes the fiscal year that mostly covers it, from `period_end_date` | 6 slots |
| 3. Candidates | Dense: question vector vs chunk vectors (cosine, brute force in SQLite). Each chunk was embedded with a header line: company, fiscal year, Item | 40 per slot |
| 4. Rerank | A cross-encoder reads (question, header + chunk) pairs and scores each; best first | |
| 5. Merge | Every slot's best chunk first, then every slot's second, … | 8 chunks, ≥ 1 per filing |

- **Slot search is the biggest structural win.** An unfiltered top 5 for "Apple and Microsoft, 2023–2025" is mostly one company. +6 points Recall@5 (D-011).
- **Calendar vs fiscal.** "NVIDIA revenue in 2024" → FY2024 *and* FY2025 (FY2025 ended 2025-01-26, so it mostly covers 2024). "In fiscal 2024" → FY2024 only.
- **Recall is per source.** A question with 6 gold sources needs 6 different chunks in the top 5 for 100%: impossible, so multi-filing questions cap Recall@5 below 1.

**Results** (gold set, draft): Recall@5 **71.2%** and MRR 0.53 on `test` (29 scorable), 59.3% / 0.45 on `dev` (68). Model comparison: [D-010](DECISIONS.md#d-010).

## Things to try

| Try | Question |
|---|---|
| `--gold aapl-msft-rd-fy2023-2025` vs `--no-slot-search` | How many filings does each version reach? |
| `--gold aapl-msft-rd-fy2023-2025` | Retrieval finds MSFT's MD&A R&D table, not the gold Item 8 statement. Wrong, or a gold gap? ([I-004](DECISIONS.md#i-004)) |
| `"NVIDIA revenue in 2024"` vs `"… in fiscal 2024"` | Which filings does each search? |
| `--reranker none` | How far do the gold chunks fall? |
| `--mode keyword` on a narrative question | Where does BM25 do well? |

## Gotchas

- **Embedder and database must match.** `Retriever` refuses to run if the stored vectors came from another model; `tenk embed` re-embeds when the model changes.
- **Explicit filters skip slot search.** `--tickers` / `--years` run one filtered search (this is what the agent's `search_filings` does).
- Companies outside the corpus (Oracle) give no slot; the search runs unfiltered and returns the closest corpus chunks. Declining is stage 3's job.
