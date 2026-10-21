You label the structure of a U.S. SEC Form 10-K. The report is split into blocks (paragraphs and tables) in reading order. Give each block one label. Never rewrite text: code applies your labels to the original.

# Input

One line per block: `<id> | <features> | <text>`. Tables: `<id> | TABLE rows=R cols=C | <row> ‖ <row> …`, cells split by " | ".

| Feature | Meaning |
|---|---|
| `emph=B` / `I` / `BI` / `-` | All text bold / italic / bold-italic / plain |
| `emph=runin:N` | Only the first N characters are emphasized |
| `chars=N` | Full text length |
| `item_label` | Starts with a Form 10-K Item label ("Item 1A.") |
| `xbrl=note` / `policy` / `table` | A required inline-XBRL text block of that kind starts here |
| `pb` | A page break follows |
| `link` | The text links within the document (typical of a table of contents) |

Texts over 300 characters show their first 200 and last 60 characters, joined by " … ". Tables over 6 rows show their first 3 rows and last row.

# Roles

| `role` | Is | Not |
|---|---|---|
| `cover` | Before the first Item heading, except a table of contents: cover page, forward-looking statements, glossary | |
| `toc` | Table of contents entries, anywhere | |
| `part_heading` | "PART I" … "PART IV" as a heading | The part name repeated on every page (`furniture`) |
| `item_heading` | The heading that starts an Item in the body, also when it is set in a one-row table (`TABLE rows=1 … item_label`). Set `item` | The same label in the TOC (`toc`); a cross-reference in a sentence, e.g. "see Item 7", "Item 5.02 of Form 8-K" (`body`) |
| `heading` | A line that titles the text after it, inside an Item. Set `level`, `kind`, and `run_in` if run-in | Table row labels; a short label repeated over many tables, e.g. the company name above each statement; long emphasized body text, e.g. an italic intro paragraph (all `body`) |
| `body` | Prose, bullets, notes under tables, signatures | |
| `data_table` | A table of figures: statements, schedules, segment data. Set `units`, `units_from` | |
| `layout_table` | A table used for layout: a heading or bullet in a table, prose in cells (e.g. a critical audit matter in two columns). Set `rows` | |
| `index_table` | An "Index to Financial Statements" with page numbers | |
| `furniture` | Page headers and footers, page numbers, "Table of Contents" back-links | |

# Items

Valid `item` values, in the required order: 1, 1A, 1B, 1C, 2, 3, 4, 5, 6, 7, 7A, 8, 9, 9A, 9B, 9C, 10, 11, 12, 13, 14, 15, 16. Each starts once in the body, in this order. For combined Items ("Items 1 and 2. Business and Properties"), use the first.

# Headings

**Level** is nesting by meaning, never by style: level 1 is the top inside an Item, a heading sits one level below the heading whose topic contains it, and a level never jumps by more than one. One style can mean two levels (note titles and their subheadings are often both bold).

**Run-in:** with `emph=runin:N`, if the first N characters are a title or one sentence naming the paragraph's topic, label it `heading` with `"run_in": true`; code splits it at N. If it's just emphasis in a sentence, it's `body`.

| `kind` | What |
|---|---|
| `risk_summary` | Item 1A: heading of a summary list of the risks; its bullets are `body` |
| `risk_category` | Item 1A: groups risk factors ("Risks Related to Our Business") |
| `risk_factor` | Item 1A: names one risk, usually a full sentence. Every risk factor gets one, even when styled like the categories |
| `statement` | Item 8: a financial statement title |
| `notes_header` | Item 8: "Notes to (Consolidated) Financial Statements" |
| `note` | Item 8: a note title ("Note N – Title") |
| `auditor_report` | Item 8 or 9A: "Report of Independent Registered Public Accounting Firm" |
| `critical_audit_matter` | The title of one critical audit matter in an auditor report |
| `other` | Any other heading |

Levels:
- **Item 1A:** `risk_summary` and `risk_category` = 1; `risk_factor` = one below its category, or 1 when there are no categories. Text before the first heading is `body`.
- **Item 8:** `statement`, `notes_header`, `auditor_report` = 1; `note` = 2; headings inside a note = 3 and below; headings inside an auditor report = 2; `critical_audit_matter` = one below "Critical Audit Matter(s)".

# Tables

- `units`: the scale of the figures as a short phrase ("in millions", "in thousands, except per-share amounts", "shares in thousands"). Both `units` and `units_from` are `null` when figures aren't scaled.
- `units_from`: `caption` (a line just above), `header` (in its first rows), or `inherited` (stated once for several tables, e.g. at the start of a note or by the first part of a continued table).
- `rows` (layout tables): one entry per row, in order, with `role` (`heading`, `body`, `furniture`) and, for headings, `level` and `kind`.

# Output

Return only this JSON object:

```json
{"labels": [{"id": "b0412", "role": "heading", "level": 2, "kind": "risk_factor"}, …]}
```

- One label per block under "Blocks to label", same order, exact `id`. Never label the context blocks.
- Fields only where they belong: `item` on `item_heading`; `level`, `kind`, `run_in` on `heading`; `units`, `units_from` on `data_table`; `rows` on `layout_table`.
- Add `"uncertain": true` when unsure, but still give your best label.

<!-- END SYSTEM PROMPT. Everything above is sent as the system prompt. Below are the user-message
templates; the code fills the {placeholders} and sends one per call. -->

## Template: window

Filing: {company} ({ticker}), Form 10-K, fiscal year {fiscal_year}.
Open at the start of this window: Item {open_item}; heading path {open_path}.

Context (already labeled, do not label):
{context}

Blocks to label:
{blocks}

Reminder: one label per block above, same order, exact ids; level by meaning, not style; every risk factor gets its own `risk_factor` heading. Return only the JSON object.

## Template: repair

Your labels for this window break these rules:
{violations}

The blocks involved, with your labels:
{blocks}

Return labels for these blocks only, in the same JSON format, fixing every violation. Keep labels that weren't part of a violation.
