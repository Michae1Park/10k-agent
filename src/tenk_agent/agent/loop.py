"""V3: the agent loop, as a LangGraph graph. Spec: docs/stages/V3_AGENT.md.

  question ─► model ──tool calls──► tools ─► model ─► … ─► JSON answer (as in V2)
                 └── no tool calls: parse the JSON answer (one "only the JSON" retry)

The model decides every step: which tool, with what arguments, and when to answer. The graph
only makes the first turn a tool call (so no answer comes from memory), turns a repeated call
into an error, and enforces the budget: calls past `max_calls` return an error, and after the
budget (or once the conversation reaches `max_context` tokens) the model gets one turn with
tools off to answer. The answer goes through V2's
finalize, so the claim check is the same, now also over XBRL facts (verify.py).
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from tenk_agent.agent.tools import Toolbox
from tenk_agent.generation.answers import finalize, unparsed
from tenk_agent.generation.generate import corpus_table
from tenk_agent.models import Model, parse_json
from tenk_agent.prompts import PROMPTS

PROMPT = PROMPTS / "agent.md"
ASK_PROMPT = PROMPTS / "ask.md"  # V4 tool set "ask"
RESEARCH_PROMPT = PROMPTS / "research.md"  # V4 tool set "research"
SEC_GUIDANCE = (
    "- xbrl_facts gives a figure as the company tagged it in XBRL; cite its xbrl_id. Prefer "
    "it for standard line items (revenue, net income, R&D), and the 10-K text (search_filings) "
    "for anything else or when the concept isn't found. It covers any SEC registrant.\n"
    "- edgar_filings answers which filings exist and when (dates, accessions); it has no "
    "financial data.\n"
)
ANSWER_NOW = "Tool budget used up. Answer now with the JSON object from what you have."
JSON_ONLY = "Reply again with only the JSON object."
REPEATED = "You already made this exact call; its result is above. Use it, or answer."


@dataclass
class Step:
    """One model turn or tool call, for the trace."""

    kind: str  # "model" or "tool"
    seconds: float
    name: str = ""  # tool name
    arguments: dict | None = None
    result: dict | None = None
    text: str = ""  # model turn: its visible text
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Run:
    answer: dict  # finalize(): answer, claims (with status), table, scope_notice, abstained, …
    steps: list[Step] = field(default_factory=list)
    seconds: float = 0.0
    stop: str = ""  # "answered", "budget", "turns"
    reply: str = ""  # the last model text, unparsed (sub-agents return their own JSON)

    @property
    def tool_calls(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "tool"]


class State(TypedDict):
    messages: list[dict]
    steps: list[Step]
    calls: int  # tool calls made
    turns: int  # model turns
    retried: bool  # the "only the JSON" retry was used
    reply: str  # the last model text
    stop: str


def build_system(toolbox: Toolbox, max_calls: int, prompt=PROMPT) -> str:
    if toolbox.tool_set in ("ask", "research"):
        default = ASK_PROMPT if toolbox.tool_set == "ask" else RESEARCH_PROMPT
        return (
            (default if prompt == PROMPT else prompt).read_text()
            .replace("{as_of}", toolbox.asof.date.isoformat())
            .replace("{max_calls}", str(max_calls))
            .replace("{coverage}", coverage_table(toolbox.store))
        )
    full = toolbox.tool_set == "full"
    return (
        prompt.read_text()
        .replace("{tool_guidance}", SEC_GUIDANCE if full else "")
        .replace("{max_calls}", str(max_calls))
        .replace("{xbrl_ids}", " or xbrl_id values, or a filing's accession_no" if full else "")
        .replace("{corpus}", corpus_table(toolbox.store))
    )


def coverage_table(store) -> str:
    """The Ask prompt's {coverage}: per company, the latest filing of each form visible at the
    store's AsOf, and how far back the corpus goes."""
    lines = ["Coverage (latest period of each form filed before today; earliest period):"]
    by_company: dict[str, list] = {}
    names = {}
    for f in store.filings():
        names[f.ticker] = f.company_name
        by_company.setdefault(f.ticker, []).append(f)
    for ticker, filings in sorted(by_company.items()):
        latest = {}
        for f in sorted(filings, key=lambda f: f.filing_date):
            fy, fp = f.fiscal_year, f.fiscal_period
            latest[f.form] = f"{f'FY{fy}' if fp == 'FY' else f'{fy}{fp}'} (filed {f.filing_date})"
        earliest = min(f.period_end_date for f in filings)
        forms = "; ".join(f"{f} {latest[f]}" for f in ("ER", "10-Q", "10-K") if f in latest)
        lines.append(f"- {ticker} {names[ticker]}: {forms}; from {earliest}")
    return "\n".join(lines)


@dataclass
class Agent:
    model: Model
    toolbox: Toolbox
    max_calls: int = 12
    max_turns: int = 20  # model turns, a guard against loops of failed calls
    max_tokens: int = 4000
    result_chars: int = 16000  # a tool result is cut to this many characters
    max_context: int = 20000  # input tokens past which the next turn must answer (tools off)
    prompt: Path = PROMPT

    def __post_init__(self):
        self.system = build_system(self.toolbox, self.max_calls, self.prompt)
        graph = StateGraph(State)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._tools)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model", self._route, {"tools": "tools", "model": "model", END: END}
        )
        graph.add_edge("tools", "model")
        self.graph = graph.compile()

    def run(self, question: str) -> Run:
        start = time.perf_counter()
        state = self.graph.invoke(
            {
                "messages": [{"role": "user", "content": f"Question: {question}"}],
                "steps": [],
                "calls": 0,
                "turns": 0,
                "retried": False,
                "reply": "",
                "stop": "",
            },
            {"recursion_limit": 2 * self.max_turns + 5},
        )
        raw = parse_answer(state["reply"])
        answer = finalize(raw, self.toolbox.store, self.toolbox.sec, self.toolbox.book)
        return Run(answer, state["steps"], round(time.perf_counter() - start, 3), state["stop"],
                   state["reply"])

    # --- Nodes ---------------------------------------------------------------------------

    def _model(self, state: State) -> dict:
        last_input = next(
            (s.input_tokens for s in reversed(state["steps"]) if s.kind == "model"), 0
        )
        over = (
            state["calls"] >= self.max_calls
            or state["turns"] >= self.max_turns - 1
            or last_input >= self.max_context
        )
        messages = state["messages"]
        if over and messages[-1]["role"] != "user":  # say why the tools are gone
            messages = messages + [{"role": "user", "content": ANSWER_NOW}]
        started = time.perf_counter()
        response = self.model.complete(
            messages,
            tools=self.toolbox.specs,
            system=self.system,
            max_tokens=self.max_tokens,
            tool_choice="none" if over else ("required" if state["turns"] == 0 else None),
        )
        step = Step(
            "model",
            round(time.perf_counter() - started, 3),
            text=response.text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        messages = messages + [response.message()]
        update = {
            "messages": messages,
            "steps": state["steps"] + [step],
            "turns": state["turns"] + 1,
            "reply": response.text,
            "stop": "",
        }
        if response.tool_calls and not over:
            return update
        # An answer turn: if it isn't the answer object, ask once more for only the JSON.
        if parse_answer(response.text).get("format_error"):
            if not state["retried"]:
                retry = {"role": "user", "content": JSON_ONLY}
                return update | {"messages": messages + [retry], "retried": True, "stop": "retry"}
        return update | {"stop": "budget" if over else "answered"}

    def _route(self, state: State) -> str:
        if state["stop"] == "retry":
            return "model"
        last = state["messages"][-1]
        return "tools" if last["role"] == "assistant" and last.get("tool_calls") else END

    def _tools(self, state: State) -> dict:
        messages, steps, calls = list(state["messages"]), list(state["steps"]), state["calls"]
        made = {_key(s.name, s.arguments) for s in steps if s.kind == "tool"}
        for call in messages[-1]["tool_calls"]:
            started = time.perf_counter()
            if call.error:
                result = {"error": call.error}
            elif calls >= self.max_calls:
                result = {"error": ANSWER_NOW}
            elif _key(call.name, call.arguments) in made:
                result = {"error": REPEATED}
            else:
                result = self.toolbox.call(call.name, call.arguments)
            calls += 1
            made.add(_key(call.name, call.arguments))
            steps.append(
                Step(
                    "tool",
                    round(time.perf_counter() - started, 3),
                    call.name,
                    call.arguments,
                    result,
                )
            )
            content = json.dumps(result, ensure_ascii=False)
            if len(content) > self.result_chars:
                content = content[: self.result_chars] + " …(cut; narrow the call)"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
        return {"messages": messages, "steps": steps, "calls": calls}


def parse_answer(text: str) -> dict:
    """The answer object in a reply, or unparsed() when there is none: no JSON, a JSON value
    without an "answer" (a bare claims list), or tool-call markup written as text (which
    holds JSON arguments)."""
    if "<tool_call>" not in text:
        try:
            raw = parse_json(text)
            if isinstance(raw, dict) and "answer" in raw:
                return raw
        except ValueError:
            pass
    return unparsed(text)


def _key(name: str, arguments: dict | None) -> str:
    return json.dumps([name, arguments], sort_keys=True)


def agent_from_settings(
    settings: dict,
    tool_set: str | None = None,
    max_calls: int | None = None,
    live: bool = False,
    model: str | None = None,
) -> Agent:
    """The agent as the config sets it: `generation:` for the model, `agent:` for the rest."""
    from tenk_agent.agent.tools import toolbox_from_settings
    from tenk_agent.models import get_model

    generation, config = settings["generation"], settings["agent"]
    toolbox = toolbox_from_settings(settings, tool_set or config["tools"], live=live)
    return Agent(
        get_model(
            model or generation["model"],
            base_url=generation["base_url"],
            thinking=generation["thinking"],
        ),
        toolbox,
        max_calls=max_calls or config["max_calls"],
        max_turns=config["max_turns"],
        max_tokens=generation["max_tokens"],
        result_chars=config["result_chars"],
        max_context=config["max_context"],
    )
