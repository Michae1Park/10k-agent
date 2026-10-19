"""The V0 sanity cases quote the filing correctly, and the checker grades chunks as the spec says.

A `find` that occurs twice would let a case pass on the wrong chunk, so each must be unique.
"""

import re
import sys
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "v0"))
sys.path.insert(0, str(ROOT / "experiments" / "V0_testdata"))

import v0_ground_truth as gtb  # noqa: E402
from sanity import CASES, check, finds  # noqa: E402
from score import key  # noqa: E402

SPEC = yaml.safe_load(CASES.read_text(encoding="utf-8"))


def filing_keys() -> list[str]:
    soup = BeautifulSoup((ROOT / SPEC["source"]).read_text(encoding="utf-8"), "lxml")
    return [
        key(b["text"] if b["kind"] == "paragraph" else "\n".join(" | ".join(r) for r in b["rows"]))
        for b in gtb.blocks(soup.body)
    ]


def test_cases_quote_the_filing():
    keys = filing_keys()
    count = lambda text: sum(k.count(key(text)) for k in keys)  # noqa: E731
    for c in SPEC["cases"]:
        for f in finds(c):
            assert count(f) == 1, (c["id"], f)
        for text in [*c.get("with", []), *c.get("without", []), *c.get("path", [])]:
            assert count(text) >= 1, (c["id"], text)


def test_case_ids_unique():
    ids = [c["id"] for c in SPEC["cases"]]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"S\d\d", i) for i in ids)


CASE = {"find": "alpha beta", "item": "1", "path": ["A", "B"], "without": ["gamma"]}


def chunk(text: str, path: list[str], item: str = "1") -> dict:
    return {"id": "c", "text": text, "item": item, "heading_path": path, "kind": "prose"}


def test_check_grades():
    assert check(CASE, [chunk("Alpha, beta.", ["A", "B"])]) == ("ok", [])
    assert check(CASE, [chunk("alpha beta", ["A"])])[0] == "coarser"
    assert check(CASE, [chunk("alpha beta", ["A", "C"])])[0] == "fail"
    assert check(CASE, [chunk("alpha beta gamma", ["A", "B"])])[0] == "fail"
    assert check(CASE, [chunk("alpha beta", ["A", "B"], item="2")])[0] == "fail"
    assert check(CASE, [chunk("alpha", ["A", "B"]), chunk("beta", ["A", "B"])])[0] == "fail"
    absent = {"find": "footer 6", "absent": True}
    assert check(absent, [chunk("body", [])]) == ("ok", [])
    assert check(absent, [chunk("Footer | 6", [])])[0] == "fail"
