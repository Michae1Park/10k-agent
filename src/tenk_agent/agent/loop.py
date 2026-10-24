"""V3: the agent loop, as a LangGraph graph. Spec: docs/stages/V3_AGENT.md.

  question ─► model ──tool calls──► tools ─► model ─► … ─► JSON answer (as in V2)
                 └── no tool calls: parse the JSON answer (one "only the JSON" retry)

The model decides every step: which tool, with what arguments, and when to answer. The graph
only enforces the budget: calls past `max_calls` return an error, and after the budget the
model gets one turn with tools off to answer. The answer goes through V2's finalize, so the
claim check is the same, now also over XBRL facts (verify.py).
"""

import json
import time
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from tenk_agent.agent.tools import Toolbox
from tenk_agent.generation.answers import finalize, unparsed
from tenk_agent.generation.generate import corpus_table
from tenk_agent.models import Model, parse_json
from tenk_agent.prompts import PROMPTS

PROMPT = PROMPTS / "agent.md"
SEC_GUIDANCE = (
    "- xbrl_facts gives a figure as the company tagged it in XBRL; cite its xbrl_id. Prefer "
    "it for standard line items (revenue, net income, R&D), and the 10-K text (search_filings) "
    "for anything else or when the concept isn't found. It covers any SEC registrant.\n"
    "- edgar_filings answers which filings exist and when (dates, accessions); it has no "
    "financial data.\n"
)
ANSWER_NOW = "Tool budget used up. Answer now with the JSON object from what you have."
JSON_ONLY = "Reply again with only the JSON object."


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
    full = toolbox.tool_set == "full"
    return (
        prompt.read_text()
        .replace("{tool_guidance}", SEC_GUIDANCE if full else "")
        .replace("{max_calls}", str(max_calls))
        .replace("{xbrl_ids}", " or xbrl_id values" if full else "")
        .replace("{corpus}", corpus_table(toolbox.store))
    )


@dataclass
class Agent:
    model: Model
    toolbox: Toolbox
    max_calls: int = 12
    max_turns: int = 20  # model turns, a guard against loops of failed calls
    max_tokens: int = 4000
    result_chars: int = 16000  # a tool result is cut to this many characters

    def __post_init__(self):
        self.system = build_system(self.toolbox, self.max_calls)
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
        try:
            raw = parse_json(state["reply"])
        except ValueError:
            raw = unparsed(state["reply"])
        answer = finalize(raw, self.toolbox.store, self.toolbox.sec)
        return Run(answer, state["steps"], round(time.perf_counter() - start, 3), state["stop"])

    # --- Nodes ---------------------------------------------------------------------------

    def _model(self, state: State) -> dict:
        over = state["calls"] >= self.max_calls or state["turns"] >= self.max_turns - 1
        started = time.perf_counter()
        response = self.model.complete(
            state["messages"],
            tools=self.toolbox.specs,
            system=self.system,
            max_tokens=self.max_tokens,
            tool_choice="none" if over else None,
        )
        step = Step(
            "model",
            round(time.perf_counter() - started, 3),
            text=response.text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        messages = state["messages"] + [response.message()]
        update = {
            "messages": messages,
            "steps": state["steps"] + [step],
            "turns": state["turns"] + 1,
            "reply": response.text,
            "stop": "",
        }
        if response.tool_calls and not over:
            return update
        # An answer turn: if it isn't JSON, ask once more for only the JSON.
        try:
            parse_json(response.text)
        except ValueError:
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
        for call in messages[-1]["tool_calls"]:
            started = time.perf_counter()
            if call.error:
                result = {"error": call.error}
            elif calls >= self.max_calls:
                result = {"error": ANSWER_NOW}
            else:
                result = self.toolbox.call(call.name, call.arguments)
            calls += 1
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
    )
