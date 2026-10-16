#!/usr/bin/env python3
"""V0 trace: how raw 10-K HTML changes at every parse and chunk step. Nothing is written to the database.
Guide: docs/V0_DATA.md. No GPU, no model.

  python versions/v0_data/trace.py --demo                    # AAPL FY2025, Item 6 -> first part of Item 7 (58 KB)
  python versions/v0_data/trace.py --demo msft               # MSFT FY2025, Item 6 -> all of Item 7 and 7A (833 KB)
  python versions/v0_data/trace.py --toy                     # tiny hand-made HTML, one case per step
  python versions/v0_data/trace.py --anchor "CONSOLIDATED STATEMENTS OF OPERATIONS"   # a slice of AAPL FY2025
  python versions/v0_data/trace.py --ticker NFLX --year 2025 --anchor "Item 8." --occurrence 2
  python versions/v0_data/trace.py --ticker NVDA --year 2025  # whole filing
  python versions/v0_data/trace.py --html my_slice.htm        # any HTML file

Steps: 0 raw HTML · 1 flatten · 2 classify tables · 3 merge split cells · 4 blocks · 5 sections · 6 chunks.
The terminal shows the first --limit lines of each step; output/ingest/<tag>-trace.md has everything,
including the entire raw HTML and the full text of every block and chunk.
"""

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from tenk_agent import parse as P
from tenk_agent.chunking import ChunkSizes, chunk_sections
from tenk_agent.ingest import document_path, downloaded_filings
from tenk_agent.playground import settings, write_report

HERE = Path(__file__).parent
DEMOS = {"aapl": HERE / "trace_demo_aapl.html", "msft": HERE / "trace_demo_msft.html"}
TOY = HERE / "trace_toy.html"
report: list[str] = []


def out(text: str = "", full: str | None = None, show: bool = True) -> None:
    """Print `text` (unless show is False) and put `full` (default: text) in the report."""
    if show:
        print(text)
    report.append(text if full is None else full)


def step(title: str) -> None:
    out(f"\n======== {title} ========")


def capped(items, limit: int):
    """Yield (item, show): every item goes to the report, the first `limit` also to the terminal."""
    items = list(items)
    for i, item in enumerate(items):
        yield item, i < limit
    if len(items) > limit:
        print(f"... {len(items) - limit} more in the report (or raise --limit)")


def load_html(a, s) -> tuple[str, str]:
    if a.demo:
        return DEMOS[a.demo].read_text(), f"{a.demo.upper()}-demo"
    if a.toy:
        return TOY.read_text(), "toy"
    if a.html:
        return Path(a.html).read_text(encoding="utf-8", errors="ignore"), Path(a.html).stem
    filing = next(
        f
        for f in downloaded_filings(s.data_dir)
        if (f.ticker, f.fiscal_year) == (a.ticker.upper(), a.year)
    )
    html = document_path(s.data_dir, filing).read_text(encoding="utf-8", errors="ignore")
    label = f"{filing.ticker}-FY{filing.fiscal_year}"
    if not a.anchor:
        return html, label
    hits = [m.start() for m in re.finditer(re.escape(a.anchor), html)]
    if len(hits) < a.occurrence:
        raise SystemExit(f"'{a.anchor}' found {len(hits)} time(s); --occurrence {a.occurrence}")
    at = hits[a.occurrence - 1]
    start = max(html.rfind("<div", 0, at), 0)
    end = at + a.span
    if html.rfind("<table", 0, end) > html.rfind("</table>", 0, end):  # don't cut a table in half
        end = html.find("</table>", end) + len("</table>")
    print(f"anchor '{a.anchor}': {len(hits)} match(es), slicing {end - start:,} chars from #{a.occurrence}")
    return html[start:end], label


def walk(el: Tag, depth: int, rows: list[tuple[str, str]]) -> None:
    """Mirror parse._blocks, but describe what it decides at each node: (short, full) lines."""
    pad = "  " * depth
    for c in el.children:
        if isinstance(c, NavigableString):
            if (t := P._clean(str(c))) and el.find(P.BLOCK_TAGS):
                rows.append((f"{pad}text node -> paragraph {t[:60]!r}", f"{pad}text node -> paragraph {t!r}"))
        elif not isinstance(c, Tag):
            continue
        elif P._hidden(c):
            t = P._clean(c.get_text(" "))
            rows.append((f"{pad}<{c.name}> display:none -> DROPPED", f"{pad}<{c.name}> display:none -> DROPPED {t!r}"))
        elif c.name == "table":
            rows.append((f"{pad}<table> -> steps 2-3",) * 2)
        elif c.find(P.BLOCK_TAGS):
            rows.append((f"{pad}<{c.name}> has block children -> recurse",) * 2)
            walk(c, depth + 1, rows)
        elif t := P._clean(c.get_text(" ")):
            rows.append((f"{pad}<{c.name}> leaf -> paragraph {t[:60]!r}", f"{pad}<{c.name}> leaf -> paragraph {t!r}"))


def block_line(i: int, b: P.Block, chars: int | None) -> str:
    flag = "HEADING" if P._heading(b) else "NOISE" if b.kind == "paragraph" and P.NOISE.match(b.text) else ""
    head = f"{i:4} {b.kind:9}{'*' if b.from_table else ' '} {flag:8} "
    if b.kind == "paragraph":
        return head + b.text[:chars]
    if chars is not None:
        return head + f"{len(b.rows)} rows {b.rows[:2]}"
    return head + f"{len(b.rows)} rows\n" + "\n".join(f"{'':24}{row}" for row in b.rows)


def main():
    s = settings()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--ticker", default="AAPL")
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--anchor", help="slice the filing starting at this text (default: whole filing)")
    p.add_argument("--occurrence", type=int, default=1, help="which match of --anchor (1 = first)")
    p.add_argument("--span", type=int, default=30000, help="HTML chars to keep after the anchor")
    p.add_argument("--html", help="trace this HTML file instead of a filing")
    p.add_argument("--demo", nargs="?", const="aapl", choices=sorted(DEMOS), help="trace a bundled excerpt")
    p.add_argument("--toy", action="store_true", help="trace the hand-made trace_toy.html")
    p.add_argument("--limit", type=int, default=80, help="max terminal lines per step (the report has all)")
    p.add_argument("--tag", help="report name (default: from the input)")
    a = p.parse_args()

    html, label = load_html(a, s)
    root = (soup := BeautifulSoup(html, "lxml")).body or soup
    tables = root.find_all("table")

    step(f"0. RAW HTML · {label} · {len(html):,} chars")
    out(html[:1200] + "\n... (terminal shows the first 1,200 chars; the report has all of it)", full=html)

    step("1. FLATTEN · what _blocks() decides at each node")
    rows: list[tuple[str, str]] = []
    walk(root, 0, rows)
    for (short, full), show in capped(rows, a.limit):
        out(short, full, show)

    step(f"2. SEPARATE TABLES · {len(tables)} <table> element(s)")
    for t, show in capped(tables, a.limit):
        trs = [c for tr in t.find_all("tr") if (c := P._row_cells(tr))]
        numbers = any(re.search(r"\d", x) for r in trs for x in r[1:])
        verdict = "DATA table" if len(trs) > 1 and numbers else "layout -> paragraphs"
        out(f"{len(trs):3} rows · digits past column 1: {numbers!s:5} => {verdict} · first row {trs[0] if trs else None}", show=show)

    step("3. MERGE SPLIT CELLS · raw <td> text vs merged row (only rows that changed)")
    changed = []
    for t in tables:
        for tr in t.find_all("tr"):
            raw = [r for td in tr.find_all(["td", "th"]) if (r := P._clean(td.get_text(" ")))]
            if raw != P._row_cells(tr):
                changed.append(f"raw    {raw}\nmerged {P._row_cells(tr)}\n")
    out(f"{len(changed)} row(s) changed by merging")
    for c, show in capped(changed, a.limit // 2):
        out(c, show=show)

    blocks = list(P._blocks(root))
    step(f"4. BLOCKS · {len(blocks)} in reading order (* = row of a layout table)")
    for (i, b), show in capped(enumerate(blocks), a.limit):
        out(block_line(i, b, 100), block_line(i, b, None), show)

    step("5. SECTIONS · split at Item headings, noise dropped, duplicates merged, Item 8 relocated")
    sections = P.parse_filing(html)
    if not sections:
        # A slice without an Item heading: treat it as one section so steps 5-6 still show something.
        kept = [b for b in blocks if not (b.kind == "paragraph" and P.NOISE.match(b.text))]
        sections = [P.Section("slice", "(no Item heading in this slice)", kept)]
    for sec in sections:
        out(f"Item {sec.item}: {sec.title!r} · {len(sec.blocks)} blocks, {sum(b.kind == 'table' for b in sec.blocks)} tables")
        for (i, b), show in capped(enumerate(sec.blocks), 3):
            out(block_line(i, b, 70), block_line(i, b, None), show)

    step("6. CHUNKS · prose packed to chunk_chars, each table whole with its caption")
    chunks = chunk_sections(label, sections, ChunkSizes(s.chunk_chars, s.table_chars, s.overlap_chars))
    out(f"{len(chunks)} chunks ({sum(c.kind == 'table' for c in chunks)} tables)")
    for c, show in capped(chunks, a.limit // 4):
        out(f"\n--- {c.id} · {c.kind} · units={c.units} · years={c.years_covered} · {len(c.text)} chars", show=show)
        out(c.text[:700] + (" ..." if len(c.text) > 700 else ""), c.text, show)

    tag = a.tag or label.lower()
    write_report("ingest", f"{tag}-trace", f"# Trace · {label}\n\n````\n" + "\n".join(report) + "\n````\n")


if __name__ == "__main__":
    main()
