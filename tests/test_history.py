from types import SimpleNamespace

from era_agent.agent import history


def chat():
    return [
        {"role": "user", "text": "Price target for DASH?"},
        {"role": "assistant", "answer": {"answer": "About $250.", "table": "| a |",
                                         "assumptions": ["take rate 13%"]}},
        {"role": "user", "text": "e"},
        {"role": "assistant", "error": "Cancelled."},
        {"role": "user", "text": "And the risks?"},
        {"role": "assistant", "rewritten": "What risks face DASH's price target?",
         "answer": {"answer": "Competition."}},
    ]


def test_turns_skip_failed_and_use_rewrites():
    out = history.turns(chat(), 5)
    assert [t["question"] for t in out] == ["Price target for DASH?",
                                            "What risks face DASH's price target?"]
    assert out[0]["answer"] == "About $250.\n\n| a |\n\nAssumptions: take rate 13%"
    assert history.turns(chat(), 1) == out[-1:]
    assert history.turns(chat(), 0) == []


def test_as_messages_and_context():
    turns = history.turns(chat(), 5)
    messages = history.as_messages(turns)
    assert [m["role"] for m in messages] == ["user", "assistant"] * 2
    assert messages[0]["content"] == "Question: Price target for DASH?"
    text = history.as_context(turns, "Why?")
    assert text.startswith("Conversation so far") and text.endswith("(answer this one): Why?")


def test_rewrite_falls_back_to_question():
    turns = history.turns(chat(), 5)

    def model(text):
        return SimpleNamespace(complete=lambda *a, **k: SimpleNamespace(text=text))

    assert history.rewrite(model('"DASH risks?"\nextra'), turns, "risks?") == "DASH risks?"
    assert history.rewrite(model(""), turns, "risks?") == "risks?"
