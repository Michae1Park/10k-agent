import json

from tenk_agent.config import Settings
from tenk_agent.evaluation import runner
from test_store_and_gold import make_store


class FakeJudge:
    def points_covered(self, question, points, answer):
        return [True] * len(points)

    def faithfulness(self, claims, citations):
        assert all(cid in citations for c in claims for cid in c["chunk_ids"])
        return [True] * len(claims)

    def relevance(self, question, texts):
        return [True] * len(texts)


def test_judge_run_adds_judge_metrics_to_a_finished_run(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    chunk = "AAPL-FY2025-8-001"
    assert store.get_chunk(chunk)
    gold = tmp_path / "gold.jsonl"
    question = {
        "id": "q1",
        "question": "Why did R&D grow?",
        "split": "test",
        "category": "grounding",
        "answer_type": "text",
        "required_points": ["headcount"],
    }
    gold.write_text(json.dumps(question) + "\n")
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({"system": "ask", "gold": str(gold)}))
    claim = {"id": 1, "text": "R&D grew.", "value": None, "chunk_ids": [chunk]}
    row = runner._base(question | {"origin": "drafted"}) | {
        "answer": {"answer": "R&D grew [1].", "claims": [claim], "abstained": False},
        "abstained": False,
        "numbers": None,
        "citations": {"numeric_claims": 0, "accurate": 0},
    }
    (run_dir / "results.jsonl").write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "load_judge", lambda settings: FakeJudge())
    monkeypatch.setattr(runner, "Store", lambda path: store)

    summary = runner.judge_run("r1", Settings(data_dir=tmp_path))

    judged = json.loads((run_dir / "results.jsonl").read_text())
    assert judged["judged"] and judged["points_covered"] == [True]
    assert judged["faithfulness"]["supported"] == 1
    assert summary["answers"]["correctness"] == 1.0
    assert summary["answers"]["faithfulness"] == 1.0
