"""Research mode (V3): a single hand-written tool-use loop over the model layer.

The agent decides what it needs, which tools to call and in what order, and checks its own
numbers with verify_citation. Each tool call is yielded as a step event (the UI streams
them over SSE). The final answer is the same JSON claims format as Ask mode, followed by
the deterministic verification pass.
"""

import json
import time
from collections.abc import Iterator

from tenk_agent.answers import ANSWER_FORMAT, RULES, corpus_table, finalize, unparsed
from tenk_agent.models import Model, parse_json
from tenk_agent.store import Store
from tenk_agent.tools import Toolbox
from tenk_agent.tracing import Trace

SYSTEM = f"""You are a research assistant for SEC 10-K filings. You answer by using tools, then write a structured, cited report.

How to work:
1. Work out what information the question needs: which companies, which fiscal years, which figures, and whether management's explanations (MD&A, Item 7) are needed as well as the financial statements (Item 8).
2. Find it with search_filings, filtered by company, fiscal year and Item. Use get_filing_section when you need to read a whole section or table in order.
3. Do every calculation with the calculator (percentage changes, shares of a total, ratios, sums). Never do arithmetic yourself.
4. Before answering, check key numbers with verify_citation against the chunk you will cite.
5. Stop when you have what you need. You have at most {{max_calls}} tool calls.

{RULES}

{{corpus}}

When you are done, write the final report. "answer" should give a short summary first, then the findings; use "table" for figures across years or companies. List anything you couldn't find or verify in the answer rather than guessing.

{ANSWER_FORMAT}"""

BUDGET_EXHAUSTED = "Tool-call budget exhausted. Write the final JSON answer now with what you have; mark anything missing."


def research(
    question: str,
    store: Store,
    toolbox: Toolbox,
    model: Model,
    max_tool_calls: int = 15,
    verify: bool = True,
    repair: bool = True,
    repair_calls: int = 4,
) -> Iterator[dict]:
    """Yield events: {"type": "step", ...} per tool call, then {"type": "answer", ...}.

    With `verify`, claims go through the deterministic verification pass; with `repair`
    too, claims that fail it are sent back to the agent once to correct (V4). Claims still
    failing afterwards stay in the answer, labeled unverified.
    """
    run = _Run(question, store, toolbox, model, max_tool_calls)
    try:
        yield from run.turns(max_tool_calls)
        raw = run.final_json()
        result = finalize(raw, store, verify)
        if verify and repair and result["unverified"]:
            run.messages.append({"role": "user", "content": _repair_request(result)})
            yield {"type": "thought", "text": "Re-checking claims that failed verification…"}
            yield from run.turns(max_tool_calls + repair_calls, name="repair")
            repaired = run.final_json(fallback=None)
            if repaired is not None:
                result = finalize(repaired, store, verify) | {"repaired": True}
    except Exception as e:  # recorded in the trace, then surfaced to the caller
        run.trace.error = f"{type(e).__name__}: {e}"
        run.trace.save(store)
        yield {"type": "error", "message": run.trace.error, "trace_id": run.trace.id}
        return

    result |= {
        "mode": "research",
        "question": question,
        "model": model.name,
        "trace_id": run.trace.id,
    }
    run.trace.output = {k: v for k, v in result.items() if k != "citations"}
    result["usage"] = run.trace.save(store)["totals"]
    yield {"type": "answer", "answer": result}


class _Run:
    """One research conversation: messages, the tool budget and the trace."""

    def __init__(self, question, store, toolbox, model, max_tool_calls):
        self.toolbox, self.model = toolbox, model
        self.trace = Trace("research", question, model.name)
        self.system = SYSTEM.replace("{max_calls}", str(max_tool_calls)).replace(
            "{corpus}", corpus_table(store)
        )
        self.messages: list[dict] = [{"role": "user", "content": question}]
        self.calls = 0
        self.response = None

    def turns(self, budget: int, name: str = "turn") -> Iterator[dict]:
        """Call the model until it stops calling tools; tool calls past `budget` fail."""
        # A few turns beyond the budget let the model answer after the budget message.
        for turn in range(budget - self.calls + 4):
            self.response = self.model.complete(
                self.messages, tools=self.toolbox.specs, system=self.system
            )
            self.trace.llm(f"{name} {turn + 1}", self.response, len(self.messages))
            self.messages.append(self.response.message())
            if not self.response.tool_calls:
                return
            if self.response.text:
                yield {"type": "thought", "text": self.response.text}
            for call in self.response.tool_calls:
                yield self._call(call, budget)

    def _call(self, call, budget: int) -> dict:
        self.calls += 1
        start = time.monotonic()
        if call.error:
            result, is_error = json.dumps({"error": call.error}), True
        elif self.calls > budget:
            result, is_error = json.dumps({"error": BUDGET_EXHAUSTED}), True
        else:
            result, is_error = self.toolbox.run(call.name, call.arguments)
        latency = time.monotonic() - start
        self.trace.tool(call.name, call.arguments, result, is_error, latency)
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": result,
                "is_error": is_error,
            }
        )
        return {
            "type": "step",
            "n": self.calls,
            "tool": call.name,
            "arguments": call.arguments,
            "label": step_label(call.name, call.arguments),
            "summary": result_summary(call.name, result, is_error),
            "is_error": is_error,
            "latency_s": round(latency, 3),
        }

    def final_json(self, fallback=unparsed) -> dict | None:
        """The final answer JSON, asking once more if the reply wasn't valid JSON."""
        text = self.response.text if self.response else ""
        raw = _final_json(text)
        if raw is None and self.response is not None:
            self.messages.append(
                {"role": "user", "content": "Reply with only the final JSON object now."}
            )
            self.response = self.model.complete(
                self.messages, tools=self.toolbox.specs, system=self.system
            )
            self.trace.llm("format repair", self.response, len(self.messages))
            text = self.response.text
            raw = _final_json(text)
        if raw is None and fallback is not None:
            return fallback(text)
        return raw


def _repair_request(result: dict) -> str:
    failed = "\n".join(
        f"- Claim {c['id']}: {c['text']} (cited {', '.join(c['chunk_ids']) or 'nothing'}): "
        f"{c['status_reason']}"
        for c in result["claims"]
        if c["status"] == "unverified"
    )
    return (
        "An automatic check could not confirm these claims in the chunks they cite:\n"
        f"{failed}\n\n"
        "For each one: find the chunk that actually contains the value and cite it, correct "
        "the value, fix the calculation inputs, or drop the number if the filings don't "
        "support it. You have a few more tool calls. Then reply with the complete corrected "
        "final JSON object (all claims, not just these)."
    )


def run_research(*args, **kwargs) -> dict:
    """Run the agent to completion and return the final answer (raises on error)."""
    for event in research(*args, **kwargs):
        if event["type"] == "answer":
            return event["answer"]
        if event["type"] == "error":
            raise RuntimeError(event["message"])
    raise RuntimeError("agent ended without an answer")


def _final_json(text: str) -> dict | None:
    try:
        return parse_json(text)
    except ValueError:
        return None


def step_label(tool: str, args: dict) -> str:
    """Human-readable step for the live step list, e.g. 'Searching AAPL FY2024 for …'."""
    if tool == "search_filings":
        scope = " ".join(
            filter(
                None,
                [
                    ", ".join(map(str, args.get("companies") or [])),
                    ", ".join(f"FY{y}" for y in args.get("fiscal_years") or []),
                    ", ".join(f"Item {i}" for i in args.get("items") or []),
                ],
            )
        )
        return f"Searching {scope or 'all filings'} for “{args.get('query', '')}”"
    if tool == "get_filing_section":
        return f"Reading {args.get('company')} FY{args.get('fiscal_year')} Item {args.get('item')}"
    if tool == "calculator":
        what = args.get("op") or args.get("expression") or "expression"
        return f"Calculating {what}"
    if tool == "verify_citation":
        target = args.get("value", args.get("quote", ""))
        return f"Verifying {target} in {args.get('chunk_id')}"
    if tool == "get_financial_facts":
        return f"Looking up XBRL facts for {args.get('company')} FY{args.get('fiscal_year')}"
    return tool


def result_summary(tool: str, result: str, is_error: bool) -> str:
    data = json.loads(result)
    if is_error:
        return data.get("error", "error")
    if tool == "search_filings":
        return f"{len(data['results'])} results: " + ", ".join(
            r["chunk_id"] for r in data["results"][:5]
        )
    if tool == "get_filing_section":
        more = f", more from {data['next_start']}" if data.get("next_start") else ""
        return f"{len(data['chunks'])} of {data['total_chunks']} chunks{more}"
    if tool == "calculator":
        return f"{data['formula']} = {data['result']:,.4f}".rstrip("0").rstrip(".")
    if tool == "verify_citation":
        return "found " + repr(data["matched_span"]) if data["found"] else "not found"
    return ""
