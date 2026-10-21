"""V0 chunking: the structured document → chunks, one of three ways, all sharing table handling
and chunk metadata (common.py):

  rule          rule-based: a new chunk at every heading, prose packed to TARGET tokens (C1–C10)
  parent_child  LlamaIndex HierarchicalNodeParser: PARENT-token parents, CHILD-token children
  agentic       Chonkie SlumberChunker: the LLM picks each boundary

Every chunk stays inside one Item (C1); each data table is its own chunk (C2–C4); metadata and
ids are the same for all (T1–T3). Spec: experiments/V0_PARSING_CHUNKING.md §2.4–2.5, §3.
"""

import json
from pathlib import Path

from tenk_agent.chunking import agentic, parent_child, rule_based
from tenk_agent.chunking.common import configure

METHODS = ("rule", "parent_child", "agentic")


def run_all(
    document: dict, chunking: dict, model: str, base_url: str, methods=METHODS
) -> dict[str, list[dict]]:
    """method → its chunks. `chunking` is the config's `chunking:`; `model` and `base_url` are
    the LLM the agentic chunker calls."""
    configure(chunking)
    run = {
        "rule": lambda: rule_based.rule_based(document),
        "parent_child": lambda: parent_child.parent_child(document),
        "agentic": lambda: agentic.agentic(document, model, base_url),
    }
    return {m: run[m]() for m in methods}


def chunk(out: Path, llm: dict, chunking: dict, methods=METHODS) -> None:
    """out/document.json → out/chunks-<method>.json; out/chunks-log.json records the `chunking`
    settings each method's chunks were made with. `llm`: {model, base_url}."""
    document = json.loads((out / "document.json").read_text())
    log_path = out / "chunks-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    for method, chunks in run_all(
        document, chunking, llm["model"], llm["base_url"], methods
    ).items():
        (out / f"chunks-{method}.json").write_text(json.dumps(chunks, indent=1, ensure_ascii=False))
        log[method] = chunking
        print(f"{method}: {len(chunks)} chunks")
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False))


def is_current(out: Path, method: str, chunking: dict) -> bool:
    """out/chunks-<method>.json exists and was made with these `chunking` settings."""
    log_path = out / "chunks-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    return log.get(method) == chunking and (out / f"chunks-{method}.json").exists()
