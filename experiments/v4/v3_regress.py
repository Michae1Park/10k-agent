"""V3 byte identity for the V4 migration (design doc §14.1): one hash per part, compared with
v3_baseline.txt after every change to shared code.

  blocks, documents   re-derived from each V3 filing's cached LLM labels
  violations          the structure validator over all labels
  embedding texts     every chunk's and child's header + text
  search, store       keyword and child search, filings, chunks(), get_chunk
  claim check         every claim of the saved V3 runs re-verified
  tools, system       the agent's tool schemas and system prompt per V3 tool set

  taskset -c 16-31 uv run python -W ignore experiments/v4/v3_regress.py \
    | diff experiments/v4/v3_baseline.txt -
"""

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from tenk_agent.agent.loop import build_system
from tenk_agent.agent.sec import SecData
from tenk_agent.agent.tools import Toolbox
from tenk_agent.generation.verify import verify_claim
from tenk_agent.preprocess.blocks import cover_metadata, read_blocks
from tenk_agent.preprocess.structure import Outline, assemble, validate
from tenk_agent.retrieval.index import embedding_text
from tenk_agent.store import Store, _child, _chunk

QUERIES = [
    "revenue growth", "risk factors competition", "share repurchase", "data center",
    "operating income segment", "foreign currency", "income taxes effective rate",
]  # fmt: skip


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def filings() -> None:
    for d in sorted(Path("data/v0-chunking").glob("*-FY*")):
        if not (d / "labels.json").exists():
            continue
        ticker, year = d.name.split("-")
        html = next(Path(f"data/raw/filings/{ticker}/{year}").glob("*.htm"))
        blocks = read_blocks(html.read_text(encoding="utf-8"))
        labels = json.loads((d / "labels.json").read_text())
        same_blocks = json.loads((d / "blocks.json").read_text()) == [asdict(b) for b in blocks]
        doc = assemble(blocks, labels, cover_metadata(html))
        same_doc = json.loads((d / "document.json").read_text()) == json.loads(json.dumps(doc))
        v = validate(blocks, labels, Outline(texts={b.id: b.text for b in blocks}))
        print(d.name, "blocks", same_blocks, "document", same_doc, "violations", len(v),
              h([str(x) for x in v]))


def store(db: Store) -> None:
    chunks = [embedding_text(_chunk(r)) for r in db._query("select * from chunks order by id")]
    children = [embedding_text(_child(r)) for r in db._query("select * from children order by id")]
    print("embedding texts", len(chunks), len(children), h(chunks), h(children))
    hits = [[c.id for c in db.search(q, k=10)] for q in QUERIES]
    hits += [
        [c.id for c in db.search(q, tickers=["AAPL"], fiscal_years=[2025], k=5)] for q in QUERIES
    ]
    kids = [[c.id for c in db.child_search(q, k=10)] for q in QUERIES]
    print("keyword search", h(hits), "child search", h(kids))
    loaded = [(f.ticker, f.fiscal_year, f.accession_no, f.filing_date) for f in db.filings()]
    print("filings", h(loaded),
          "chunks()", h([c.id for c in db.chunks("AAPL", 2025)]),
          "get_chunk", h([db.get_chunk(c).text if db.get_chunk(c) else None for c in hits[0]]))


def claims(db: Store, sec: SecData) -> None:
    checked = []
    for run in sorted(Path("output/generation").glob("v3-*/results.json")):
        for row in json.loads(run.read_text()):
            for claim in row.get("claims") or []:
                bare = {k: v for k, v in claim.items() if k not in ("status", "status_reason")}
                out = verify_claim(bare, db, sec)
                checked.append((run.parent.name, row["id"], claim.get("id"), out["status"],
                                out["status_reason"]))
    print("claim check", len(checked), h(checked))


def agent(db: Store, sec: SecData) -> None:
    for tool_set in ("text", "full"):
        box = Toolbox(db, None, sec, tool_set)
        print("tools", tool_set, h([asdict(s) for s in box.specs]),
              "system", h(build_system(box, 12)))


if __name__ == "__main__":
    db = Store(Path(os.environ.get("V3_DB", "data/v0-chunking/parent_child.db")))
    sec = SecData(Path("data/raw/edgar"))
    filings()
    store(db)
    claims(db, sec)
    agent(db, sec)
