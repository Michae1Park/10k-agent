"""The model graph (V4 design doc §5.1): lines × periods from a template, evaluated in Python.

A template (templates/*.yaml) lists lines; each is
  input       a reported series from the data pack (blue, with its source id)
  formula     a DSL expression over other lines (black)
  assumption  a forecast driver (V4.4; blue, Assumptions tab)
and says how its fiscal-year column is made (`fy`): sum (flows), last (balances), input (the
reported FY: per-share lines), formula (ratios recomputed from FY totals) or none.

Columns are quarters, with an FY column after each Q4 of a complete year; lags count quarters
only. `templates/statements.yaml` (the condensed statements, §5.2) is read first, then the
company's template (its operating build), whose lines may replace statement lines by name.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tenk_agent.research.datapack import Point
from tenk_agent.research.model.dsl import Expr, Readers, evaluate, parse

TEMPLATES = Path(__file__).with_name("templates")
KINDS = ("input", "formula", "assumption")
FY_RULES = ("sum", "last", "input", "formula", "none")


@dataclass
class Line:
    name: str
    kind: str
    label: str
    unit: str
    statement: str
    fy: str
    expr: Expr | None = None
    source: str | None = None  # input: q:<metric> or kpi:<metric>[.<segment>]
    fallback: str | None = None  # input: a second source for periods the first lacks (Q4 EPS)
    default: float | None = None  # input: the value when the company doesn't tag it, logged


@dataclass
class Cell:
    value: float | None
    kind: str  # input, default, formula, missing
    ref: str | None = None  # input: its citable id
    filed: str | None = None


@dataclass
class Model:
    ticker: str
    template: str
    as_of: str
    lines: list[Line]
    quarters: list[str]
    columns: list[str]  # quarters and FY columns, in sheet order
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    defaults: list[str] = field(default_factory=list)  # "line period": filled by `default`

    def value(self, line: str, column: str) -> float | None:
        cell = self.cells.get((line, column))
        return cell.value if cell else None

    def line(self, name: str) -> Line:
        return next(x for x in self.lines if x.name == name)

    def to_json(self) -> dict:
        return {
            "ticker": self.ticker, "template": self.template, "as_of": self.as_of,
            "columns": self.columns,
            "lines": [{"name": x.name, "kind": x.kind, "label": x.label, "unit": x.unit,
                       "statement": x.statement, "fy": x.fy, "source": x.source,
                       "expr": x.expr.text if x.expr else None} for x in self.lines],
            "cells": {f"{n}|{c}": vars(cell) for (n, c), cell in self.cells.items()},
        }


def load_template(name: str, ticker: str) -> tuple[list[Line], dict]:
    """The statements' lines, then the template's (bound to the company's KPI names); a
    template line replaces a statement line of the same name. Returns (lines, template)."""
    template = yaml.safe_load((TEMPLATES / f"{name}.yaml").read_text())
    if ticker not in template.get("companies", {}):
        raise ValueError(f"{ticker} has no binding in template {name!r}")
    binding = template["companies"][ticker] or {}
    specs = dict(yaml.safe_load((TEMPLATES / "statements.yaml").read_text())["lines"])
    for line_name, spec in template["lines"].items():
        specs.pop(line_name, None)
        specs[line_name] = spec
    for line_name, spec in (binding.get("lines") or {}).items():  # the company's own definition
        specs[line_name] = spec
    for line_name in binding.get("drop", []):
        specs.pop(line_name)
    lines = [_line(n, s, binding.get("sources", {})) for n, s in specs.items()]
    return _ordered(lines), template


def _line(name: str, spec: dict, sources: dict) -> Line:
    kind = spec["kind"]
    if kind not in KINDS:
        raise ValueError(f"{name}: kind {kind!r}")
    fy = spec.get("fy", "sum" if kind == "input" else "formula")
    if fy not in FY_RULES:
        raise ValueError(f"{name}: fy {fy!r}")
    source = sources.get(name, spec.get("source"))
    if kind == "input" and not source:
        raise ValueError(f"{name}: an input needs a source")
    return Line(name, kind, spec.get("label", name.replace("_", " ")), spec["unit"],
                spec.get("statement", "operating"), fy,
                parse(spec["expr"]) if kind == "formula" else None, source,
                spec.get("fallback"), spec.get("default"))


def _ordered(lines: list[Line]) -> list[Line]:
    """Sheet order kept where it can be; a formula after every line it reads in its period."""
    by_name = {x.name: x for x in lines}
    for x in lines:
        unknown = (x.expr.lines - by_name.keys()) if x.expr else set()
        if unknown:
            raise ValueError(f"{x.name} reads unknown lines {sorted(unknown)}")
    done: list[Line] = []
    placed: set[str] = set()
    visiting: set[str] = set()

    def place(x: Line):
        if x.name in placed:
            return
        if x.name in visiting:
            raise ValueError(f"cycle through {x.name}")
        visiting.add(x.name)
        for dep in sorted(x.expr.same_period) if x.expr else ():
            place(by_name[dep])
        visiting.discard(x.name)
        placed.add(x.name)
        done.append(x)

    for x in lines:
        place(x)
    return done


def columns_for(quarters: list[str]) -> list[str]:
    """Quarters in order, with FY<year> after the Q4 of each year whose four quarters are in."""
    out = []
    for q in quarters:
        out.append(q)
        year = q[:4]
        if q.endswith("Q4") and all(f"{year}Q{i}" in quarters for i in (1, 2, 3)):
            out.append(f"FY{year}")
    return out


def build(ticker: str, template: str, as_of: str, quarters: list[str],
          pack: dict[str, dict[str, Point]]) -> Model:
    """The history model: every input from the data pack, every formula evaluated."""
    lines, _ = load_template(template, ticker)
    model = Model(ticker, template, as_of, lines, quarters, columns_for(quarters))
    for column in model.columns:
        fiscal = column.startswith("FY")
        for line in lines:
            model.cells[(line.name, column)] = (
                _fy_cell(model, line, column, pack) if fiscal else _cell(model, line, column, pack)
            )
    return model


def _cell(model: Model, line: Line, quarter: str, pack) -> Cell:
    if line.kind == "input":
        point = pack.get(line.source, {}).get(quarter) or pack.get(line.fallback, {}).get(quarter)
        if point:
            return Cell(point.value, "input", point.id, point.filed)
        if line.default is not None:
            model.defaults.append(f"{line.name} {quarter}")
            return Cell(float(line.default), "default")
        return Cell(None, "missing")
    if line.kind == "assumption":
        return Cell(None, "missing")  # V4.4
    return _formula(model, line, quarter)


def _fy_cell(model: Model, line: Line, column: str, pack) -> Cell:
    year = column[2:]
    quarters = [f"{year}Q{i}" for i in (1, 2, 3, 4)]
    values = [model.value(line.name, q) for q in quarters]
    if line.fy == "sum":
        return Cell(None if None in values else sum(values), "formula")
    if line.fy == "last":
        return Cell(values[-1], "formula")
    if line.fy == "input":
        point = pack.get(line.source, {}).get(column)
        return Cell(point.value, "input", point.id, point.filed) if point else Cell(None, "missing")
    if line.fy == "formula" and line.expr:
        return _formula(model, line, column)
    return Cell(None, "missing")


def _formula(model: Model, line: Line, column: str) -> Cell:
    fiscal = column.startswith("FY")
    index = None if fiscal else model.quarters.index(column)

    def read_line(name: str, lag: int):
        if lag == 0:
            return model.value(name, column)
        if fiscal or index - lag < 0:
            return None
        return model.value(name, model.quarters[index - lag])

    def read_fy(name: str):
        year = column[2:] if fiscal else column[:4]
        values = [model.value(name, f"{year}Q{i}") for i in (1, 2, 3, 4)]
        return None if None in values else sum(values)

    # A formula stays a formula when an input is missing: blank, never 0
    return Cell(evaluate(line.expr, Readers(read_line, read_fy, lambda name: None)), "formula")
