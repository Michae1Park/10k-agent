"""The V0 chunkers' sentence cuts (C5) and table captions (C3)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "v0"))

import chunkers  # noqa: E402

chunkers.CAPTION_CHARS = 300


def test_sentences_skip_abbreviations_and_join_back():
    text = "Sales outside the U.S. Department of Commerce rose. Apple Inc. sells; it grows. End"
    pieces = chunkers.sentences(text)
    assert pieces == [
        "Sales outside the U.S. Department of Commerce rose. ",
        "Apple Inc. sells; ",
        "it grows. ",
        "End",
    ]
    assert "".join(pieces) == text


def test_captions_are_the_short_blocks_before_each_table():
    blocks = [
        {"id": "h", "kind": "heading", "text": "Segment Operating Performance"},
        {"id": "c", "kind": "paragraph", "text": "The following table shows net sales:"},
        {"id": "t", "kind": "table", "text": "", "rows": []},
        {"id": "p", "kind": "paragraph", "text": "Americas net sales increased."},
    ]
    section = {"blocks": blocks}
    assert chunkers.captions(section, blocks[1:]) == {"h", "c"}
