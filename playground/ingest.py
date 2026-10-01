#!/usr/bin/env python3
"""Stage 1 playground: one 10-K's HTML -> sections by Item -> chunks. Nothing is written to the database.
Guide: docs/STAGE1_INGEST.md. No GPU, no model.

  python playground/ingest.py                                   # AAPL FY2025, config.yaml `ingest:` sizes
  python playground/ingest.py --ticker NVDA --year 2025 --item 8 --tag nvda8
  python playground/ingest.py --chunk-chars 1200 --tag small    # smaller prose chunks
  python playground/ingest.py --find "Total net sales"          # which chunks contain this text

Writes output/ingest/<tag>.md: the sections table and every chunk of --item.
"""

import argparse
import time
from collections import Counter

from _common import preview, settings, table, write_report

from tenk_agent.chunking import ChunkSizes, chunk_sections
from tenk_agent.gold import normalize
from tenk_agent.ingest import document_path, downloaded_filings
from tenk_agent.parse import parse_filing


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--ticker", default="AAPL")
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--item", default="8", help="Item whose chunks go in the report (default 8)")
    p.add_argument("--chunk-chars", type=int, default=s.chunk_chars)
    p.add_argument("--table-chars", type=int, default=s.table_chars)
    p.add_argument("--overlap-chars", type=int, default=s.overlap_chars)
    p.add_argument("--find", help="list the chunks containing this text (case, $, | ignored)")
    p.add_argument("--tag", default="play")
    a = p.parse_args()

    filing = next(
        f
        for f in downloaded_filings(s.data_dir)
        if (f.ticker, f.fiscal_year) == (a.ticker.upper(), a.year)
    )
    html = document_path(s.data_dir, filing).read_text(encoding="utf-8", errors="ignore")

    # 1. parse: HTML -> blocks -> sections by Item heading
    t0 = time.perf_counter()
    sections = parse_filing(html)
    t_parse = time.perf_counter() - t0
    # 2. chunk: sections -> prose chunks + whole tables
    sizes = ChunkSizes(a.chunk_chars, a.table_chars, a.overlap_chars)
    t0 = time.perf_counter()
    chunks = chunk_sections(f"{filing.ticker}-FY{filing.fiscal_year}", sections, sizes)
    t_chunk = time.perf_counter() - t0

    # 3. report
    per_item = Counter(c.item for c in chunks)
    tables = Counter(c.item for c in chunks if c.kind == "table")
    rows = []
    for sec in sections:
        n_tables = sum(b.kind == "table" for b in sec.blocks)
        chars = sum(len(b.text or "") for b in sec.blocks)
        rows.append(
            [
                sec.item,
                sec.title[:50],
                len(sec.blocks),
                n_tables,
                f"{chars:,}",
                per_item[sec.item],
                tables[sec.item],
            ]
        )
    head = (
        f"{filing.ticker} FY{filing.fiscal_year} · period ended {filing.period_end_date} · {filing.source_url}\n"
        f"HTML {len(html):,} chars · parse {t_parse:.1f} s · chunk {t_chunk * 1000:.0f} ms · "
        f"{len(sections)} sections · {len(chunks)} chunks ({sum(tables.values())} tables)\n"
        f"sizes: chunk {sizes.chunk_chars} · table {sizes.table_chars} · overlap {sizes.overlap_chars} chars"
    )
    sec_table = table(
        ["Item", "Title", "Blocks", "Tables", "Prose chars", "Chunks", "Table chunks"], rows
    )
    lengths = [len(c.text) for c in chunks if c.kind == "prose"]
    split = sum(1 for c in chunks if c.kind == "table" and len(c.text) > sizes.table_chars * 0.8)
    stats = (
        f"prose chunks: {len(lengths)}, mean {sum(lengths) / max(len(lengths), 1):.0f} chars, "
        f"max {max(lengths, default=0)} · table chunks near the split size: {split}"
    )
    print(head + "\n\n" + sec_table + "\n\n" + stats)

    if a.find:
        needle = normalize(a.find)
        hits = [c for c in chunks if needle in normalize(c.text)]
        print(f"\n'{a.find}' found in {len(hits)} chunk(s):")
        for c in hits:
            print(f"  {c.id} [{c.kind}{', ' + c.units if c.units else ''}] {preview(c.text, 120)}")

    item_chunks = [c for c in chunks if c.item == a.item.upper()]
    body = "\n\n".join(
        f"### {c.id} · {c.kind}"
        + (f" · {c.units}" if c.units else "")
        + (f" · years {c.years_covered}" if c.years_covered else "")
        + f" · {len(c.text)} chars\n\n```\n{c.text}\n```"
        for c in item_chunks
    )
    write_report(
        "ingest",
        a.tag,
        f"# Ingest · {filing.ticker} FY{filing.fiscal_year}\n\n{head}\n\n{sec_table}\n\n{stats}\n\n"
        f"## Item {a.item.upper()} chunks ({len(item_chunks)})\n\n{body}\n",
    )


if __name__ == "__main__":
    main()
