"""Report mode's research sub-agents (design doc §9): S2 events and S9 debates.

Each is a bounded Agent (the Ask tools, its own prompt and research.yaml budget) that returns
JSON; an item is kept only if at least one evidence id resolves at the as-of. A debate's
evidence values (resolved numbers) become note facts, so its numbers can be traced.
"""

import re

from tenk_agent.agent.loop import Agent
from tenk_agent.agent.tools import Toolbox
from tenk_agent.models import parse_json
from tenk_agent.prompts import PROMPTS
from tenk_agent.research.refs import Resolver
from tenk_agent.store import Store


def _agent(llm, store: Store, search, prompt: str, budget: dict) -> Agent:
    return Agent(llm, Toolbox(store, search, None, "ask"), max_calls=budget["max_calls"],
                 max_tokens=budget["max_tokens"], max_context=budget["max_context"],
                 prompt=PROMPTS / prompt)


def events(llm, store: Store, search, ticker: str, period: str, budget: dict) -> list[dict]:
    run = _agent(llm, store, search, "events.md", budget).run(
        f"What changed for {ticker} in {period}, with its latest release and filings?")
    try:
        found = parse_json(run.reply).get("events") or []
    except (ValueError, AttributeError):
        return []
    resolver = Resolver(store)
    out = []
    for e in found:
        if not isinstance(e, dict):
            continue
        ids = [str(i) for i in e.get("evidence") or [] if resolver.resolve(str(i))]
        if ids:
            out.append({"type": str(e.get("type", "other")), "summary": str(e.get("summary", "")),
                        "evidence": ids})
    return out


def debate(llm, store: Store, search, ticker: str, driver: str, value: float,
           budget: dict) -> dict | None:
    line = re.sub(r"\.(g0|shift0)$", "", driver)
    run = _agent(llm, store, search, "debates.md", budget).run(
        f"Frame the debate on {ticker}'s {line.replace('_', ' ')}: our model assumes "
        f"{driver} = {value:.4f} for the first forecast quarter.")
    try:
        d = parse_json(run.reply).get("debate")
    except (ValueError, AttributeError):
        return None
    if not isinstance(d, dict):
        return None
    resolver = Resolver(store)
    values, ids = [], []
    for i in d.get("evidence") or []:
        r = resolver.resolve(str(i))
        if r is None:
            continue
        ids.append(str(i))
        for v in r.values:
            values.append({"id": str(i), "value": v["value"], "unit": v["unit"] or "number"})
    if not ids:
        return None
    return {k: str(d.get(k, "")) for k in ("question", "bull", "bear", "hypothesis", "trigger",
                                           "gap")} | {"driver": driver, "evidence": ids,
                                                      "evidence_values": values}
