"""Rule-based structure labels for 10-Qs and earnings releases (V4 design doc §4.4).

The 10-K keeps V0's LLM structure agent. A 10-Q's headings are regular (a bold "PART I" line,
then bold "Item 2." lines), and a release has no Items, so rules label both; the LLM agent is
added only if the pilot shows headings the rules miss. The labels have the structure agent's
shape, so V0's assembly and chunkers run unchanged.

  item_heading  a bold, short, non-link "Item n." paragraph (or one-row table) that moves
                forward in the form's Item order; 10-Q keys carry the open Part (I.2, II.1A)
  part_heading  "PART I" / "PART II" (dropped from the document, opens the Part)
  heading       other bold paragraphs under 120 characters (level 1, kind other)
  furniture     a bare page number before a page break
  data_table    every other table; units from the table or the paragraph before it
  body          everything else
"""

import json
import re
import time
from dataclasses import asdict
from pathlib import Path

from tenk_agent.preprocess.blocks import Block, read_blocks
from tenk_agent.preprocess.forms import FormSpec, form_spec
from tenk_agent.preprocess.structure import assemble

PART = re.compile(r"^part\s+(i{1,2})\b[\s.:—–-]*", re.I)  # EXPE: "Part I. Item 1. …" on one line
ITEM = re.compile(r"^i\s?tems?\s+(\d{1,2}[a-c]?)\b", re.I)  # TRIP: "I tem 2." (styled initial)
UNITS = re.compile(r"in (thousands|millions|billions)", re.I)
PAGE_NUMBER = re.compile(r"^\s*(\d{1,3}|[A-Z]-\d{1,3})\s*$")
MAX_HEADING = 150  # characters: longer bold text is a paragraph, not a heading


def _text(b: Block) -> str:
    return " ".join(b.rows[0]) if b.kind == "table" and len(b.rows) == 1 else b.text


def _bold(b: Block) -> bool:
    return b.emph in ("B", "BI")


def label_blocks(blocks: list[Block], spec: FormSpec) -> dict[str, dict]:
    """A label per block, in the structure agent's format."""
    labels: dict[str, dict] = {}
    part, last = None, -1  # the open Part; index of the last Item in spec.items
    for i, b in enumerate(blocks):
        text = _text(b).strip()
        short = len(text) <= MAX_HEADING and not b.link
        label: dict = {"id": b.id, "role": "body"}
        one_line = b.kind == "paragraph" or len(b.rows) == 1
        if short and one_line and (m := PART.match(text)):
            part, text = m.group(1).upper(), text[m.end() :]
            label["role"] = "part_heading"
        if short and one_line and (m := ITEM.match(text)):
            number = m.group(1).upper()
            key = f"{part}.{number}" if spec.form == "10-Q" and part else number
            if key in spec.items and spec.items.index(key) > last:
                last = spec.items.index(key)
                label |= {"role": "item_heading", "item": key}
        elif label["role"] == "part_heading":
            pass
        elif b.kind == "table" and not (len(b.rows) == 1 and len(text) <= MAX_HEADING):
            caption = blocks[i - 1].text if i else ""
            m = UNITS.search(b.text[:400]) or UNITS.search(caption)
            units = f"USD {m.group(1).lower()}" if m else None
            source = None if not m else ("header" if UNITS.search(b.text[:400]) else "caption")
            label |= {"role": "data_table", "units": units, "units_from": source}
        elif b.kind == "paragraph" and b.pb and PAGE_NUMBER.match(text):
            label["role"] = "furniture"
        elif b.kind == "paragraph" and _bold(b) and len(text) < 120 and not b.link:
            label |= {"role": "heading", "level": 1, "kind": "other"}
        labels[b.id] = label
    return labels


def release_blocks(documents: list[tuple[str, str]]) -> list[Block]:
    """One block list for a release's exhibits, each opened by a pseudo-Item heading block.
    `documents`: (exhibit type, HTML) in index order, EX-99.1 first."""
    blocks: list[Block] = []
    for n, (exhibit, html) in enumerate(documents, start=1):
        blocks.append(Block(id=f"x{n}", text=f"{exhibit} ({'release' if n == 1 else 'exhibit'})"))
        for b in read_blocks(html):
            b.id = f"x{n}-{b.id}"
            blocks.append(b)
    return blocks


def label_release(blocks: list[Block], spec: FormSpec) -> dict[str, dict]:
    """Releases: the body rules, with each exhibit's pseudo-heading as its Item."""
    labels = label_blocks([b for b in blocks if "-" in b.id], spec)
    labels = {
        i: (lb if lb["role"] != "item_heading" else {"id": i, "role": "body"})
        for i, lb in labels.items()
    }  # "Item 2.02" in an exhibit is not a section
    for n, b in enumerate(b for b in blocks if "-" not in b.id):
        labels[b.id] = {"id": b.id, "role": "item_heading", "item": spec.items[n]}
    return {b.id: labels[b.id] for b in blocks}


def preprocess_filing(folder: Path, out: Path, registrant: str) -> dict:
    """A V4 10-Q or release folder (acquisition/filings.py) → blocks.json, labels.json,
    labels-log.json and document.json in `out`, as V0's preprocess writes them."""
    meta = json.loads((folder / "metadata.json").read_text())
    spec = form_spec(meta["form"])
    t = time.time()
    if meta["form"] == "ER":
        docs = [
            (d["type"], (folder / d["name"]).read_text(encoding="utf-8", errors="replace"))
            for d in meta["documents"]
        ]
        blocks = release_blocks(docs)
        labels = label_release(blocks, spec)
    else:
        html = (folder / meta["documents"][0]["name"]).read_text(encoding="utf-8")
        blocks = read_blocks(html)
        labels = label_blocks(blocks, spec)
    filing = {
        "ticker": meta["ticker"],
        "registrant": registrant,
        "form": meta["form"],
        "fiscal_year": meta["fiscal_year"],
        "fiscal_period": meta["fiscal_period"],
        "period_end": meta["period_end_date"],
        "filing_date": meta["filing_date"],
        "accession": meta["accession_no"],
        "source_url": meta["documents"][0]["url"],
    }
    document = assemble(blocks, labels, filing)
    out.mkdir(parents=True, exist_ok=True)
    for name, obj in (
        ("blocks.json", [asdict(b) for b in blocks]),
        ("labels.json", labels),
        ("labels-log.json", {"labeling": "rules", "seconds": round(time.time() - t, 1)}),
        ("document.json", document),
    ):
        (out / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    return document
