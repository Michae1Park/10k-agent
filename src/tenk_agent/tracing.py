"""Request tracing: every model call, retrieval and tool call with inputs, outputs, tokens,
latency and cost.

Traces are saved in the SQLite store (feeding the UI's trace view and the eval harness's
agent and cost metrics) and, when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set, also
exported to Langfuse through its public ingestion API.
"""

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from tenk_agent.models import Response
from tenk_agent.store import Store

log = logging.getLogger(__name__)
MAX_LOGGED_CHARS = 20_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Trace:
    mode: str
    question: str
    model: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_now)
    spans: list[dict] = field(default_factory=list)
    output: dict | None = None
    error: str | None = None
    _start: float = field(default_factory=time.monotonic)

    def llm(self, name: str, response: Response, request_messages: int) -> None:
        self.spans.append(
            {
                "type": "llm",
                "name": name,
                "model": response.model,
                "input": {"messages": request_messages},
                "output": {
                    "text": response.text[:MAX_LOGGED_CHARS],
                    "tool_calls": [c.to_dict() for c in response.tool_calls],
                    "stop_reason": response.stop_reason,
                },
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_s": round(response.latency_s, 3),
                "cost_usd": response.cost_usd,
                "at": _now(),
            }
        )

    def tool(
        self, name: str, arguments: dict, result: str, is_error: bool, latency_s: float
    ) -> None:
        self.spans.append(
            {
                "type": "tool",
                "name": name,
                "input": arguments,
                "output": result[:MAX_LOGGED_CHARS],
                "is_error": is_error,
                "latency_s": round(latency_s, 3),
                "at": _now(),
            }
        )

    def retrieval(self, query: str, chunk_ids: list[str], latency_s: float) -> None:
        self.spans.append(
            {
                "type": "retrieval",
                "name": "retrieve",
                "input": {"query": query},
                "output": chunk_ids,
                "latency_s": round(latency_s, 3),
                "at": _now(),
            }
        )

    def totals(self) -> dict:
        llm = [s for s in self.spans if s["type"] == "llm"]
        return {
            "latency_s": round(time.monotonic() - self._start, 3),
            "input_tokens": sum(s["input_tokens"] for s in llm),
            "output_tokens": sum(s["output_tokens"] for s in llm),
            "cost_usd": round(sum(s["cost_usd"] for s in llm), 6),
            "llm_calls": len(llm),
            "tool_calls": sum(s["type"] == "tool" for s in self.spans),
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "mode": self.mode,
            "question": self.question,
            "model": self.model,
            "spans": self.spans,
            "totals": self.totals(),
            "output": self.output,
            "error": self.error,
        }

    def save(self, store: Store) -> dict:
        record = self.to_dict()
        store.save_trace(record)
        _export_langfuse(record)
        return record


def _export_langfuse(trace: dict) -> None:
    public, secret = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if not (public and secret):
        return
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    events = [
        {
            "id": uuid.uuid4().hex,
            "timestamp": trace["created_at"],
            "type": "trace-create",
            "body": {
                "id": trace["id"],
                "name": trace["mode"],
                "input": trace["question"],
                "output": trace["output"],
                "metadata": {"model": trace["model"], **trace["totals"]},
            },
        }
    ]
    for span in trace["spans"]:
        body = {
            "id": uuid.uuid4().hex,
            "traceId": trace["id"],
            "name": span["name"],
            "input": span["input"],
            "output": span["output"],
            "startTime": span["at"],
            "metadata": {"latency_s": span["latency_s"]},
        }
        if span["type"] == "llm":
            body |= {
                "model": span["model"],
                "usage": {"input": span["input_tokens"], "output": span["output_tokens"]},
            }
        events.append(
            {
                "id": uuid.uuid4().hex,
                "timestamp": span["at"],
                "type": "generation-create" if span["type"] == "llm" else "span-create",
                "body": body,
            }
        )
    try:
        httpx.post(
            f"{host}/api/public/ingestion",
            json={"batch": events},
            auth=(public, secret),
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError as e:  # tracing must never break a request
        log.warning("Langfuse export failed: %s", e)
