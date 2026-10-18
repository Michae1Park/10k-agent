#!/usr/bin/env python3
"""V0 ground truth draft: Items, heading tree, risk factors, tables, page furniture of any 10-K.
Spec: experiments/V0_PARSING_CHUNKING.md §4. Shares no code with src/tenk_agent/parse.py, so the
ground truth doesn't inherit the baseline's rules.

  python -I experiments/V0_testdata/v0_ground_truth.py experiments/V0_testdata/aapl-fy25-excerpt.html \
      --out experiments/V0_testdata/aapl-fy25-excerpt.gt.json

Outside Item 8, heading levels are guessed from styling (first style seen in an Item = level 1, the
next = 2, ...); Item 8 levels come from its standard parts. Styling is not structure, so every
draft is reviewed by hand: fix `level`, drop false headings, add missed ones, record each fix in
"review", then set "status": "reviewed".
"""

import argparse
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

TOKENIZER = "Qwen/Qwen3-Embedding-0.6B"
BLOCK_TAGS = {"div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "center"}
ITEM = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*\.\s*(\S.*)$", re.I)
NOTE = re.compile(r"^note\s+\d+\b", re.I)
FURNITURE = [
    re.compile(
        r"^.{0,80}\|\s*(?:\d{4}\s+)?form\s+10-k\s*\|\s*\d{1,3}$", re.I
    ),  # Apple Inc. | 2025 Form 10-K | 22
    re.compile(r"^\d{1,3}$"),  # bare page number
    re.compile(r"^table of contents$", re.I),
]
UNITS = re.compile(r"in (millions|thousands|billions)", re.I)
NUMBER = re.compile(
    r"^[\s$(]*-?[\d.,]+\s*[)%]*\s*$"
)  # a cell that is only a number: "1,234", "(7,763)", "12 %"
MAX_HEADING = 300
# Item 8 has the same parts in every 10-K, so its levels come from these names, not from styling.
ITEM8_TOP = re.compile(
    r"^(index to .*financial statements|notes to .*financial statements"
    r"|report of independent registered|consolidated (statements?|balance sheets?)\b)",
    re.I,
)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def emphasis(node: NavigableString, stop: Tag) -> str:
    """'B', 'I', 'BI' or '-' for one text run, from tags and inline styles up to `stop`."""
    bold = italic = False
    for parent in node.parents:
        if parent is stop:
            break
        style = (parent.get("style") or "").replace(" ", "").lower()
        bold |= parent.name in ("b", "strong") or bool(
            re.search(r"font-weight:(bold|[6-9]00)", style)
        )
        italic |= parent.name in ("i", "em") or "font-style:italic" in style
    return ("B" if bold else "") + ("I" if italic else "") or "-"


def runs(tag: Tag) -> list[tuple[str, str]]:
    """(emphasis, text) per non-empty text run, adjacent runs of one emphasis merged."""
    out: list[tuple[str, str]] = []
    for s in tag.find_all(string=True):
        if not s.strip():
            continue
        e = emphasis(s, tag.parent)
        if out and out[-1][0] == e:
            out[-1] = (e, out[-1][1] + s)
        else:
            out.append((e, str(s)))
    return out


def hidden(tag: Tag) -> bool:
    return "display:none" in (tag.get("style") or "").replace(" ", "").lower()


def blocks(root: Tag):
    """Paragraphs and tables in reading order. A layout table (no number outside its first column)
    yields one paragraph per row, since filers use them for headings and bullets."""
    for child in root.children:
        if not isinstance(child, Tag) or hidden(child):
            continue
        if child.name == "table":
            rows = [
                [norm(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
                for tr in child.find_all("tr")
            ]
            rows = [[c for c in r if c] for r in rows]
            rows = [r for r in rows if r]
            if len(rows) > 1 and any(NUMBER.match(c) for r in rows for c in r[1:]):
                yield {"kind": "table", "rows": rows}
            else:
                for tr in child.find_all("tr"):
                    yield from _paragraph(tr)
        elif child.find(BLOCK_TAGS):
            yield from blocks(child)
        else:
            yield from _paragraph(child)


def _paragraph(tag: Tag):
    parts = runs(tag)
    text = norm("".join(t for _, t in parts))
    if text:
        yield {"kind": "paragraph", "text": text, "runs": [(e, norm(t)) for e, t in parts]}


def heading_text(block: dict) -> tuple[str, str] | None:
    """(style, heading) if the paragraph is a heading candidate: all emphasized, or a run-in
    heading (an emphasized lead sentence followed by plain body text in the same paragraph)."""
    runs_ = [(e, t) for e, t in block["runs"] if t]
    styles = {e for e, _ in runs_ if re.search(r"\w", _)}
    text = block["text"]
    if styles and "-" not in styles and len(text) <= MAX_HEADING:
        style = styles.pop() if len(styles) == 1 else "B"
        if text.isupper():
            style += "-caps"
        elif NOTE.match(text):
            style += "-note"
        return style, text
    first_style, first = runs_[0]
    if (
        first_style != "-"
        and len(first) <= MAX_HEADING
        and re.search(r"[.:]$", first)
        and len(runs_) > 1
    ):
        return first_style + "-runin", first
    return None


def draft(html_path: Path, ticker: str, fiscal_year: int) -> dict:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
    stream = list(blocks(soup.body))
    # A short line repeated 3+ times and always followed by another heading ("Apple Inc." over every
    # statement title) is a label, not a heading.
    seen: dict[str, list[bool]] = {}
    for b, nxt in zip(stream, stream[1:] + [{"kind": "end"}], strict=True):
        if b["kind"] == "paragraph" and len(b["text"]) <= 40 and heading_text(b):
            seen.setdefault(b["text"], []).append(
                nxt["kind"] == "paragraph" and bool(heading_text(nxt))
            )
    labels = {text for text, followed in seen.items() if len(followed) >= 3 and all(followed)}

    items: list[dict] = []
    furniture: list[str] = []
    tables: list[dict] = []
    for b in stream:
        if b["kind"] == "paragraph" and any(p.match(b["text"]) for p in FURNITURE):
            furniture.append(b["text"])
            continue
        m = ITEM.match(b["text"]) if b["kind"] == "paragraph" and len(b["text"]) <= 150 else None
        if m:
            items.append(
                {
                    "item": m.group(1).upper(),
                    "title": m.group(2),
                    "styles": [],
                    "headings": [],
                    "blocks": [],
                }
            )
            continue
        if not items:
            continue
        item = items[-1]
        if b["kind"] == "table":
            item["blocks"].append(b)
            continue
        found = heading_text(b)
        if found and b["text"] not in labels:
            style, text = found
            if style not in item["styles"]:
                item["styles"].append(style)
            item["headings"].append(
                {"level": item["styles"].index(style) + 1, "style": style, "text": text}
            )
            b = {**b, "heading": len(item["headings"]) - 1}
        item["blocks"].append(b)

    for item in items:
        if item["item"] == "8":
            _item8_levels(item["headings"])
        stack: list[str] = []
        for h in item["headings"]:
            stack = stack[: h["level"] - 1] + [h["text"]]
            h["path"] = list(stack)
        path: list[str] = []
        last_units: tuple[tuple[str, ...], str] | None = None  # (path, units) of the previous table
        for i, b in enumerate(item["blocks"]):
            if "heading" in b:
                path = item["headings"][b["heading"]]["path"]
            elif b["kind"] == "table":
                caption = [
                    p["text"][-200:]
                    for p in item["blocks"][max(0, i - 2) : i]
                    if p["kind"] == "paragraph"
                ]
                found = UNITS.search(" ".join(caption + [" ".join(r) for r in b["rows"][:3]]))
                units, source = (
                    (f"in {found.group(1).lower()}", "caption") if found else (None, None)
                )
                # A continuation table, or one under a Note that states units once, inherits them.
                if not units and last_units and tuple(path[:2]) == last_units[0]:
                    units, source = last_units[1], "inherited"
                if units:
                    last_units = (tuple(path[:2]), units)
                index = re.match(r"index to ", b["rows"][0][0], re.I)
                tables.append(
                    {
                        "id": f"T{len(tables) + 1:02d}",
                        "item": item["item"],
                        "kind": "index" if index else "data",
                        "path": path,
                        "rows": len(b["rows"]),
                        "first_row": " | ".join(b["rows"][0])[:120],
                        "units": units,
                        "units_from": source,
                    }
                )

    return {
        "status": "draft",
        "spec": "experiments/V0_PARSING_CHUNKING.md §4",
        "source": {"file": str(html_path), "ticker": ticker, "fiscal_year": fiscal_year},
        "tokenizer": TOKENIZER,
        "items": [
            {
                "item": i["item"],
                "title": i["title"],
                "text": _item_text(i),
                "headings": i["headings"],
            }
            for i in items
        ],
        "risk_factors": _risk_factors(next((i for i in items if i["item"] == "1A"), None)),
        "tables": tables,
        "furniture": {"count": len(furniture), "examples": sorted(set(furniture))[:3]},
        "_items_raw": items,
    }


def _item8_levels(headings: list[dict]) -> None:
    """Statements, the notes header, the index and auditor reports = 1; "Note N" = 2; inside a note
    each new style goes one level deeper (3, 4, ...); inside anything else, one level below it. An
    auditor's critical audit matters (each a topic heading) sit under "Critical Audit Matter(s)"."""
    styles: list[str] = []
    in_note = in_cam = False
    for h in headings:
        if (ITEM8_TOP.match(h["text"]) or h["text"].isupper()) and not NOTE.match(h["text"]):
            h["level"], in_note, in_cam = 1, False, False
        elif NOTE.match(h["text"]):
            h["level"], in_note = 2, True
        elif in_note:
            if h["style"] not in styles:
                styles.append(h["style"])
            h["level"] = 3 + styles.index(h["style"])
        elif in_cam:
            h["level"] = 3
        else:
            h["level"], in_cam = 2, h["text"].lower().startswith("critical audit matter")


def _item_text(item: dict) -> str:
    return "\n".join(
        b["text"] if b["kind"] == "paragraph" else "\n".join(" | ".join(r) for r in b["rows"])
        for b in item["blocks"]
    )


def _risk_factors(item: dict | None) -> dict | None:
    """Units of Item 1A: the preamble before the first heading, an optional summary (a top-level
    heading containing "summary"), and one unit per deepest-level heading (the risk factors)."""
    if not item or not item["headings"]:
        return None
    deepest = max(h["level"] for h in item["headings"])
    units: list[dict] = []
    current: dict | None = None
    preamble: list[str] = []
    for b in item["blocks"]:
        text = (
            b["text"] if b["kind"] == "paragraph" else "\n".join(" | ".join(r) for r in b["rows"])
        )
        if "heading" in b:
            h = item["headings"][b["heading"]]
            if "summary" in h["text"].lower() and h["level"] == 1:
                current = {"kind": "summary", "heading": h["text"], "path": h["path"], "body": []}
                units.append(current)
            elif h["level"] == deepest:
                current = {
                    "kind": "risk_factor",
                    "heading": h["text"],
                    "path": h["path"],
                    "body": [],
                }
                units.append(current)
            elif current and current["kind"] == "summary":
                current["body"].append(text)  # a summary's own subheadings stay in the summary
            else:
                current = None  # a category heading belongs to the next risk factor's path
            # A run-in heading's body text is in the same paragraph.
            if current and text != h["text"]:
                current["body"].append(text[len(h["text"]) :].strip())
            continue
        (current["body"] if current else preamble).append(text)
    return {"preamble": "\n".join(preamble) or None, "units": units}


def finish(gt: dict) -> dict:
    """Ids, end anchors, char and token counts; drops the working fields."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(gt["tokenizer"], local_files_only=True)
    n = lambda s: len(tok.encode(s, add_special_tokens=False))  # noqa: E731
    gt.pop("_items_raw", None)
    for item in gt["items"]:
        text = item.pop("text")
        item.update(chars=len(text), tokens=n(text), ends_with=text[-80:])
    rf = gt["risk_factors"]
    if rf:
        if rf["preamble"]:
            text = rf["preamble"]
            rf["preamble"] = {
                "starts_with": text[:80],
                "ends_with": text[-80:],
                "chars": len(text),
                "tokens": n(text),
            }
        k = 0
        for u in rf["units"]:
            text = "\n".join([u["heading"]] + u.pop("body"))
            if u["kind"] == "risk_factor":
                k += 1
                u["id"] = f"RF{k:02d}"
            u.update(
                category=u["path"][0] if len(u["path"]) > 1 else None,
                ends_with=text[-80:],
                chars=len(text),
                tokens=n(text),
            )
    return gt


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("html", type=Path)
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    gt = finish(draft(args.html, args.ticker, args.year))
    args.out.write_text(json.dumps(gt, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    rf = gt["risk_factors"] or {"units": []}
    headings = sum(len(i["headings"]) for i in gt["items"])
    risks = sum(u["kind"] == "risk_factor" for u in rf["units"])
    print(
        f"{args.out}: {len(gt['items'])} Items, {headings} headings, {risks} risk factors, "
        f"{len(gt['tables'])} tables, {gt['furniture']['count']} furniture lines"
    )


if __name__ == "__main__":
    main()
