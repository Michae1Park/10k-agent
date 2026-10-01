import json

from tenk_agent.agent import research, run_research
from tenk_agent.answers import finalize
from tenk_agent.ask import ask
from tenk_agent.evaluation.runner import summarize
from tenk_agent.evaluation.scoring import agent_metrics, citation_accuracy, score_numbers
from tenk_agent.models import Response, ToolCall, _anthropic_messages, parse_json
from tenk_agent.retrieval import Retriever
from tenk_agent.tools import Toolbox
from test_store_and_gold import make_store

TABLE_CHUNK = "AAPL-FY2025-8-001"


class ScriptedModel:
    """Replays canned responses; records what it was sent."""

    name = "test:scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools=None, system=None, max_tokens=8000):
        self.requests.append({"messages": list(messages), "tools": tools, "system": system})
        text, calls = self.responses.pop(0)
        return Response(text, calls, 100, 20, 0.01, self.name, "end_turn")


def final(claims, **extra) -> str:
    return json.dumps(
        {"answer": "Apple spent $34,550 million on R&D [1].", "claims": claims} | extra
    )


RD_CLAIM = {
    "id": 1,
    "text": "Apple's FY2025 R&D expense was $34,550 million.",
    "value": 34550,
    "unit": "USD millions",
    "company": "AAPL",
    "fiscal_year": 2025,
    "chunk_ids": [TABLE_CHUNK],
}


def toolbox(tmp_path):
    store = make_store(tmp_path)
    return store, Toolbox(store, Retriever(store, mode="keyword", slot_search=False))


def test_agent_runs_tools_streams_steps_and_verifies_claims(tmp_path):
    store, tools = toolbox(tmp_path)
    growth = {
        "id": 2,
        "text": "R&D grew 10.1%.",
        "value": 10.1,
        "unit": "percent",
        "chunk_ids": [TABLE_CHUNK],
        "calculation": {
            "op": "pct_change",
            "inputs": {"old": 31370, "new": 34550},
            "input_unit": "USD millions",
        },
    }
    model = ScriptedModel(
        [
            (
                "",
                [
                    ToolCall(
                        "c1",
                        "search_filings",
                        {
                            "query": "research and development",
                            "companies": ["Apple"],
                            "fiscal_years": [2025],
                        },
                    )
                ],
            ),
            (
                "",
                [
                    ToolCall(
                        "c2",
                        "calculator",
                        {"op": "pct_change", "inputs": {"old": 31370, "new": 34550}},
                    )
                ],
            ),
            (final([RD_CLAIM, growth]), []),
        ]
    )
    events = list(research("How much did Apple spend on R&D?", store, tools, model))

    steps = [e for e in events if e["type"] == "step"]
    assert [s["tool"] for s in steps] == ["search_filings", "calculator"]
    assert steps[0]["label"] == "Searching Apple FY2025 for “research and development”"
    assert TABLE_CHUNK in steps[0]["summary"]
    answer = events[-1]["answer"]
    assert [c["status"] for c in answer["claims"]] == ["verified", "calculated"]
    assert answer["citations"][TABLE_CHUNK]["url"] == "https://example.com/aapl.htm"
    trace = store.get_trace(answer["trace_id"])
    assert trace["totals"]["tool_calls"] == 2 and trace["totals"]["llm_calls"] == 3
    # Tool results go back to the model as tool messages.
    assert model.requests[1]["messages"][-1]["role"] == "tool"


def test_tool_budget_is_enforced(tmp_path):
    store, tools = toolbox(tmp_path)
    call = ToolCall("c", "calculator", {"op": "sum", "inputs": {"a": 1}})
    model = ScriptedModel([("", [call, call, call]), (final([]), [])])
    events = list(research("q", store, tools, model, max_tool_calls=2))
    steps = [e for e in events if e["type"] == "step"]
    assert [s["is_error"] for s in steps] == [False, False, True]
    assert "budget exhausted" in steps[2]["summary"]


def test_tool_errors_are_returned_to_the_model_not_raised(tmp_path):
    store, tools = toolbox(tmp_path)
    result, is_error = tools.run(
        "get_filing_section", {"company": "Oracle", "fiscal_year": 2024, "item": "7"}
    )
    assert is_error and "not in the corpus" in json.loads(result)["error"]
    result, is_error = tools.run("search_filings", {"query": "x", "fiscal_years": [2027]})
    assert is_error and "outside the corpus" in json.loads(result)["error"]
    result, is_error = tools.run("calculator", {"expression": "a +", "inputs": {"a": 1}})
    assert is_error
    result, is_error = tools.run("no_such_tool", {})
    assert is_error


def test_verify_citation_and_section_paging(tmp_path):
    store, tools = toolbox(tmp_path)
    found = json.loads(
        tools.run(
            "verify_citation", {"chunk_id": TABLE_CHUNK, "value": 34.55, "unit": "USD billions"}
        )[0]
    )
    assert found == {"chunk_id": TABLE_CHUNK, "found": True, "matched_span": "$34,550"}
    section = json.loads(
        tools.run("get_filing_section", {"company": "AAPL", "fiscal_year": 2025, "item": "8"})[0]
    )
    assert [c["chunk_id"] for c in section["chunks"]][-1] == TABLE_CHUNK
    assert section["next_start"] is None


def test_unverifiable_claims_are_labeled_not_dropped(tmp_path):
    store, _ = toolbox(tmp_path)
    wrong = RD_CLAIM | {"value": 40000}
    answer = finalize(
        {"answer": "x", "claims": [wrong, {"text": "t", "chunk_ids": ["nope"]}]}, store
    )
    assert [c["status"] for c in answer["claims"]] == ["unverified", "unverified"]
    assert answer["unverified"] == [1, 2]


def test_format_repair_then_fallback(tmp_path):
    store, tools = toolbox(tmp_path)
    model = ScriptedModel([("not json", []), ("still not json", [])])
    answer = run_research("q", store, tools, model)
    assert answer["format_error"] and answer["claims"] == []


def test_ask_pipeline_single_call_with_retrieved_chunks(tmp_path):
    store, tools = toolbox(tmp_path)
    model = ScriptedModel([(final([RD_CLAIM]), [])])
    answer = ask("research and development", store, tools.retriever, model)
    assert len(model.requests) == 1
    assert f'<chunk id="{TABLE_CHUNK}">' in model.requests[0]["messages"][0]["content"]
    assert answer["claims"][0]["status"] == "verified"
    assert TABLE_CHUNK in answer["retrieved"]


def test_scoring_numbers_citations_and_agent_metrics(tmp_path):
    store, _ = toolbox(tmp_path)
    q = {
        "answer_type": "number",
        "expected_answer": {"value": 34550, "unit": "USD millions"},
        "sources": [{"company": "AAPL", "fiscal_year": 2025}],
        "expected_tools": ["search_filings"],
    }
    answer = finalize(
        {"answer": "", "claims": [RD_CLAIM | {"value": 34.55, "unit": "USD billions"}]}, store
    )
    assert score_numbers(q, answer) == {"expected": 1, "matched": 1, "all": True}
    assert (
        score_numbers(
            q, finalize({"answer": "", "claims": [RD_CLAIM | {"fiscal_year": 2024}]}, store)
        )["all"]
        is False
    )
    assert citation_accuracy(answer, store) == {"numeric_claims": 1, "accurate": 1}
    span = {"type": "tool", "name": "search_filings", "input": {"query": "x"}, "is_error": False}
    metrics = agent_metrics(q, {"spans": [span, span]})
    assert metrics["tool_selection"] and metrics["duplicate_calls"] == 1


def test_summary_counts_answered_abstention_questions_as_unsupported():
    rows = [
        {
            "id": "a",
            "category": "failure_case",
            "should_abstain": True,
            "abstained": False,
            "verified": False,
            "correctness": 0.0,
            "task_complete": False,
        },
        {
            "id": "b",
            "category": "failure_case",
            "should_abstain": True,
            "abstained": True,
            "verified": False,
            "correctness": 1.0,
            "task_complete": True,
        },
        {
            "id": "c",
            "category": "retrieval",
            "should_abstain": False,
            "abstained": False,
            "verified": False,
            "correctness": 1.0,
            "task_complete": True,
        },
    ]
    answers = summarize(rows, "ask")["answers"]
    assert answers["unsupported_answer_rate"] == 0.5
    assert answers["false_abstention_rate"] == 0.0
    assert answers["task_completion"] == round(2 / 3, 4)


def test_anthropic_translation_groups_tool_results():
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("1", "a", {}), ToolCall("2", "b", {})],
            "provider_content": None,
        },
        {"role": "tool", "tool_call_id": "1", "content": "r1"},
        {"role": "tool", "tool_call_id": "2", "content": "r2"},
    ]
    translated = _anthropic_messages(messages)
    assert [m["role"] for m in translated] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in translated[2]["content"]] == ["1", "2"]


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('Here:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure. {"a": {"b": 2}} Done.') == {"a": {"b": 2}}


def test_failed_claims_get_one_repair_round(tmp_path):
    store, tools = toolbox(tmp_path)
    wrong = RD_CLAIM | {"chunk_ids": ["AAPL-FY2025-1A-000"]}
    model = ScriptedModel([(final([wrong]), []), (final([RD_CLAIM]), [])])
    answer = run_research("q", store, tools, model)
    assert answer["repaired"] and answer["claims"][0]["status"] == "verified"
    assert "could not confirm" in model.requests[1]["messages"][-1]["content"]

    model = ScriptedModel([(final([wrong]), [])])
    answer = run_research("q", store, tools, model, repair=False)
    assert answer["claims"][0]["status"] == "unverified" and len(model.requests) == 1


def test_malformed_claims_are_unverified_not_fatal(tmp_path):
    store, _ = toolbox(tmp_path)
    claims = [
        RD_CLAIM | {"value": "N/A"},
        RD_CLAIM | {"calculation": {"op": "pct_change", "inputs": [1, 2]}},
    ]
    answer = finalize({"answer": "x", "claims": claims}, store)
    assert [c["status"] for c in answer["claims"]] == ["unverified", "unverified"]
