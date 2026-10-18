"""Score chunks against the reviewed ground truth (criteria 1–3 and the sanity checks).
Spec: experiments/V0_PARSING_CHUNKING.md §4.

Matching is by text, not block id: the filing's text stream is rebuilt with the ground-truth
builder's own block reader (independent of the preprocessing), every stream block gets its
ground-truth Item, heading path and risk-factor unit, and each chunk line is located in that
stream by an alphanumeric-only key (so "$1,234" and "$ | 1,234" match).
"""

import bisect
import math
import re
import statistics
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "V0_testdata"))

import v0_ground_truth as gtb  # noqa: E402
from chunkers import PARENT, TARGET, full_parent_ratio  # noqa: E402

WHOLE = 0.95  # share of a block's text that must be in a chunk to count as "whole"
MIN_KEY = 8  # shorter lines are too ambiguous to locate


def key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


class Reference:
    """The filing's text stream with ground-truth labels per block."""

    def __init__(self, html_path: Path, gt: dict):
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        self.blocks: list[dict] = []
        self.furniture: set[str] = set()
        items = {i["item"]: i["headings"] for i in gt["items"]}
        tables = iter(gt["tables"])
        item, heads, hi, path = None, [], 0, []
        for b in gtb.blocks(soup.body):
            text = (
                b["text"]
                if b["kind"] == "paragraph"
                else "\n".join(" | ".join(r) for r in b["rows"])
            )
            if b["kind"] == "paragraph" and any(p.match(text) for p in gtb.FURNITURE):
                self.furniture.add(key(text))
                continue
            m = gtb.ITEM.match(text) if b["kind"] == "paragraph" and len(text) <= 150 else None
            if m:
                item, heads, hi, path = m.group(1).upper(), items.get(m.group(1).upper(), []), 0, []
                continue
            if item is None:
                continue
            heading = False
            if (
                b["kind"] == "paragraph"
                and hi < len(heads)
                and key(text).startswith(key(heads[hi]["text"]))
            ):
                path, heading = heads[hi]["path"], key(text) == key(heads[hi]["text"])
                hi += 1
            entry = {
                "key": key(text),
                "item": item,
                "path": [key(p) for p in path],
                "heading": heading,
                "text": text,
                "unit": None,
                "table": next(tables) if b["kind"] == "table" else None,
            }
            self.blocks.append(entry)
        self._units(gt)
        # A label (a short line repeated over tables, e.g. the company name above each statement)
        # is not a heading (§4.4) and carries no structure, so 1b ignores it.
        counts: dict[str, int] = {}
        for blk in self.blocks:
            counts[blk["key"]] = counts.get(blk["key"], 0) + 1
        for blk in self.blocks:
            blk["label"] = (
                blk["table"] is None
                and not blk["heading"]
                and len(blk["text"]) <= 40
                and counts[blk["key"]] >= 3
            )
        self.starts, pos = [], 0
        for blk in self.blocks:
            self.starts.append(pos)
            pos += len(blk["key"])
        self.stream = "".join(b["key"] for b in self.blocks)
        for blk in self.blocks:  # what locate() can find: lines shorter than MIN_KEY are skipped
            blk["locatable"] = sum(
                n for ln in blk["text"].split("\n") if (n := len(key(ln))) >= MIN_KEY
            ) or len(blk["key"])
        self._occ: dict[str, tuple[list[int], int]] = {}
        self.exact: dict[str, list[int]] = {}
        for i, blk in enumerate(self.blocks):
            self.exact.setdefault(blk["key"], []).append(self.starts[i])

    def _units(self, gt: dict) -> None:
        """Item 1A units: preamble, summary, risk factors; a category joins the next unit."""
        rf = gt["risk_factors"] or {"units": []}
        units = rf["units"]
        ui, current, pending = 0, "preamble", []
        for blk in (b for b in self.blocks if b["item"] == "1A"):
            if blk["heading"] and ui < len(units) and blk["key"] == key(units[ui]["heading"]):
                current = units[ui].get("id") or units[ui]["kind"]
                ui += 1
                for p in pending:
                    p["unit"] = current
                pending = []
                blk["unit"] = current
            elif blk["heading"]:
                pending.append(blk)
            else:
                blk["unit"] = current
        for p in pending:
            p["unit"] = current

    def locate(self, chunk_text: str, anchor: int) -> list[tuple[int, int]]:
        """Stream spans of a chunk's lines. The chunk is pinned by a pivot, its rarest line, at that
        line's first occurrence after the anchor (the previous chunk's start); every other line
        takes the occurrence nearest to where it should be relative to the pivot. So a row shared
        by sibling tables (T35–T37's header), a statement title also in the index table, or a
        heading word inside an earlier paragraph lands in the right place."""
        lines = [k for line in chunk_text.split("\n") if len(k := key(line)) >= MIN_KEY]
        found = [(j, k) for j, k in enumerate(lines) if self._occurrences(k)[1]]
        if not found:
            return []
        offsets = [0]
        for k in lines[:-1]:
            offsets.append(offsets[-1] + len(k))
        pj, pk = min(found, key=lambda jk: self._occurrences(jk[1])[1])
        occ = self._occurrences(pk)[0]
        base = next((p for p in occ if p >= anchor), occ[-1]) - offsets[pj]
        spans = []
        for j, k in found:
            at = min(self._occurrences(k)[0], key=lambda p: abs(p - (base + offsets[j])))
            spans.append((at, at + len(k)))
        return spans

    def _occurrences(self, k: str) -> tuple[list[int], int]:
        """(positions, count) of a line's key anywhere in the stream."""
        if k not in self._occ:
            pos, at = [], self.stream.find(k)
            while at >= 0:
                pos.append(at)
                at = self.stream.find(k, at + 1)
            self._occ[k] = (pos, len(pos))
        return self._occ[k]

    def block_at(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos) - 1


def score_all(html_path: Path, gt: dict, document: dict, chunks: dict[str, list[dict]]) -> dict:
    ref = Reference(Path(html_path), gt)
    out = {"preprocessing": headings_score(document, gt)}
    for method, cs in chunks.items():
        target = PARENT if method == "parent_child" else TARGET
        out[method] = score_method(ref, gt, cs, target, method == "parent_child")
    return out


def score_method(ref: Reference, gt: dict, chunks: list[dict], target: int, parents: bool) -> dict:
    located = []  # per chunk: {block index: covered chars}
    anchor = 0
    for c in chunks:
        spans = ref.locate(c["text"], anchor)
        cover: dict[int, set] = {}
        for s, e in spans:
            for pos in range(s, e):
                cover.setdefault(ref.block_at(pos), set()).add(pos)
        located.append(cover)
        if spans:
            anchor = spans[0][0]
    n_blocks = len(ref.blocks)
    whole = lambda cover, i: len(cover.get(i, ())) >= WHOLE * max(1, ref.blocks[i]["locatable"])  # noqa: E731

    # Criterion 1
    item_ok, classes, unlocated = 0, {"exact": 0, "merged": 0, "coarse": 0, "wrong": 0}, 0
    for c, cover in zip(chunks, located, strict=True):
        if not cover:
            unlocated += 1
            continue
        idx = sorted(cover)
        item_ok += all(ref.blocks[i]["item"] == c["item"] for i in idx)
        classes[path_class(ref, c, idx, cover, whole)] += 1
    scored = len(chunks) - unlocated

    # Criterion 2 (Item 1A)
    units = [
        u for u in (gt["risk_factors"] or {"units": []})["units"] if u["kind"] == "risk_factor"
    ]
    rf_chunks = [
        (c, {ref.blocks[i]["unit"] for i in cover if ref.blocks[i]["item"] == "1A"})
        for c, cover in zip(chunks, located, strict=True)
    ]
    rf_chunks = [(c, us) for c, us in rf_chunks if us]
    pure = sum(len(us) == 1 for _, us in rf_chunks)
    intact, frag, carried, later = 0, [], 0, 0
    small = [u for u in units if u["tokens"] <= target]
    for u in units:
        blocks = [i for i in range(n_blocks) if ref.blocks[i]["unit"] == u["id"]]
        touching = [
            (c, cover)
            for c, cover in zip(chunks, located, strict=True)
            if any(i in cover for i in blocks)
        ]
        frag.append(len(touching) / math.ceil(u["tokens"] / target))
        covered = sum(len(set().union(*(cv.get(i, set()) for _, cv in touching))) for i in blocks)
        if (
            u["tokens"] <= target
            and len(touching) == 1
            and covered >= WHOLE * sum(len(ref.blocks[i]["key"]) for i in blocks)
        ):
            intact += 1
        for c, _ in touching[1:]:
            later += 1
            carried += key(u["heading"]) in key(c["text"]) or bool(
                c["heading_path"] and key(c["heading_path"][-1]) == key(u["heading"])
            )

    # Criterion 3 (parents only)
    ratio = None
    if parents:
        prose = [c for c in chunks if c["kind"] == "prose"]
        full = [r for c in prose if c["tokens"] >= 0.8 * PARENT and (r := full_parent_ratio(c))]
        ratio = {
            "full_parents": len(full),
            "median": round(statistics.median(full), 2) if full else None,
            "pct_4_6": pct(sum(4 <= r <= 6 for r in full), len(full)),
            "small_parents_pct": pct(sum(c["tokens"] < 0.8 * PARENT for c in prose), len(prose)),
        }

    # Sanity checks
    all_cover: set[int] = set().union(*(set().union(*cv.values()) for cv in located if cv))
    noise = sum(
        key(line) in ref.furniture for c in chunks for line in c["text"].split("\n") if key(line)
    )
    data = [i for i in range(n_blocks) if (ref.blocks[i]["table"] or {}).get("kind") == "data"]
    tables_ok = 0
    for i in data:
        want = scale(ref.blocks[i]["table"]["units"])
        if any(
            whole(cv, i) and scale(c.get("units")) == want
            for c, cv in zip(chunks, located, strict=True)
        ):
            tables_ok += 1
    sizes = sorted(c["tokens"] for c in chunks)
    return {
        "chunks": len(chunks),
        "unlocated": unlocated,
        "tokens": {
            "mean": round(statistics.mean(sizes)),
            "p95": sizes[int(0.95 * (len(sizes) - 1))],
            "max": sizes[-1],
        },
        "1a_item": pct(item_ok, scored),
        "1b_path": {k: pct(v, scored) for k, v in classes.items()},
        "2a_purity": pct(pure, len(rf_chunks)),
        "2a_chunks": len(rf_chunks),
        "2b_intact": pct(intact, len(small)),
        "2b_of": len(small),
        "2c_fragmentation": round(statistics.mean(frag), 2),
        "2d_heading": pct(carried, later) if later else None,
        "2d_of": later,
        "3_ratio": ratio,
        "coverage": pct(len(all_cover), len(ref.stream)),
        "duplication": round(
            sum(len(s) for cv in located for s in cv.values()) / len(ref.stream), 2
        ),
        "noise": noise,
        "tables": f"{tables_ok}/{len(data)}",
    }


def path_class(ref: Reference, chunk: dict, idx: list[int], cover: dict, whole) -> str:
    """1b: exact / merged / coarse / wrong (§4.1)."""
    idx = [i for i in idx if not ref.blocks[i]["label"]] or idx
    paths = [tuple(ref.blocks[i]["path"]) for i in idx]
    keep = [
        p
        for i, p in zip(idx, paths, strict=True)
        if not (
            ref.blocks[i]["heading"] and any(len(q) > len(p) and q[: len(p)] == p for q in paths)
        )
    ]
    keep = keep or paths
    mine = tuple(key(p) for p in chunk["heading_path"])
    distinct = set(keep)
    if len(distinct) == 1:
        return "exact" if mine == keep[0] else "wrong"
    common = list(keep[0])
    for p in keep[1:]:
        n = 0
        while n < min(len(common), len(p)) and common[n] == p[n]:
            n += 1
        common = common[:n]
    if mine != tuple(common):
        return "wrong"
    members = [
        i
        for i in range(len(ref.blocks))
        if any(
            tuple(ref.blocks[i]["path"][: len(q)]) == q for q in distinct if len(q) > len(common)
        )
    ]
    return "merged" if all(whole(cover, i) for i in members) else "coarse"


def headings_score(document: dict, gt: dict) -> dict:
    """1c: the document's headings vs the ground truth's, per Item."""
    got = [
        (s["item"], key(b["text"]), b["level"])
        for s in document["sections"]
        for b in s["blocks"]
        if b["kind"] == "heading"
    ]
    want = [(i["item"], key(h["text"]), h["level"]) for i in gt["items"] for h in i["headings"]]
    pool = list(want)
    matched = level_ok = 0
    for item, k, level in got:
        hit = next((w for w in pool if w[0] == item and w[1] == k), None)
        if hit:
            pool.remove(hit)
            matched += 1
            level_ok += hit[2] == level
    return {
        "headings": len(got),
        "gt_headings": len(want),
        "precision": pct(matched, len(got)),
        "recall": pct(matched, len(want)),
        "level": pct(level_ok, matched),
        "items": [s["item"] for s in document["sections"]],
        "dropped": document["dropped"],
        "review": len(document["review"]),
    }


def scale(units: str | None) -> str | None:
    m = re.search(r"(millions|thousands|billions)", units or "", re.I)
    return m.group(1).lower() if m else None


def pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None
