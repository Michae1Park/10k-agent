# V4 · Agent runtime and tools: how they work

Companion to [design doc §3](../V4_TECHNICAL_DESIGN_DOC.md#3-tools) (tools) and [§6](../V4_TECHNICAL_DESIGN_DOC.md#6-agent-runtime) (agent runtime), which hold the contract. The V3 loop is described in [V3_AGENT.md](V3_AGENT.md). This doc does not restate the rules; where the two disagree, the design doc wins.

## The loop

```text
question ─► model ──tool calls──► tools ─► model ─► … ─► JSON answer ─► claim check
```

`agent/loop.py` is a LangGraph graph: the model turn, a tool node, a budget guard and a final answer node. The first turn forces a tool call; a repeated identical call is answered with an error and still counts; past `max_calls`, or once the conversation reaches `max_context` tokens, one last turn runs with tools off. The reply is parsed with `models.parse_json`; a parse failure gets one "only the JSON" retry. The claim check then marks each claim (design §10.3).

**Context management.** State lives in files, not chat. Each call gets a purpose-built view: the proposer a driver table, a section writer only its own facts, a debate one driver. Detail is fetched by id, never preloaded.

## Tools

`agent/tools.py` holds `SPECS` (the JSON schemas the model sees), the groups of design §3.2 and one handler per tool. `Toolbox(tool_set=…)` exposes only the groups of its set. Every handler catches its own errors and returns `{"error": …}` with the valid choices.

<p align="center">
  <img src="../img/v4-research-tools.svg" alt="run_scenario, compare_peers and variance_review: validate, rerun or reuse cached code runs, return tables with citable ids" width="100%">
</p>

| Tool | How |
|---|---|
| `financials` | derived quarters (`facts/quarters.py`) and direct facts, as `q:` / `xbrl:` ids |
| `research_report` | the latest `model_versions` row visible at D (or at `as_of`, which only narrows); reads the version's folder |
| `model_view` | the model book's base model at the session's as-of |
| `run_scenario` | validates names, clips to the proposer's bounds, reruns forecast and valuation, stores `scratch-n` in the book |
| `compare_peers` | builds each peer with defaults on first use, then reads the cached models |
| `variance_review` | the latest saved version before the quarter's release vs the reported actual, with a revenue bridge |

## Sub-agents

`research/subagents.py` (S2 events, S9 debates) and `research/propose.py` (S4) run a bounded `Agent` with the `ask` tools, their own prompt and a `research.yaml` budget. Each returns JSON; an item is kept only if one of its evidence ids resolves at D. A debate's resolved evidence values are kept as `evidence_values` and become note facts.

## Tests

`test_agent_loop.py`: tool results feed the next turn; calls past the budget error and force an answer; a non-JSON reply gets one retry; a repeated call errors and counts; a long conversation answers with tools off. `test_agent_tools.py` (21): calculator formula and errors; offline SEC data never fetches; a value matching a cited fact verifies; another company's fact doesn't. `test_ask.py`: guidance targets become period keys; data ids resolve at the store's as-of; claims verify against data rows; the prompt names the as-of and coverage. `test_forecast.py::proposals_are_bounded_anchors_kept_and_evidence_required`. Gold: Ask (79) and Research (72) ([V4_EVALUATION.md](V4_EVALUATION.md)).
