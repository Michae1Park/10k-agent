"""The web UI's API (`tenk serve`): chat in Ask, Research or Investigate mode, Reports, sources.

  POST /api/chat                 {chat_id?, mode, question, as_of} → SSE trace, then "done"
  GET  /api/chats[/{id}]         saved chats (output/web/chats/<id>.json); DELETE removes one
  GET  /api/sources/{id}         a cited chunk or XBRL fact
  GET  /api/companies            the V4 coverage
  GET  /api/reports              saved report runs (output/reports/<T>/<as-of>/report.json)
  GET  /api/reports/{T}/{as_of}  one report: summary, note, exceptions, versions
  GET  /api/reports/{T}/{as_of}/files/{name}   workbook.xlsx, note.pdf, note.md
  POST /api/reports              {ticker, as_of, llm, resume} → a job; /api/jobs/{id}/events SSE
  POST /api/reports/approve      {version, by}
"""

import json
import re
import threading
import time
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from tenk_agent.corpus import COMPANIES
from tenk_agent.research.pipeline import OUT as REPORTS
from tenk_agent.web import engine

CHATS = REPORTS.parent / "web" / "chats"
FILES = {"workbook.xlsx", "note.pdf", "note.md"}
SAFE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _safe(*parts: str) -> None:
    if not all(SAFE.match(p) and ".." not in p for p in parts):
        raise HTTPException(400, "bad path")


def _sse(events) -> StreamingResponse:
    def stream():
        for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ChatIn(BaseModel):
    chat_id: str | None = None
    mode: str
    question: str
    as_of: str


class ReportIn(BaseModel):
    ticker: str
    as_of: str
    llm: bool = True
    resume: bool = False


class ApproveIn(BaseModel):
    version: str
    by: str


def create_app(settings: dict) -> FastAPI:
    app = FastAPI(title="tenk")
    jobs = engine.Jobs()
    chat_lock = threading.Lock()
    CHATS.mkdir(parents=True, exist_ok=True)

    def load_chat(chat_id: str) -> dict:
        _safe(chat_id)
        path = CHATS / f"{chat_id}.json"
        if not path.exists():
            raise HTTPException(404, "no such chat")
        return json.loads(path.read_text())

    def save_chat(chat: dict) -> None:
        chat["updated"] = time.time()
        (CHATS / f"{chat['id']}.json").write_text(json.dumps(chat, indent=1, default=str))

    # --- chat ------------------------------------------------------------------------------

    @app.post("/api/chat")
    def chat(body: ChatIn):
        if body.mode not in ("ask", "research", "investigate"):
            raise HTTPException(400, "mode: ask, research or investigate")
        date.fromisoformat(body.as_of)
        with chat_lock:
            if body.chat_id:
                record = load_chat(body.chat_id)
            else:
                record = {"id": uuid.uuid4().hex[:12], "title": body.question[:80],
                          "created": time.time(), "messages": []}
            user = {"role": "user", "mode": body.mode, "as_of": body.as_of,
                    "text": body.question, "time": time.time()}
            record["messages"].append(user)
            save_chat(record)
        emit = engine.Emitter()

        def work():
            try:
                result = engine.chat(settings, body.mode, body.question, body.as_of, emit)
                result.pop("steps", None)  # the trace below holds them
                message = {"role": "assistant", "time": time.time(), **result,
                           "trace": emit.log}
                emit("done", chat_id=record["id"], message=message)
            except Exception as e:  # noqa: BLE001  the error is the reply
                message = {"role": "assistant", "mode": body.mode, "time": time.time(),
                           "error": f"{type(e).__name__}: {e}", "trace": emit.log}
                emit("error", chat_id=record["id"], message=message)
            with chat_lock:
                saved = load_chat(record["id"])
                saved["messages"].append(message)
                save_chat(saved)
            emit.close()

        threading.Thread(target=work, daemon=True).start()

        def events():
            yield {"type": "chat", "chat_id": record["id"], "title": record["title"]}
            yield from emit.events()

        return _sse(events())

    @app.get("/api/chats")
    def chats():
        out = []
        for path in CHATS.glob("*.json"):
            c = json.loads(path.read_text())
            out.append({"id": c["id"], "title": c["title"], "updated": c.get("updated", 0),
                        "messages": len(c["messages"])})
        return sorted(out, key=lambda c: -c["updated"])

    @app.get("/api/chats/{chat_id}")
    def get_chat(chat_id: str):
        return load_chat(chat_id)

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(chat_id: str):
        load_chat(chat_id)
        (CHATS / f"{chat_id}.json").unlink()
        return {"ok": True}

    # --- sources and coverage -------------------------------------------------------------

    @app.get("/api/sources/{source_id}")
    def source(source_id: str):
        from tenk_agent.store import Store

        store = Store(engine.db_path())
        chunk = store.get_chunk(source_id)
        if chunk:
            return {"kind": "chunk", "id": chunk.id, "ticker": chunk.ticker,
                    "form": chunk.form, "fiscal_year": chunk.fiscal_year,
                    "fiscal_period": chunk.fiscal_period, "item": chunk.item,
                    "chunk_kind": chunk.kind, "pages": chunk.printed_pages or chunk.pages,
                    "filed": chunk.filed, "units": chunk.units, "text": chunk.text}
        if store.has_facts():
            fact = store.fact(source_id)
            if fact:
                return {"kind": "fact", "id": source_id, "fact": fact}
            from tenk_agent.research.refs import Resolver

            found = Resolver(store).resolve(source_id)  # q:, kpi:, guide:, px:, macro:, cc:
            if found and found.values:
                return {"kind": "data", "id": source_id, "ref_kind": found.ref.kind,
                        "values": found.values, "detail": found.extra}
        return {"kind": "other", "id": source_id,
                "text": "Not a filing chunk or XBRL fact: a model, valuation or scratch-run "
                        "reference, checked against the model book when the answer was made."}

    @app.get("/api/companies")
    def companies():
        return [{"ticker": c.ticker, "name": c.name} for c in COMPANIES]

    # --- reports --------------------------------------------------------------------------

    def versions(ticker: str) -> list[dict]:
        from tenk_agent.asof import AsOf
        from tenk_agent.store import Store

        store = Store(engine.db_path(), AsOf.today())
        store.enable_facts()
        return store.model_versions(ticker)

    @app.get("/api/reports")
    def reports():
        out = []
        for path in sorted(REPORTS.glob("*/*/report.json")):
            r = json.loads(path.read_text())
            out.append({k: r.get(k) for k in ("ticker", "as_of", "version", "rating",
                                              "price_target", "exceptions", "untraced")}
                       | {"seconds": (r.get("timing") or {}).get("seconds"),
                          "modified": path.stat().st_mtime})
        running = [jobs.public(j) for j in jobs.jobs.values()]
        return {"reports": sorted(out, key=lambda r: -r["modified"]), "jobs": running}

    @app.get("/api/reports/{ticker}/{as_of}")
    def report(ticker: str, as_of: str):
        _safe(ticker, as_of)
        folder = REPORTS / ticker / as_of
        if not (folder / "report.json").exists():
            raise HTTPException(404, "no such report")

        def read(name, default=None):
            p = folder / name
            return json.loads(p.read_text()) if p.exists() else default

        note = folder / "note.md"
        return {"summary": read("report.json"), "exceptions": read("exceptions.json", []),
                "revisions": read("revisions.json", []),
                "note": note.read_text() if note.exists() else "",
                "files": sorted(f for f in FILES if (folder / f).exists()),
                "versions": versions(ticker)}

    @app.get("/api/reports/{ticker}/{as_of}/files/{name}")
    def report_file(ticker: str, as_of: str, name: str):
        _safe(ticker, as_of, name)
        path = REPORTS / ticker / as_of / name
        if name not in FILES or not path.exists():
            raise HTTPException(404, "no such file")
        return FileResponse(path, filename=f"{ticker}_{as_of}_{name}")

    @app.post("/api/reports")
    def start_report(body: ReportIn):
        ticker = body.ticker.upper()
        if ticker not in {c.ticker for c in COMPANIES}:
            raise HTTPException(400, f"{ticker} is not in the coverage")
        date.fromisoformat(body.as_of)
        return jobs.public(jobs.start(settings, ticker, body.as_of, body.llm, body.resume))

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str):
        job = jobs.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        return _sse(jobs.follow(job))

    @app.post("/api/reports/approve")
    def approve(body: ApproveIn):
        from tenk_agent.asof import AsOf
        from tenk_agent.store import Store

        ticker = body.version.split(":")[0]
        store = Store(engine.db_path(), AsOf.today())
        store.enable_facts()
        if not any(v["id"] == body.version for v in store.model_versions(ticker)):
            raise HTTPException(404, f"no saved version {body.version}")
        store.set_version_status(body.version, f"reviewed by {body.by} on {date.today()}")
        return {"ok": True, "versions": store.model_versions(ticker)}

    # --- the built UI (ui/dist), when present ---------------------------------------------

    dist = Path(__file__).resolve().parents[3] / "ui" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app
