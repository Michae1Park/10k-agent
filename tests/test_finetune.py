import json

from tenk_agent.corpus import load_corpus
from tenk_agent.finetune import dataset, rollouts
from tenk_agent.finetune.questions import heldout_companies, split_of
from tenk_agent.models import ToolCall
from test_agent import RD_CLAIM, ScriptedModel, final, toolbox

OPTIONS = dataset.DatasetOptions(rollouts=None, out=None)


def rollout_row(**overrides) -> dict:
    """A passing rollout over a training-corpus company."""
    transcript = {
        "system": "You are a research assistant.",
        "tools": [{"name": "search_filings", "description": "Search.", "parameters": {}}],
        "messages": [
            {"role": "user", "content": "What was Intel's total revenue in fiscal 2024?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "name": "search_filings", "arguments": {"query": "revenue"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "search_filings",
                "content": json.dumps({"results": [{"chunk_id": "INTC-FY2024-8-003"}]}),
                "is_error": False,
            },
            {"role": "assistant", "content": '{"answer": "53,101", "claims": []}'},
        ],
    }
    row = {
        "id": "ft-single-abc",
        "sample": 0,
        "split": "train",
        "kind": "single",
        "question": "What was Intel's total revenue in fiscal 2024?",
        "model": "openai:Qwen/Qwen3.5-9B",
        "corpus": ["INTC", "WMT"],
        "passed": True,
        "tool_errors": 0,
        "transcript": transcript,
    }
    return row | overrides


def reject(row: dict) -> str | None:
    return dataset.rejection(row, OPTIONS, {"aapl-rd-fy2024"}, {"how much did apple spend"})


def test_a_passing_training_corpus_rollout_is_kept():
    assert reject(rollout_row()) is None


def test_d022_guards_reject_claude_gold_and_eval_corpus():
    assert reject(rollout_row(model="anthropic:claude-sonnet-5")) == "D-022: Claude output"
    assert reject(rollout_row(id="aapl-rd-fy2024")) == "D-022: gold question"
    assert reject(rollout_row(question="How much did Apple spend?")) == "D-022: gold question"
    assert reject(rollout_row(corpus=["AAPL"])) == "D-022: eval-corpus filing"
    leaked = rollout_row()
    leaked["transcript"]["messages"][2]["content"] = '{"chunk_id": "MSFT-FY2024-8-001"}'
    assert reject(leaked) == "D-022: eval-corpus filing"
    # Eval companies' filings before the eval years are allowed (D-022).
    old = rollout_row()
    old["transcript"]["messages"][2]["content"] = '{"chunk_id": "MSFT-FY2021-8-001"}'
    assert reject(old) is None


def test_quality_filters():
    assert reject(rollout_row(passed=False)) == "failed checks"
    assert reject(rollout_row(tool_errors=1)) == "tool errors"
    assert reject(rollout_row(split="heldout")) == "split heldout"


def test_record_is_openai_chat_format_with_object_arguments():
    record = dataset.to_record(rollout_row())
    roles = [m["role"] for m in record["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    call = record["messages"][2]["tool_calls"][0]
    # vLLM hands the chat template arguments as objects, so training must too.
    assert call["function"] == {"name": "search_filings", "arguments": {"query": "revenue"}}
    assert record["messages"][3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": rollout_row()["transcript"]["messages"][2]["content"],
    }
    assert record["tools"][0]["type"] == "function"


def test_build_dedupes_and_caps_attempts_per_question(tmp_path):
    rows = [rollout_row(sample=i) for i in range(3)]
    rows[2]["transcript"]["messages"][3]["content"] = '{"answer": "53.1 billion", "claims": []}'
    rows.append(rollout_row(id="ft-single-def", sample=0, model="anthropic:claude-sonnet-5"))
    path = tmp_path / "rollouts.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = dataset.build(dataset.DatasetOptions(path, tmp_path / "sft.jsonl"))
    assert report.kept == 2
    assert report.rejected == {"duplicate attempt": 1, "D-022: Claude output": 1}


def test_agent_transcript_is_captured_and_serializable(tmp_path):
    store, tools = toolbox(tmp_path)
    call = ToolCall("c1", "search_filings", {"query": "research and development"})
    model = ScriptedModel([("", [call]), (final([RD_CLAIM]), [])])
    row = rollouts.rollout(
        {"id": "q", "split": "train", "question": "Apple R&D?", "answer_type": "number",
         "expected_answer": {"value": 34550, "unit": "USD millions"},
         "sources": [{"company": "AAPL", "fiscal_year": 2025}]},
        0, store, tools, model, settings=_Settings(),
    )  # fmt: skip
    assert row["passed"] and all(row["checks"].values())
    messages = row["transcript"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["tool_calls"][0]["name"] == "search_filings"
    json.dumps(row)  # plain JSON all the way down
    # The test store is the eval corpus: the guard must refuse it.
    assert reject(row) == "D-022: eval-corpus filing"


class _Settings:
    max_tool_calls, verify, repair, repair_calls, thinking = 15, True, True, 4, False


def test_corpus_file_and_heldout_split(tmp_path):
    path = tmp_path / "corpus.yaml"
    path.write_text(
        "fiscal_years: [2023, 2024]\n"
        "companies:\n  - {ticker: INTC, cik: 50863, name: Intel Corporation, aliases: [Intel]}\n"
    )
    companies, years = load_corpus(path)
    assert companies[0].ticker == "INTC" and companies[0].aliases == ("Intel",)
    assert years == (2023, 2024)
    held = heldout_companies(companies * 1, 0.2, seed=7)
    assert held == {"INTC"}  # at least one company is always held out
    assert split_of(["INTC", "WMT"], held) == "heldout"
    assert split_of(["WMT"], held) == "train"
