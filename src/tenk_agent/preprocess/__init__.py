"""V0 preprocessing: one 10-K's HTML → one structured document (sections, headings, tables).

  step 1  rules: HTML → blocks with features            blocks.py
  step 2  LLM structure agent: labels, validated and
          repaired; assembled into the document         structure.py, validation.py

Spec and results: experiments/V0_PARSING_CHUNKING.md §2. Guide: docs/V0_PARSING_CHUNKING.md.
"""

import json
import time
from dataclasses import asdict
from pathlib import Path

from tenk_agent.preprocess.blocks import cover_metadata, read_blocks
from tenk_agent.preprocess.structure import Labeler, assemble


class StaleLabels(RuntimeError):
    """labels.json was made with other labeling settings."""


def preprocess(
    html: Path, cover: Path, out: Path, llm: dict, labeling: dict, relabel: bool = False
) -> dict:
    """HTML → blocks → labels → document, written to `out` as blocks.json, labels.json,
    labels-log.json and document.json. The labels are cached: reused unless `relabel`, and
    only if made with the same `labeling` settings. `cover` is the full filing, for the dei:
    cover tags (the filing itself unless `html` is an excerpt). `llm`: {model, base_url}."""
    blocks = read_blocks(html.read_text(encoding="utf-8"))
    filing = cover_metadata(cover)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "blocks.json", [asdict(b) for b in blocks])
    labels_path = out / "labels.json"
    if labels_path.exists() and not relabel:
        cached = json.loads((out / "labels-log.json").read_text()).get("labeling")
        if cached != labeling:
            raise StaleLabels(
                f"{labels_path} was labeled with {cached}, not {labeling}: "
                "relabel, or write to another folder to keep both"
            )
        labels = json.loads(labels_path.read_text())
    else:
        t = time.time()
        labeler = Labeler(llm["model"], llm["base_url"], filing, labeling)
        labels = labeler.label(blocks)
        _write(labels_path, labels)
        log = {
            "labeling": labeling,
            "seconds": round(time.time() - t),
            "usage": labeler.usage,
            "windows": labeler.log,
        }
        _write(out / "labels-log.json", log)
        print(f"labeled {len(blocks)} blocks in {log['seconds']} s, {labeler.usage}")
    document = assemble(blocks, labels, filing)
    _write(out / "document.json", document)
    print(
        "sections:",
        [(s["item"], len(s["blocks"])) for s in document["sections"]],
        "dropped:",
        document["dropped"],
        "review:",
        len(document["review"]),
    )
    return document


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
