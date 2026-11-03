"""docs/V4_INTERFACES.md §4: every tool example matches what the tool returns.

Each tool block in §4 has a JSON example {"as_of"?, "call", "result"}. The test runs the call
on the V4 store at that as-of (default 2026-08-06) and checks the result's shape: the same keys
at every level (lists compared on their first item), so a renamed, added or dropped field fails
until the doc is updated. Values are not compared: rebuilds may move them. Skipped without the
V4 store.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "V4_INTERFACES.md"
DB = ROOT / "data" / "v4" / "build" / "parent_child.db"
DEFAULT_AS_OF = "2026-08-06"


def examples() -> list[tuple[str, dict]]:
    text = DOC.read_text()
    section = text[text.index("## 4. Tool calls"):text.index("## 5. LLM calls")]
    out = []
    for block in re.split(r"^### ", section, flags=re.M)[1:]:
        tool = re.search(r"`(\w+)`", block.splitlines()[0]).group(1)
        for body in re.findall(r"```json\n(.*?)```", block, flags=re.S):
            out.append((tool, json.loads(body)))
    return out


def shape(value):
    """Keys at every level; a list by its first item."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


def same_shape(doc, real, path="result") -> list[str]:
    if isinstance(doc, dict) and isinstance(real, dict):
        problems = []
        if set(doc) != set(real):
            problems.append(f"{path}: doc {sorted(doc)} vs code {sorted(real)}")
        for k in set(doc) & set(real):
            problems += same_shape(doc[k], real[k], f"{path}.{k}")
        return problems
    if isinstance(doc, list) and isinstance(real, list) and doc and real:
        return same_shape(doc[0], real[0], f"{path}[0]")
    return []


def test_every_tool_has_an_example():
    from tenk_agent.agent.tools import TOOL_SETS

    documented = {tool for tool, _ in examples()} | {"search_filings"}  # search: §3
    assert set(TOOL_SETS["research"]) <= documented


@pytest.fixture(scope="module")
def v4_corpus():
    """The V4 company list for this module; conftest.py pins V0–V3 for the rest. corpus.py
    reads its file at import, so its globals are switched too, then restored."""
    from tenk_agent import corpus

    path = ROOT / "companies.yaml"
    companies, years = corpus.load(path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TENK_COMPANIES", str(path))
        mp.setattr(corpus, "COMPANIES", companies)
        mp.setattr(corpus, "FISCAL_YEARS", years)
        yield


@pytest.fixture(scope="module")
def toolboxes(v4_corpus):
    if not DB.exists():
        pytest.skip("needs the V4 store")
    from tenk_agent.agent.tools import toolbox_from_settings
    from tenk_agent.asof import AsOf
    from tenk_agent.config import load
    from tenk_agent.research.book import Book

    settings = load(ROOT / "config.yaml") | {"db": str(DB), "data_dir": str(DB.parents[1])}
    cache = {}

    def get(as_of: str):
        if as_of not in cache:
            tb = toolbox_from_settings(settings, "research", search=False, asof=AsOf.of(as_of))
            tb.book = Book(tb.store)
            cache[as_of] = tb
        return cache[as_of]

    return get


@pytest.mark.parametrize("tool,example", examples(), ids=[t for t, _ in examples()])
def test_tool_example_matches_code(toolboxes, tool, example):
    real = toolboxes(example.get("as_of", DEFAULT_AS_OF)).call(tool, example["call"])
    assert "error" not in real, real
    assert not same_shape(shape(example["result"]), shape(real))
