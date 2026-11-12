"""Telemetry: every run, LLM call and tool call, with full payloads, in one SQLite file.

The data is for improving the agent later (failure analysis, eval and fine-tuning sets), so
nothing is cut. Writes never raise: a telemetry failure must not break a run.

    runs       one chat turn or report run: input, output, status, error, timings
    llm_calls  one `Model.complete`: messages, tools, system, reply, tokens, latency, cost, error
    tool_calls one `Toolbox.call`: name, arguments, result, error, duration

LLM and tool calls made inside `run(...)` carry its id (and the sub-agent `scope`, if any);
calls outside a run (CLI, evaluation) are logged with run_id NULL.

    ERA_TELEMETRY=0          turn it off
    ERA_TELEMETRY_DB=<path>  default data/telemetry.db
"""

import contextvars
import dataclasses
import functools
import json
import os
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, kind TEXT, mode TEXT, chat_id TEXT, input TEXT, output TEXT,
    status TEXT, error TEXT, started REAL, ended REAL, seconds REAL, meta TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id TEXT PRIMARY KEY, run_id TEXT, scope TEXT, model TEXT, system TEXT, messages TEXT,
    tools TEXT, tool_choice TEXT, max_tokens INTEGER, response TEXT, tool_calls TEXT,
    stop_reason TEXT, input_tokens INTEGER, output_tokens INTEGER, latency_s REAL,
    cost_usd REAL, error TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY, run_id TEXT, scope TEXT, name TEXT, arguments TEXT, result TEXT,
    is_error INTEGER, error TEXT, seconds REAL, ts REAL
);
CREATE INDEX IF NOT EXISTS llm_run ON llm_calls(run_id);
CREATE INDEX IF NOT EXISTS tool_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS tool_err ON tool_calls(name, is_error);
"""

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("era_run", default=None)
_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar("era_scope", default=None)
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def enabled() -> bool:
    return os.environ.get("ERA_TELEMETRY", "1") not in ("0", "false", "off")


def db_path() -> Path:
    return Path(os.environ.get("ERA_TELEMETRY_DB", "data/telemetry.db"))


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def _json(value) -> str | None:
    if value is None:
        return None

    def default(o):
        if hasattr(o, "to_dict"):
            return o.to_dict()
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return str(o)

    return json.dumps(value, ensure_ascii=False, default=default)


def _write(sql: str, params: tuple) -> None:
    if not enabled():
        return
    try:
        with _lock:
            _db().execute(sql, params)
    except Exception:  # noqa: BLE001  telemetry never breaks a run
        traceback.print_exc()


def _error(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}\n{''.join(traceback.format_tb(e.__traceback__))}"


# --- Runs ----------------------------------------------------------------------------------


class Run:
    def __init__(self, run_id: str):
        self.id = run_id
        self.output = None
        self.status = "ok"

    def finish(self, output=None, status: str = "ok") -> None:
        self.output, self.status = output, status


@contextmanager
def run(kind: str, input=None, mode: str | None = None, chat_id: str | None = None, **meta):
    """One chat turn or report run; LLM and tool calls inside it are linked to it."""
    r = Run(uuid.uuid4().hex)
    started = time.time()
    _write("INSERT INTO runs (id, kind, mode, chat_id, input, status, started, meta) "
           "VALUES (?,?,?,?,?,?,?,?)",
           (r.id, kind, mode, chat_id, _json(input), "running", started, _json(meta or None)))
    token = _run_id.set(r.id)
    error = None
    try:
        yield r
    except BaseException as e:
        r.status = "cancelled" if type(e).__name__ == "Cancelled" else "failed"
        error = _error(e)
        raise
    finally:
        _run_id.reset(token)
        ended = time.time()
        _write("UPDATE runs SET output=?, status=?, error=?, ended=?, seconds=? WHERE id=?",
               (_json(r.output), r.status, error, ended, round(ended - started, 3), r.id))


@contextmanager
def scope(name: str | None):
    """A sub-agent within a run (Investigate's sub-questions)."""
    token = _scope.set(name)
    try:
        yield
    finally:
        _scope.reset(token)


# --- Calls ---------------------------------------------------------------------------------


def logged_complete(complete):
    """Decorates a Model.complete: logs the request, the reply or the error."""

    @functools.wraps(complete)
    def wrapper(self, messages, tools=None, system=None, max_tokens=8000, tool_choice=None):
        started = time.monotonic()
        response, error = None, None
        try:
            response = complete(self, messages, tools, system, max_tokens, tool_choice)
            return response
        except BaseException as e:
            error = _error(e)
            raise
        finally:
            r = response
            _write(
                "INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, _run_id.get(), _scope.get(), getattr(self, "name", None),
                 system, _json(messages), _json(tools), tool_choice, max_tokens,
                 r.text if r else None, _json(r.tool_calls) if r else None,
                 r.stop_reason if r else None, r.input_tokens if r else None,
                 r.output_tokens if r else None,
                 r.latency_s if r else round(time.monotonic() - started, 3),
                 r.cost_usd if r else None, error, time.time()),
            )

    return wrapper


def logged_tool(call):
    """Decorates a Toolbox.call: logs arguments, result, and errors (returned or raised)."""

    @functools.wraps(call)
    def wrapper(self, name, arguments):
        started = time.monotonic()
        result, error = None, None
        try:
            result = call(self, name, arguments)
            return result
        except BaseException as e:
            error = _error(e)
            raise
        finally:
            returned = isinstance(result, dict) and "error" in result
            _write(
                "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, _run_id.get(), _scope.get(), name, _json(arguments),
                 _json(result), int(bool(error) or returned),
                 error or (str(result["error"]) if returned else None),
                 round(time.monotonic() - started, 3), time.time()),
            )

    return wrapper
