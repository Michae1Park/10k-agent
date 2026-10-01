# Stage 1 — Ingest: 10-K HTML → sections → chunks

## Commands

Workstation terminal, repo root. No GPU, no model.

```bash
source .venv/bin/activate                                          # once per terminal
export SEC_USER_AGENT="Your Name you@example.com"                  # SEC requires a contact
tenk fetch                                                         # download the 24 10-Ks (cached)
tenk ingest                                                        # parse + chunk all of them into data/tenk.db (~30 s)
tenk check                                                         # every filing has Items 1A, 7, 8 and tables in 8

python playground/ingest.py                                        # one filing, nothing written: AAPL FY2025
python playground/ingest.py --ticker NVDA --year 2025 --tag nvda    # NVIDIA: statements moved from Item 15
python playground/ingest.py --chunk-chars 1200 --tag small         # smaller prose chunks
python playground/ingest.py --find "Total net sales"               # which chunks contain this text
python playground/ingest.py --help
```
The playground prints a report and saves `output/ingest/<tag>.md` (sections table + every chunk of `--item`, default 8).

## Data

| Data | File |
|---|---|
| 10-K primary document (HTML) | `data/raw/filings/<TICKER>/FY<year>/*.htm` |
| Filing metadata: accession, fiscal year, period end, URL | `data/raw/manifest.json`, `…/metadata.json` |
| XBRL company facts (gold cross-checks only, D-007) | `data/raw/edgar/<TICKER>/companyfacts.json` |
| **Database** | `data/tenk.db` |

| Table | Key fields |
|---|---|
| `companies` | ticker, cik, name, aliases ("Google", "Facebook") |
| `filings` | accession_no, ticker, fiscal_year, period_end_date, filing_date, source_url |
| `sections` | id (`AAPL-FY2025-7`), item, title |
| `chunks` | id (`AAPL-FY2025-7-012`), ticker, fiscal_year, item, seq, kind (prose / table), text, units, years_covered; FTS5 index; vectors in `chunks_vec` (stage 2) |
| `traces` | every Ask / Research request (stages 3–4) |

## Knobs

Defaults: `config.yaml` → `ingest:`. Flags override per playground run; `tenk ingest` uses the file.

| Knob | Flag | What it does | Try |
|---|---|---|---|
| `chunk_chars` | `--chunk-chars` | Prose chunk size (4 chars ≈ 1 token) | 2400 → 170 chunks for AAPL FY2025; 1200 → 267 |
| `table_chars` | `--table-chars` | Longer tables split into row groups that repeat the header rows | 7200: no table in the corpus is that long (max chunk 4,747) |
| `overlap_chars` | `--overlap-chars` | A chunk starts with the previous chunk's last paragraph if it's this short | 0 = no overlap |

## Outputs

| Output | Where |
|---|---|
| Filing, HTML size, parse / chunk time, section and chunk counts | terminal, report |
| Per Item: blocks, tables, prose chars, chunks, table chunks | terminal, report |
| Prose chunk size: mean, max | terminal, report |
| Chunks containing `--find` text, with kind and units | terminal |
| Every chunk of `--item`, with units and years covered | `output/ingest/<tag>.md` |

## How it works

```
HTML ─► blocks (paragraphs, tables) ─► sections by Item heading ─► chunks ─► SQLite (+ FTS5)
```

| Step | What happens | Here (AAPL FY2025) |
|---|---|---|
| 1. Fetch | EDGAR submissions → the 10-K per fiscal year (fiscal year from XBRL, D-004); ≤ 10 requests/s, declared User-Agent | 1.5 MB HTML |
| 2. Blocks | HTML flattened to paragraphs and tables in reading order; hidden elements dropped; split cells merged (`$` + `1,234`, `(3` + `)%`) | 0.3 s |
| 3. Sections | An "Item N. Title" paragraph *outside* a table starts a section (skips the table of contents and cross-references) | 23 sections |
| 4. Item 8 | If Item 8 has no tables, the statements (from their index or the auditor's report) move into it | NVIDIA: from Item 15 |
| 5. Chunks | Prose packed into `chunk_chars` chunks, never across sections. Each table is one chunk with up to two caption lines ("(In millions)") | 170 chunks, 48 tables |
| 6. Metadata | Table chunks get `units` ("USD millions") and `years_covered` from their top lines | |

**Whole corpus:** 24 filings → 6,299 chunks (1,571 tables), mean 1,320 chars. Item 8 holds 2,889 chunks, 1A 1,185, 7 902.

**A table chunk** (`AAPL-FY2025-8-002`, first lines; what retrieval, the model and verification all see):

```
CONSOLIDATED STATEMENTS OF OPERATIONS
(In millions, except number of shares, which are reflected in thousands, and per-share amounts)
| Years ended |
| September 27, 2025 | September 28, 2024 | September 30, 2023 |
| Net sales: |
| Products | $307,003 | $294,866 | $298,085 |
| Services | 109,158 | 96,169 | 85,200 |
| Total net sales | 416,161 | 391,035 | 383,285 |
```

Columns are dates, not fiscal-year labels, and their order differs by filer (Alphabet and Amazon run oldest → newest).

## Things to try

| Try | Question |
|---|---|
| `--find "Total net sales"` | Why does one figure appear in 13 chunks? (Hint: MD&A, notes, segments, the statement.) |
| `--chunk-chars 600 / 1200 / 4800` | How many chunks? What happens to a long risk factor? |
| `--ticker NVDA --year 2025` vs `--ticker AAPL` | Which Items does each filing have? Where did NVIDIA's statements come from? |
| `--ticker NFLX --item 8` | Which unit does Netflix use? (Not millions.) |
| `--overlap-chars 0` | Which chunks lose their lead-in sentence? |

## Gotchas

- **Units differ by filer.** Netflix reports in thousands; per-share figures in dollars. `units` comes from the caption; a table without a unit line has `units = None`.
- **The same number is in several chunks.** A 10-K repeats the statements' figures in MD&A and the notes, and the next year's 10-K repeats them again. The gold set names a primary source per fiscal year ([EVAL.md](EVAL.md)).
- A prose chunk can exceed `chunk_chars` by its overlap paragraph (max 2,791 at 2,400).
- `tenk ingest` deletes the vectors of re-ingested filings: run `tenk embed` afterwards.
