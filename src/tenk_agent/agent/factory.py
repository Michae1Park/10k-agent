"""Ask and Research agents at any as-of, sharing one model book per as-of (design doc §5.3).

Used by `tenk investigate` and the gold runners (experiments/v4/research_run.py,
investigate_run.py).
"""

import threading
from dataclasses import replace
from pathlib import Path

from tenk_agent.agent.loop import Agent
from tenk_agent.agent.tools import Toolbox
from tenk_agent.asof import AsOf
from tenk_agent.models import get_model
from tenk_agent.research.book import Book
from tenk_agent.retrieval.search import SubQuestionRetriever
from tenk_agent.store import Store


def agent_factory(db: Path, retrieval: dict, generation: dict, agent_config: dict):
    """A function (as_of, tool_set, session) -> a fresh agent. Search runs on the CPU (vLLM
    holds the GPU)."""
    base = SubQuestionRetriever.from_settings(Store(db), retrieval | {"device": "cpu"})
    model = get_model(generation["model"], base_url=generation["base_url"], thinking=False)
    lock, search_lock = threading.Lock(), threading.Lock()
    books: dict[str, Book] = {}

    def agent(as_of: str, tool_set: str = "research", session: Book | None = None) -> Agent:
        """A fresh agent per question (its own scratch runs), sharing the as-of's book of base
        models and valuations. session: an existing session's book, so several agents (an
        Investigate run's sub-runs) share their scratch runs."""
        store = Store(db, AsOf.of(as_of))
        with lock:
            book = books.setdefault(as_of, Book(store))
        # shared base forecasts; own scratch runs and valuations (scratch-n is per session)
        session = session or Book(store, book.settings, book.forecasts, {}, {}, book.earlier)
        search = replace(base, decomposer=replace(base.decomposer, store=store),
                         hybrid=replace(base.hybrid, store=store))
        toolbox = Toolbox(store, search, None, tool_set, session)
        call = toolbox.call

        def locked(name, arguments):
            if name == "search_filings":
                with search_lock:
                    return call(name, arguments)
            with lock:  # model tools share the book's caches
                return call(name, arguments)

        toolbox.call = locked
        return Agent(model, toolbox, max_calls=agent_config["max_calls"],
                     max_turns=agent_config["max_turns"], max_tokens=generation["max_tokens"],
                     result_chars=agent_config["result_chars"],
                     max_context=agent_config["max_context"])

    return agent
