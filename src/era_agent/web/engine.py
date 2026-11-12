"""The V4 modes behind the web UI, run one at a time (one GPU), each streaming its trace.

Ask, Research and Investigate are chat modes: agents from the shared factory (as `era
investigate` builds them). Report is the fixed S1–S11 pipeline (`era research report`). The
core code is not changed: the trace comes from wrapping the model's `complete`, each agent's
tool calls and runs, and the pipeline's Clock.step, for the length of one run.
"""

import json
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from era_agent.corpus import DATA_DIR

RESULT_CHARS = 4000  # a tool result in the trace is cut to this many characters

_gpu = threading.Lock()  # one run at a time: chat and report share the model server
_factory = None
_factory_lock = threading.Lock()


def db_path() -> Path:
    if not DATA_DIR:
        raise RuntimeError("the web UI needs the V4 corpus: ERA_COMPANIES=companies.yaml")
    return Path(DATA_DIR) / "build" / "parent_child.db"


def factory(settings: dict):
    """The agent factory, built once (it loads the embedder and reranker)."""
    global _factory
    with _factory_lock:
        if _factory is None:
            from era_agent.agent.factory import agent_factory

            _factory = agent_factory(db_path(), settings["retrieval"], settings["generation"],
                                     settings["agent"])
        return _factory


def _cut(value) -> object:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + " …(cut)"


class Cancelled(BaseException):
    """The user stopped the run. BaseException: the agents' `except Exception` must not catch it."""


class Emitter:
    """Events from a worker thread, kept so SSE responses can follow and re-attach."""

    def __init__(self):
        self.events_: list[dict] = []
        self.log: list[dict] = []  # kept with the chat message, to replay the trace
        self.started = time.perf_counter()
        self.closed = False
        self.cancel = threading.Event()
        self._cond = threading.Condition()

    def elapsed(self) -> float:
        return round(time.perf_counter() - self.started, 2)

    def __call__(self, kind: str, **data) -> None:
        event = {"type": kind, "t": self.elapsed()} | data
        if kind in ("model", "tool", "sub_start", "sub_end"):
            self.log.append(event)
        with self._cond:
            self.events_.append(event)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled

    def events(self):
        """Every event from the start, then new ones until the run ends."""
        i = 0
        while True:
            with self._cond:
                while i >= len(self.events_) and not self.closed:
                    self._cond.wait(timeout=15)
                    if i >= len(self.events_) and not self.closed:
                        break  # no event for 15 s: send a keep-alive
                batch, done = self.events_[i:], self.closed
            i += len(batch)
            yield from batch
            if not batch and not done:
                yield {"type": "ping"}
            if done and i >= len(self.events_):
                return


def _trace_model(model, emit, scope: dict):
    """Wrap model.complete: each turn's text and tool calls become events."""
    original = model.complete

    def complete(*args, **kwargs):
        emit.check()
        emit("model_start", scope=scope.get("id"), tools=bool(kwargs.get("tools")))
        started = time.perf_counter()
        response = original(*args, **kwargs)
        emit("model", scope=scope.get("id"), text=response.text[:6000],
             tool_calls=[c.name for c in response.tool_calls or []],
             seconds=round(time.perf_counter() - started, 2),
             input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        return response

    model.complete = complete

    def restore():
        model.complete = original

    return restore


def _trace_tools(agent, emit, scope: dict):
    call = agent.toolbox.call

    def traced(name, arguments):
        emit.check()
        emit("tool_start", scope=scope.get("id"), name=name, arguments=arguments)
        started = time.perf_counter()
        result = call(name, arguments)
        emit("tool", scope=scope.get("id"), name=name, arguments=arguments,
             result=_cut(result), seconds=round(time.perf_counter() - started, 2))
        return result

    agent.toolbox.call = traced


def chat(settings: dict, mode: str, question: str, as_of: str, emit: Emitter) -> dict:
    """One chat message in its mode → {"mode", "answer", "steps" | "investigation"}."""
    if not _gpu.acquire(blocking=False):
        emit("status", text="waiting for the running job to finish")
        while not _gpu.acquire(timeout=1):
            emit.check()
    try:
        emit.check()
        emit("status", text="loading models" if _factory is None else "starting")
        agent = factory(settings)
        scope: dict = {}
        if mode in ("ask", "research"):
            run_agent = agent(as_of, mode)
            _trace_tools(run_agent, emit, scope)
            restore = _trace_model(run_agent.model, emit, scope)
            try:
                run = run_agent.run(question)
            finally:
                restore()
            return {"mode": mode, "answer": run.answer, "stop": run.stop,
                    "seconds": run.seconds, "steps": [asdict(s) for s in run.steps]}
        if mode == "investigate":
            from era_agent.agent.plan import Investigator

            research = agent(as_of)
            ask = agent(as_of, "ask", research.toolbox.book)
            agents = {"ask": ask, "research": research}
            for a in agents.values():
                _trace_tools(a, emit, scope)
                original = a.run

                def run_sub(q, original=original, a=a):
                    scope["id"] = f"sub-{uuid.uuid4().hex[:6]}"
                    emit("sub_start", scope=scope["id"], question=q,
                         mode=a.toolbox.tool_set)
                    try:
                        r = original(q)
                    except Exception as e:
                        emit("sub_end", scope=scope["id"], error=f"{type(e).__name__}: {e}")
                        raise
                    emit("sub_end", scope=scope["id"], stop=r.stop, answer=r.answer)
                    scope.pop("id", None)
                    return r

                a.run = run_sub
            restore = _trace_model(research.model, emit, scope)
            try:
                inv = Investigator(research.model, agents).run(question)
            finally:
                restore()
            data = json.loads(json.dumps(inv.to_dict(), default=str))
            return {"mode": mode, "answer": inv.answer, "stop": inv.stop,
                    "seconds": inv.seconds, "investigation": data}
        raise ValueError(f"unknown mode {mode!r}")
    finally:
        _gpu.release()


# --- Report mode -------------------------------------------------------------------------------

STAGES = ["S2 events", "S3-S5 model and forecast", "S6-S7 peers and valuation", "S8 revisions",
          "S9 debates", "S7 sensitivities", "S10 write", "S11 export"]


class Jobs:
    """Report runs in background threads; their events are kept so a page can re-attach."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, settings: dict, ticker: str, as_of: str, llm: bool, resume: bool) -> dict:
        job = {"id": uuid.uuid4().hex[:10], "ticker": ticker, "as_of": as_of, "llm": llm,
               "status": "queued", "events": [], "result": None, "error": None,
               "created": time.time(), "cond": threading.Condition()}
        with self.lock:
            self.jobs[job["id"]] = job
        threading.Thread(target=self._run, args=(job, settings, resume), daemon=True).start()
        return job

    def _emit(self, job: dict, kind: str, status: str | None = None, **data) -> None:
        with job["cond"]:
            job["status"] = status or job["status"]
            job["events"].append({"type": kind, "t": time.time()} | data)
            job["cond"].notify_all()

    def _run(self, job: dict, settings: dict, resume: bool) -> None:
        from era_agent.research import pipeline
        from era_agent.research.forecast import knobs

        self._emit(job, "status", text="waiting for the model server")
        with _gpu:
            self._emit(job, "status", status="running", text="started")
            step = pipeline.Clock.step

            def traced(clock, name):
                step(clock, name)
                if name:
                    self._emit(job, "stage", name=name)

            pipeline.Clock.step = traced
            try:
                search = None
                if job["llm"]:
                    import yaml

                    from era_agent.asof import AsOf
                    from era_agent.retrieval.search import SubQuestionRetriever
                    from era_agent.store import Store

                    retrieval = yaml.safe_load(
                        Path("experiments/v1/config.yaml").read_text())["retrieval"]
                    search = SubQuestionRetriever.from_settings(
                        Store(db_path(), AsOf.today()), retrieval | {"device": "cpu"})
                result = pipeline.run(db_path(), job["ticker"], job["as_of"], knobs(),
                                      job["llm"], resume, search)
                job["result"] = result
                self._emit(job, "done", status="done", result=result)
            except Exception as e:  # noqa: BLE001  shown to the user, not swallowed
                job["error"] = f"{type(e).__name__}: {e}"
                self._emit(job, "error", status="failed", text=job["error"])
            finally:
                pipeline.Clock.step = step

    def public(self, job: dict) -> dict:
        return {k: job[k] for k in ("id", "ticker", "as_of", "llm", "status", "result",
                                    "error", "created")}

    def follow(self, job: dict):
        """Every event so far, then each new one, until the job ends."""
        i = 0
        while True:
            with job["cond"]:
                if i >= len(job["events"]) and job["status"] not in ("done", "failed"):
                    job["cond"].wait(timeout=15)
                new, i = job["events"][i:], len(job["events"])
                ended = job["status"] in ("done", "failed")
            yield from new or [{"type": "ping"}]
            if ended:
                return
