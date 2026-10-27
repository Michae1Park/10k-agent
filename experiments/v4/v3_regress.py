"""Run: taskset -c 16-31 uv run python -W ignore experiments/v4/v3_regress.py
V3 byte-identity check for migration steps: re-derive blocks/document from cached labels,
validator output over all labels, and every chunk's embedding text; print one hash per part."""
import hashlib, json, sys, sqlite3
from dataclasses import asdict
from pathlib import Path
from tenk_agent.preprocess.blocks import cover_metadata, read_blocks
from tenk_agent.preprocess.structure import assemble, validate, Outline
from tenk_agent.retrieval.index import embedding_text
from tenk_agent.store import Store

h = lambda o: hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()[:16]
for d in sorted(Path("data/v0-chunking").glob("*-FY*")):
    if not (d / "labels.json").exists(): continue
    t, fy = d.name.split("-")
    html = next(Path(f"data/raw/filings/{t}/{fy}").glob("*.htm"))
    blocks = read_blocks(html.read_text(encoding="utf-8"))
    labels = json.loads((d / "labels.json").read_text())
    same_blocks = json.loads((d / "blocks.json").read_text()) == [asdict(b) for b in blocks]
    doc = assemble(blocks, labels, cover_metadata(html))
    same_doc = json.loads((d / "document.json").read_text()) == json.loads(json.dumps(doc))
    v = validate(blocks, labels, Outline(texts={b.id: b.text for b in blocks}))
    print(d.name, "blocks", same_blocks, "document", same_doc, "violations", len(v), h([str(x) for x in v]))
from tenk_agent.store import _chunk, _child
import os
db = Store(Path(os.environ.get("V3_DB", "data/v0-chunking/parent_child.db")))
chunks = [embedding_text(_chunk(r)) for r in db._query("select * from chunks order by id")]
children = [embedding_text(_child(r)) for r in db._query("select * from children order by id")]
print("embedding texts", len(chunks), len(children), h(chunks), h(children))
queries = ["revenue growth", "risk factors competition", "share repurchase", "data center",
           "operating income segment", "foreign currency", "income taxes effective rate"]
hits = [[c.id for c in db.search(q, k=10)] for q in queries]
hits += [[c.id for c in db.search(q, tickers=["AAPL"], fiscal_years=[2025], k=5)] for q in queries]
kids = [[c.id for c in db.child_search(q, k=10)] for q in queries]
print("keyword search", h(hits), "child search", h(kids))
print("filings", h([(f.ticker, f.fiscal_year, f.accession_no, f.filing_date) for f in db.filings()]),
      "chunks()", h([c.id for c in db.chunks("AAPL", 2025)]),
      "get_chunk", h([db.get_chunk(c).text if db.get_chunk(c) else None for c in hits[0]]))
# The claim check over every claim of the saved V3 runs (verify.py, refs.py).
from tenk_agent.agent.sec import SecData
from tenk_agent.generation.verify import verify_claim
sec = SecData(Path("data/raw/edgar"))
checked = []
for run in sorted(Path("output/generation").glob("v3-*/results.json")):
    for row in json.loads(run.read_text()):
        for claim in row.get("claims") or []:
            out = verify_claim({k: v for k, v in claim.items() if k not in ("status", "status_reason")}, db, sec)
            checked.append((run.parent.name, row["id"], claim.get("id"), out["status"], out["status_reason"]))
print("claim check", len(checked), h(checked))
