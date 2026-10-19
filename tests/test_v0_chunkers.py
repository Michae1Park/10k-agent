"""The V0 chunkers' sentence cuts (C5)."""

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
