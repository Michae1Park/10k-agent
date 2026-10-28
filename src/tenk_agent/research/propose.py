"""S4 assumption proposer (V4 design doc §7.3): the LLM's only influence on the numbers.

A bounded sub-agent (the Ask tools, prompts/propose.md, research.yaml `proposer.budget`) sees a
driver table: each proposable assumption with its history, default and bounds. It returns
proposals {driver, value, basis, confidence, rationale, evidence}. Code then
  - keeps only drivers that are proposable: growth starts (.g0), ratio changes (.shift0) and
    the fade; never one a guidance anchor solves (the anchor runs after and would undo it);
  - clips each value to its bounds (growth start: its last-8-quarter range ± 5 points; ratio
    change: ± the ratio's 8-quarter range plus its band; fade: research.yaml fade_bounds) and
    logs the clip;
  - keeps a proposal only if at least one evidence id resolves at the as-of (research/refs.py).
`proposer.method: defaults` skips the LLM: no overrides (the ablation, §13.2).
"""

import re
from dataclasses import asdict, dataclass

from tenk_agent.agent.loop import Agent
from tenk_agent.agent.tools import Toolbox
from tenk_agent.models import get_model, parse_json
from tenk_agent.prompts import PROMPTS
from tenk_agent.research.model.graph import Model
from tenk_agent.research.refs import Resolver
from tenk_agent.store import Store

PROMPT = PROMPTS / "propose.md"
GROWTH_SLACK = 0.05


@dataclass
class Proposal:
    driver: str
    value: float  # as applied, after clipping
    proposed: float
    default: float
    clipped: bool
    basis: str
    confidence: str
    rationale: str
    evidence: list[str]  # the ids that resolved


def proposable(model: Model, anchored: set[str]) -> dict[str, tuple[float, float]]:
    """{driver: (low, high)} the proposer may set."""
    out = {}
    for name in model.assumptions:
        if name in anchored:
            continue
        if m := re.fullmatch(r"(.+)\.g0", name):
            values = _history(model, m[1])
            if values:
                out[name] = (min(values) - GROWTH_SLACK, max(values) + GROWTH_SLACK)
        elif m := re.fullmatch(r"(.+)\.shift0", name):
            values = _history(model, m[1])
            if values:
                width = max(values) - min(values)
                band = model.assumptions.get(f"{m[1]}.hi", max(values)) - max(values)
                out[name] = (-(width + band), width + band)
    return out


def _history(model: Model, line: str) -> list[float]:
    return [v for q in model.history[-8:] if (v := model.value(line, q)) is not None]


def driver_view(model: Model, bounds: dict[str, tuple[float, float]], anchored: dict[str, float],
                fade_bounds: tuple[float, float]) -> str:
    """The driver table the proposer reads (§8: a view, not the data pack)."""
    quarters = model.history[-8:]
    rows = [f"Company {model.ticker}, model as of {model.as_of}; history {quarters[0]}–"
            f"{quarters[-1]}; first forecast quarter {model.forecast[0]}.", "",
            "| driver | " + " | ".join(quarters) + " | default | bounds |",
            "|---|" + "---|" * (len(quarters) + 2)]
    for name, (lo, hi) in sorted(bounds.items()):
        line = re.sub(r"\.(g0|shift0)$", "", name)
        history = [model.value(line, q) for q in quarters]
        cells = " | ".join("" if v is None else f"{v:.4f}" for v in history)
        rows.append(f"| {name} | {cells} | {model.assumptions[name]:.4f} | {lo:.4f} to {hi:.4f} |")
    rows += ["", f"fade: default {model.assumptions.get('fade', 0.9)}, bounds {fade_bounds[0]} to "
             f"{fade_bounds[1]}", "", "Anchored to guidance (not yours to change): "
             + (", ".join(f"{k} = {v:.4f}" for k, v in anchored.items()) or "none")]
    return "\n".join(rows)


def propose(model: Model, store: Store, anchored: dict[str, float], settings: dict,
            search=None) -> tuple[dict[str, float], list[Proposal], list[dict]]:
    """(overrides, the applied proposals, exceptions) from the LLM proposer."""
    config = settings["proposer"]
    fade_bounds = tuple(settings["forecast"]["fade_bounds"])
    bounds = proposable(model, set(anchored)) | {"fade": fade_bounds}
    llm = get_model(config["model"], base_url=config.get("base_url"), thinking=False)
    budget = config["budget"]
    agent = Agent(llm, Toolbox(store, search, None, "ask"), max_calls=budget["max_calls"],
                  max_tokens=budget["max_tokens"], max_context=budget["max_context"],
                  prompt=PROMPT)
    view = driver_view(model, {k: v for k, v in bounds.items() if k != "fade"}, anchored,
                       fade_bounds)
    run = agent.run(f"Set the forecast assumptions for {model.ticker}.\n\n{view}")
    return apply(run.reply, model, bounds, Resolver(store))


def apply(reply: str, model: Model, bounds: dict[str, tuple[float, float]],
          resolver: Resolver) -> tuple[dict[str, float], list[Proposal], list[dict]]:
    """Validate, clip and keep the reply's proposals."""
    exceptions: list[dict] = []
    try:
        raw = parse_json(reply)
        proposals = raw.get("proposals") or [] if isinstance(raw, dict) else []
    except ValueError:
        return {}, [], [{"severity": "warning", "item": "proposer reply has no JSON: defaults"}]
    overrides, applied = {}, []
    for p in proposals:
        if not isinstance(p, dict):
            continue
        driver = str(p.get("driver", ""))
        if driver not in bounds:
            exceptions.append({"severity": "info", "item": f"proposal {driver!r} dropped: not a "
                               "proposable driver (or anchored to guidance)"})
            continue
        try:
            value = float(p.get("value"))
        except (TypeError, ValueError):
            exceptions.append({"severity": "info", "item": f"proposal {driver}: no number"})
            continue
        evidence = [str(i) for i in p.get("evidence") or [] if resolver.resolve(str(i))]
        if not evidence:
            exceptions.append({"severity": "info", "item": f"proposal {driver} dropped: no "
                               "evidence id resolves"})
            continue
        lo, hi = bounds[driver]
        clipped = min(max(value, lo), hi)
        if clipped != value:
            exceptions.append({"severity": "warning", "item": f"proposal {driver} {value:.4f} "
                               f"clipped to {clipped:.4f} (bounds {lo:.4f} to {hi:.4f})"})
        overrides[driver] = clipped
        applied.append(Proposal(driver, clipped, value, model.assumptions.get(driver, value),
                                clipped != value, str(p.get("basis", "")),
                                str(p.get("confidence", "")), str(p.get("rationale", "")),
                                evidence))
    return overrides, applied, exceptions


def to_json(proposals: list[Proposal]) -> list[dict]:
    return [asdict(p) for p in proposals]

