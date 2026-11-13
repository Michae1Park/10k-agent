"""Multi-turn chat: earlier turns as context for a follow-up question.

Two strategies (config `chat.history`, or per request):
  append   the earlier turns go into the agent's conversation as user/assistant messages
           (Ask, Research) or as a "Conversation so far" block in the question (Investigate)
  rewrite  one model call rewrites the follow-up into a standalone question; the agent runs
           on it with no history
  none     each question stands alone (the single-turn behaviour)
"""

import json

from era_agent.prompts import PROMPTS

STRATEGIES = ("append", "rewrite", "none")
REWRITE_PROMPT = PROMPTS / "rewrite.md"
REWRITE_ANSWER_CHARS = 2000  # an earlier answer as the rewrite call sees it


def answer_text(answer: dict | None) -> str:
    """An answer as the model reads it back: the prose, its table and assumptions."""
    if not answer:
        return ""
    parts = [answer.get("answer") or ""]
    if table := answer.get("table"):
        parts.append(table if isinstance(table, str) else json.dumps(table, ensure_ascii=False))
    if assumptions := answer.get("assumptions"):
        parts.append("Assumptions: " + "; ".join(map(str, assumptions)))
    return "\n\n".join(p for p in parts if p)


def turns(messages: list[dict], limit: int) -> list[dict]:
    """The last `limit` answered turns of a saved chat → [{question, answer}]. A question
    whose run failed or was cancelled is skipped."""
    out, question = [], None
    for m in messages:
        if m["role"] == "user":
            question = m["text"]
        elif question is not None:
            if text := answer_text(m.get("answer")):  # a rewrite stands in for the follow-up
                out.append({"question": m.get("rewritten") or question, "answer": text})
            question = None
    return out[-limit:] if limit else []


def as_messages(history: list[dict]) -> list[dict]:
    """Earlier turns as the agent loop's messages, oldest first."""
    out = []
    for t in history:
        out += [{"role": "user", "content": f"Question: {t['question']}"},
                {"role": "assistant", "content": t["answer"]}]
    return out


def as_context(history: list[dict], question: str) -> str:
    """Earlier turns and the follow-up as one question text (for the Investigate planner)."""
    lines = ["Conversation so far (oldest first):"]
    for t in history:
        lines += [f"User: {t['question']}", f"Analyst: {t['answer']}", ""]
    return "\n".join(lines) + f"\nFollow-up question (answer this one): {question}"


def rewrite(model, history: list[dict], question: str) -> str:
    """The follow-up as a standalone question; the question unchanged if the call fails."""
    shown = "\n\n".join(
        f"User: {t['question']}\nAnalyst: {t['answer'][:REWRITE_ANSWER_CHARS]}" for t in history)
    prompt = REWRITE_PROMPT.read_text().replace("{history}", shown).replace("{question}",
                                                                             question)
    response = model.complete([{"role": "user", "content": prompt}], max_tokens=300)
    lines = (response.text or "").strip().splitlines()
    text = lines[0].strip().strip('"').strip() if lines else ""
    return text or question
