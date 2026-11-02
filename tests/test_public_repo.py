"""The public repo holds no credentials, downloaded data, run outputs or third-party market data
(D-046): Tiingo prices, FRED and Damodaran values and anything derived from them stay local."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"


def tracked() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.splitlines()


def test_no_local_only_paths_tracked():
    bad = [p for p in tracked() if p.startswith(("data/", "output/"))
           or Path(p).name in ("credentials.yaml", ".env")]
    assert not bad, bad


def test_example_note_is_redacted():
    note = (EXAMPLES / "UBER-2026-08-06-note.md").read_text()
    assert "Public copy, redacted" in note
    summary = next(line for line in note.splitlines() if line.startswith("| local only"))
    assert "$" not in summary
    for row in re.findall(r"^\| E-\d+ \| [^|]+ \| ([^|]+) \| ((?:px|macro:|erp:)[^|]*|[^|]*val-[^|]*)\|",
                          note, re.MULTILINE):
        assert row[0].strip() == "local only", row


def test_no_price_ids_with_values_in_docs():
    """An evidence row citing a price, macro or ERP id never shows its value in a tracked doc."""
    pattern = re.compile(r"\| [^|]*\d[^|]* \| (px:[A-Z]+:\d{4}|macro:[A-Z0-9]+:\d{4}|erp:)")
    for path in tracked():
        if path.endswith(".md"):
            text = (ROOT / path).read_text(errors="ignore")
            assert not pattern.search(text), path
