"""V4 Investigate (agent/plan.py) with a scripted planner and stub sub-agents."""

import json

from tenk_agent.agent.loop import Run
from tenk_agent.agent.plan import NOT_FROM_SUB, Investigator
from tenk_agent.agent.tools import Toolbox
from tenk_agent.generation.answers import finalize
from test_agent_loop import ANSWER, Scripted
from test_agent_tools import FACT, make_sec, make_store

QUESTION = "How much did Apple spend on R&D in fiscal 2025, and what does that imply?"


def plan(*subs) -> str:
    return json.dumps({"sub_questions": [{"question": q, "mode": m} for q, m in subs]})


class Stub:
    """A sub-agent: records the questions it got, answers each with ANSWER."""

    def __init__(self, toolbox, fail=False):
        self.toolbox, self.fail, self.asked = toolbox, fail, []

    def run(self, question):
        self.asked.append(question)
        if self.fail:
            raise RuntimeError("model down")
        return Run(finalize(ANSWER, self.toolbox.store, self.toolbox.sec), [], 1.0, "answered")


def investigator(tmp_path, replies, fail=False):
    toolbox = Toolbox(make_store(tmp_path), None, make_sec(tmp_path), "ask")
    agents = {"ask": Stub(toolbox, fail), "research": Stub(toolbox, fail)}
    return Investigator(Scripted(replies), agents, max_sub=2, max_follow=1), agents


def test_sub_questions_run_alone_then_the_answer_is_combined_and_checked(tmp_path):
    inv, agents = investigator(tmp_path, [
        plan(("What was Apple's R&D expense in fiscal 2025?", "ask")),
        plan(),  # no follow-ups
        json.dumps(ANSWER),
    ])
    result = inv.run(QUESTION)
    assert agents["ask"].asked == ["What was Apple's R&D expense in fiscal 2025?"]
    assert [s.id for s in result.sub_runs] == ["s1"] and result.stop == "answered"
    assert result.answer["claims"][0]["status"] == "verified"


def test_a_claim_citing_an_id_no_sub_answer_cited_is_unverified(tmp_path):
    claim = ANSWER["claims"][0] | {"chunk_ids": [FACT, "kpi:AAPL:revenue:2025Q4"]}
    inv, _ = investigator(tmp_path, [
        plan(("What was Apple's R&D expense in fiscal 2025?", "ask")), plan(),
        json.dumps(ANSWER | {"claims": [claim]}),
    ])
    checked = inv.run(QUESTION).answer["claims"][0]
    assert checked["status"] == "unverified" and NOT_FROM_SUB in checked["status_reason"]


def test_bad_sub_questions_are_rejected_and_the_plan_cut_to_its_limit(tmp_path):
    inv, agents = investigator(tmp_path, [
        plan((QUESTION, "research"),  # restates the question
             ("What is Apple's R&D?", "guess"),  # unknown mode
             ("What was Apple's R&D in FY2025?", "ask"),
             ("What was Apple's R&D in FY2024?", "ask"),
             ("What was Apple's R&D in FY2023?", "ask")),  # past max_sub
        plan(("What was Apple's R&D in FY2025?", "ask"),  # a repeat
             ("What was Apple's revenue in FY2025?", "research")),
        json.dumps(ANSWER),
    ])
    result = inv.run(QUESTION)
    assert agents["ask"].asked == ["What was Apple's R&D in FY2025?",
                                   "What was Apple's R&D in FY2024?"]
    assert [(s.id, s.follow_up) for s in result.sub_runs][-1] == ("s3", True)
    assert len(result.log) == 4  # restated, mode, cut, repeat


def test_an_unusable_plan_falls_back_to_one_research_run(tmp_path):
    inv, agents = investigator(tmp_path, ["no plan", "still no plan"])
    result = inv.run(QUESTION)
    assert result.fallback and agents["research"].asked == [QUESTION]
    assert result.answer["claims"][0]["status"] == "verified"


def test_a_failed_sub_run_is_a_gap_the_combiner_sees(tmp_path):
    inv, _ = investigator(tmp_path, [
        plan(("What was Apple's R&D expense in fiscal 2025?", "ask")), plan(),
        json.dumps({"answer": "R&D is not available.", "claims": [], "abstained": True}),
    ], fail=True)
    result = inv.run(QUESTION)
    assert result.sub_runs[0].stop.startswith("failed: RuntimeError")
    combine_input = inv.model.seen[-1][0][0]["content"]
    assert '"failed"' in combine_input and result.answer["abstained"]
