"""HTTP API for the UI: Ask, Research (streamed over SSE), traces and chunks.

uv run tenk serve        # http://127.0.0.1:8000
"""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tenk_agent.agent import research
from tenk_agent.ask import ask
from tenk_agent.config import Settings
from tenk_agent.models import load_model
from tenk_agent.retrieval import Retriever
from tenk_agent.store import Store
from tenk_agent.tools import Toolbox

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.load()
    store = Store(settings.db_path)
    retriever = Retriever.from_settings(store, settings)
    state.update(
        settings=settings,
        store=store,
        retriever=retriever,
        toolbox=Toolbox.from_settings(store, retriever, settings),
    )
    yield
    state.clear()


app = FastAPI(title="10k-agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    model: str | None = None


def _model(spec: str | None):
    return load_model(state["settings"], spec)


@app.get("/api/health")
def health() -> dict:
    settings: Settings = state["settings"]
    return {
        "ok": True,
        "model": settings.model,
        "retriever": state["retriever"].name,
        "chunks_embedded": state["store"].embedding_count(),
    }


@app.get("/api/corpus")
def corpus() -> list[dict]:
    return [f.__dict__ for f in state["store"].filings()]


@app.post("/api/ask")
def ask_endpoint(body: Question) -> dict:
    settings: Settings = state["settings"]
    return ask(
        body.question,
        state["store"],
        state["retriever"],
        _model(body.model),
        verify=settings.verify,
        k=settings.ask_k,
    )


@app.post("/api/research")
def research_endpoint(body: Question) -> StreamingResponse:
    settings: Settings = state["settings"]
    events = research(
        body.question,
        state["store"],
        state["toolbox"],
        _model(body.model),
        settings.max_tool_calls,
        verify=settings.verify,
        repair=settings.repair,
        repair_calls=settings.repair_calls,
    )

    def stream():
        for event in events:
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/traces")
def traces(limit: int = 50) -> list[dict]:
    return state["store"].recent_traces(limit)


@app.get("/api/traces/{trace_id}")
def trace(trace_id: str) -> dict:
    record = state["store"].get_trace(trace_id)
    if record is None:
        raise HTTPException(404, f"no trace {trace_id}")
    return record


@app.get("/api/chunks/{chunk_id}")
def chunk(chunk_id: str) -> dict:
    store: Store = state["store"]
    record = store.get_chunk(chunk_id)
    if record is None:
        raise HTTPException(404, f"no chunk {chunk_id}")
    filing = store.filing(record.ticker, record.fiscal_year)
    return record.__dict__ | {
        "company_name": filing.company_name if filing else record.ticker,
        "period_end_date": filing.period_end_date if filing else None,
        "url": filing.source_url if filing else None,
        "section_title": store.section_titles(record.ticker, record.fiscal_year).get(record.item),
    }
