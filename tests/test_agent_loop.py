"""The V3 agent loop with a scripted model: tool routing, the budget, the JSON retry."""

import json

from tenk_agent.agent.loop import ANSWER_NOW, Agent
from tenk_agent.agent.tools import Toolbox
from tenk_agent.models import Response, ToolCall
from test_agent_tools import FACT, make_sec, make_store

ANSWER = {
    "answer": "Apple's R&D was $34,550 million in fiscal 2025 [1].",
    "claims": [
        {
            "id": 1,
            "value": 34550,
            "unit": "USD millions",
            "company": "AAPL",
            "fiscal_year": 2025,
            "chunk_ids": [FACT],
        }
    ],
    "abstained": False,
}
XBRL = {"company": "AAPL", "concept": "ResearchAndDevelopmentExpense", "fiscal_year": 2025}


class Scripted:
    """Replies in order: a list of tool calls, or text."""

    name = "scripted"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, tools=None, system=None, max_tokens=0, tool_choice=None):
        self.seen.append((list(messages), tool_choice))
        reply = self.replies.pop(0)
        calls = (
            [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(reply)]
            if isinstance(reply, list)
            else []
        )
        return Response(reply if isinstance(reply, str) else "", calls, 10, 5, 0.0, "s", "stop")


def agent(tmp_path, replies, **kwargs) -> tuple[Agent, Scripted]:
    model = Scripted(replies)
    toolbox = Toolbox(make_store(tmp_path), None, make_sec(tmp_path))
    return Agent(model, toolbox, **kwargs), model


def test_tool_results_feed_the_next_turn_and_the_answer_is_verified(tmp_path):
    run_agent, model = agent(tmp_path, [[("xbrl_facts", XBRL)], json.dumps(ANSWER)])
    run = run_agent.run("What was Apple's R&D in fiscal 2025?")
    assert run.stop == "answered" and [s.name for s in run.tool_calls] == ["xbrl_facts"]
    tool_message = model.seen[1][0][-1]
    assert tool_message["role"] == "tool" and FACT in tool_message["content"]
    assert run.answer["claims"][0]["status"] == "verified"
    assert model.seen[0][1] == "required"  # the first turn must call a tool


def test_calls_past_the_budget_error_and_the_model_must_answer(tmp_path):
    calls = [("calculator", {"op": "sum", "inputs": {"a": i}}) for i in range(3)]
    run_agent, model = agent(tmp_path, [calls, json.dumps(ANSWER)], max_calls=2)
    run = run_agent.run("q")
    assert [s.result.get("error") for s in run.tool_calls] == [None, None, ANSWER_NOW]
    assert model.seen[-1][1] == "none" and run.stop == "budget"


def test_a_reply_that_is_not_json_gets_one_retry(tmp_path):
    run_agent, model = agent(tmp_path, ["Let me think…", json.dumps(ANSWER)])
    assert run_agent.run("q").answer["claims"]
    assert model.seen[1][0][-1]["content"] == "Reply again with only the JSON object."
    run_agent, _ = agent(tmp_path / "b", ["no", "still no"])
    assert run_agent.run("q").answer["format_error"]


def test_text_tool_set_prompt_does_not_mention_sec_tools(tmp_path):
    run_agent, _ = agent(tmp_path, [])
    assert "xbrl_facts" in run_agent.system and "{" not in run_agent.system.split("Corpus")[0]
    run_agent.toolbox.tool_set = "text"
    run_agent.__post_init__()
    assert "xbrl" not in run_agent.system


def test_a_repeated_call_returns_an_error_and_still_counts(tmp_path):
    call = ("calculator", {"op": "sum", "inputs": {"a": 1}})
    run_agent, _ = agent(tmp_path, [[call], [call], json.dumps(ANSWER)])
    run = run_agent.run("q")
    assert run.tool_calls[0].result["result"] == 1
    assert "already made" in run.tool_calls[1].result["error"]
