"""S10 writer (design doc §10.2): prose for each slot from its facts only, number-traced.

One LLM call per slot (prompts/section.md, no tools); the number trace (trace.py) checks it;
one repair turn names the untraced numbers; any sentence that still holds one is dropped from
the text and logged as an exception (so a draft never carries a figure that has no source).
"""

import re

from tenk_agent.models import parse_json
from tenk_agent.prompts import PROMPTS
from tenk_agent.research.note import trace
from tenk_agent.research.note.facts import Facts
from tenk_agent.research.note.sections import Section, Slot

PROMPT = PROMPTS / "section.md"


def fill(sections: list[Section], facts: Facts, llm, max_tokens: int = 1500) -> list[dict]:
    """Write every slot in place; returns the exceptions (untraced numbers)."""
    system = PROMPT.read_text()
    exceptions = []
    for section in sections:
        for block in section.blocks:
            if not isinstance(block, Slot):
                continue
            listing = "\n".join(f"{f.id} | {f.label} | {f.text()} | {f.source}"
                                for i in block.facts if (f := facts.get(i)))
            messages = [{"role": "user", "content": f"{block.instruction}\n\nFacts:\n{listing}"}]
            text = _ask(llm, messages, system, max_tokens)
            untraced = trace.check(text, _slot_facts(facts, block), block.key)
            if untraced:
                messages += [{"role": "assistant", "content": f'{{"text": {text!r}}}'},
                             {"role": "user", "content": "These numbers are not in the facts; "
                              "rewrite without them or with the facts' figures: "
                              + ", ".join(u.text for u in untraced)}]
                text = _ask(llm, messages, system, max_tokens) or text
                untraced = trace.check(text, _slot_facts(facts, block), block.key)
            if untraced:
                text = _drop_untraced(text, _slot_facts(facts, block), block.key)
            block.text = text
            exceptions += [{"severity": "warning", "item": f"dropped sentence with untraced "
                            f"number {u.text!r} in section {section.number} ({u.where})"}
                           for u in untraced]
    return exceptions


def _drop_untraced(text: str, slot_facts: Facts, key: str) -> str:
    """The text without the sentences whose figures still fail the trace."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in sentences if not trace.check(s, slot_facts, key))


def _slot_facts(facts: Facts, slot: Slot) -> Facts:
    return Facts([f for i in slot.facts if (f := facts.get(i))])


def _ask(llm, messages, system, max_tokens) -> str:
    reply = llm.complete(messages, system=system, max_tokens=max_tokens)
    try:
        return str(parse_json(reply.text).get("text", "")).strip()
    except (ValueError, AttributeError):
        return reply.text.strip()
