"""The model graph (V4 design doc §5.1): lines × periods from a template, evaluated in Python.

A template (templates/*.yaml) lists lines; each is
  input       a reported series from the data pack (blue, with its source id)
  formula     a DSL expression over other lines (black)
  assumption  a forecast driver (V4.4; blue, Assumptions tab)
and says how its fiscal-year column is made (`fy`): sum (flows), last (balances), input (the
reported FY: per-share lines), formula (ratios recomputed from FY totals) or none.

Columns are quarters, with an FY column after each Q4 of a complete year; lags count quarters
only. Forecast quarters (V4.4) follow the history: there a line uses its `forecast` expression
(`forecast_first` in the first forecast quarter, where a growth rate starts from its assumption),
else its history expression; an input without one is blank. `@name` reads a scalar assumption
(research/forecast.py sets them; the workbook's Assumptions tab). An FY column that includes
forecast quarters uses `fy_forecast` for a line whose reported FY is an input (EPS, shares).
`expr_for` decides a cell's expression for Python and the workbook alike.

A template's `forecast:` section is shorthand, expanded here into ordinary lines (so the workbook
shows every formula):
  growth: [x, …]          x = x[-4] × (1 + x_g) (x[-8] × (1 + x_g)² where last year's quarter is
                          missing: ABNB's 2025 GBV); x_g = x / x[-4] − 1 in history, @x_g.g0 in
                          the first forecast quarter, then x_g[-1] × @fade
  ratios: {r: band, …}    r = clamp(r[-4] + r_shift, @r.lo, @r.hi) (r[-8] where r[-4] is missing);
                          r_shift = r − r[-4] in history, @r.shift0 first, then r_shift[-1] ×
                          @fade (band: research.yaml ratio_bounds)

`templates/statements.yaml` (the condensed statements, §5.2) is read first, then the company's
template (its operating build), whose lines may replace statement lines by name.
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
    forecast: Expr | None = None  # forecast quarters
    forecast_first: Expr | None = None  # the first forecast quarter (a growth rate's start)
    fy_forecast: Expr | None = None  # an FY column with forecast quarters, for fy: input


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
    quarters: list[str]  # history, then forecast
    columns: list[str]  # quarters and FY columns, in sheet order
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    defaults: list[str] = field(default_factory=list)  # "line period": filled by `default`
    forecast: list[str] = field(default_factory=list)  # the forecast quarters
    assumptions: dict[str, float] = field(default_factory=dict)

    @property
    def history(self) -> list[str]:
        return [q for q in self.quarters if q not in self.forecast]

    def value(self, line: str, column: str) -> float | None:
        cell = self.cells.get((line, column))
        return cell.value if cell else None

    def line(self, name: str) -> Line:
        return next(x for x in self.lines if x.name == name)

    def to_json(self) -> dict:
        return {
            "ticker": self.ticker, "template": self.template, "as_of": self.as_of,
            "columns": self.columns, "forecast": self.forecast, "assumptions": self.assumptions,
            "lines": [{"name": x.name, "kind": x.kind, "label": x.label, "unit": x.unit,
                       "statement": x.statement, "fy": x.fy, "source": x.source,
                       "expr": x.expr.text if x.expr else None,
                       "forecast": x.forecast.text if x.forecast else None,
                       "forecast_first": x.forecast_first.text if x.forecast_first else None}
                      for x in self.lines],
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
    _expand_forecast(specs, template.get("forecast") or {})
    lines = [_line(n, s, binding.get("sources", {})) for n, s in specs.items()]
    labels = [x.label for x in lines]
    if len(set(labels)) != len(labels):
        twice = sorted({x for x in labels if labels.count(x) > 1})
        raise ValueError(f"{name}: lines share labels {twice} (a YAML comma in a label?)")
    _check_names(lines)
    for regime in ("history", "forecast"):
        _ordered(lines, regime)  # a cycle in either is a template error
    return lines, template


def _expand_forecast(specs: dict, rules: dict) -> None:
    """Growth drivers and ratios (see the module docstring) into lines, in place."""
    for x in rules.get("growth", []):
        specs[x] = dict(specs[x], forecast=f"first({x}[-4], {x}[-8] * (1 + {x}_g)) * (1 + {x}_g)")
        specs[f"{x}_g"] = {"kind": "formula", "expr": f"{x} / {x}[-4] - 1", "unit": "fraction",
                           "forecast_first": f"@{x}_g.g0", "forecast": f"{x}_g[-1] * @fade",
                           "statement": specs[x].get("statement", "operating"), "fy": "none",
                           "label": f"{specs[x].get('label', x.replace('_', ' '))} growth y/y"}
    for r in (rules.get("ratios") or {}):
        specs[r] = dict(specs[r], forecast=f"min(max(first({r}[-4], {r}[-8]) + {r}_shift, "
                                           f"@{r}.lo), @{r}.hi)")
        specs[f"{r}_shift"] = {"kind": "formula", "expr": f"{r} - {r}[-4]", "unit": "fraction",
                               "forecast_first": f"@{r}.shift0",
                               "forecast": f"{r}_shift[-1] * @fade",
                               "statement": specs[r].get("statement", "operating"), "fy": "none",
                               "label": f"{specs[r].get('label', r.replace('_', ' '))} change y/y"}


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
    def optional(key):
        return parse(str(spec[key])) if spec.get(key) is not None else None

    return Line(name, kind, spec.get("label", name.replace("_", " ")), spec["unit"],
                spec.get("statement", "operating"), fy,
                parse(spec["expr"]) if kind == "formula" else None, source,
                spec.get("fallback"), spec.get("default"), optional("forecast"),
                optional("forecast_first"), optional("fy_forecast"))


def _exprs(x: Line, regime: str = "all") -> list[Expr]:
    """A line's expressions: all, those evaluated in history columns, or in forecast ones."""
    if regime == "history":
        found = [x.expr]
    elif regime == "forecast":
        found = [x.forecast, x.forecast_first] if x.forecast or x.forecast_first else [x.expr]
    else:
        found = [x.expr, x.forecast, x.forecast_first, x.fy_forecast]
    return [e for e in found if e]


def _check_names(lines: list[Line]) -> None:
    names = {x.name for x in lines}
    for x in lines:
        unknown = set().union(*(e.lines for e in _exprs(x))) - names
        if unknown:
            raise ValueError(f"{x.name} reads unknown lines {sorted(unknown)}")


def _ordered(lines: list[Line], regime: str) -> list[Line]:
    """Evaluation order for history or forecast columns: a line after every line it reads in
    its own period. (In history take_rate = revenue / gross_bookings; in the forecast revenue =
    gross_bookings × take_rate: the two regimes order differently.)"""
    by_name = {x.name: x for x in lines}
    done: list[Line] = []
    placed: set[str] = set()
    visiting: set[str] = set()

    def place(x: Line):
        if x.name in placed:
            return
        if x.name in visiting:
            raise ValueError(f"cycle through {x.name}")
        visiting.add(x.name)
        for dep in sorted(set().union(*(e.same_period for e in _exprs(x, regime)))):
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
          pack: dict[str, dict[str, Point]], forecast: list[str] = (),
          assumptions: dict[str, float] | None = None) -> Model:
    """The model: every input from the data pack, every formula evaluated; forecast quarters
    (after `quarters`, the history) from the forecast expressions and `assumptions`."""
    lines, _ = load_template(template, ticker)
    model = Model(ticker, template, as_of, lines, list(quarters) + list(forecast),
                  columns_for(list(quarters) + list(forecast)), forecast=list(forecast),
                  assumptions=dict(assumptions or {}))
    evaluate_cells(model, pack)
    return model


def evaluate_cells(model: Model, pack) -> None:
    """(Re)compute every cell, in column order (a forecast reads earlier columns)."""
    model.cells.clear()
    model.defaults.clear()
    orders = {r: _ordered(model.lines, r) for r in ("history", "forecast")}
    for column in model.columns:
        fiscal = column.startswith("FY")
        for line in orders["forecast" if column in model.forecast else "history"]:
            model.cells[(line.name, column)] = (
                _fy_cell(model, line, column, pack) if fiscal else _cell(model, line, column, pack)
            )


def expr_for(model: Model, line: Line, column: str) -> Expr | str | None:
    """The cell's expression, or its FY rule ("sum", "last"), or None (an input or blank)."""
    if column.startswith("FY"):
        year = column[2:]
        forecast_year = any(q.startswith(year) for q in model.forecast)
        if line.fy == "input":
            return line.fy_forecast if forecast_year else None
        if line.fy in ("sum", "last"):
            return line.fy
        return line.expr if line.fy == "formula" else None
    if column in model.forecast:
        if column == model.forecast[0] and line.forecast_first:
            return line.forecast_first
        return line.forecast or (line.expr if line.kind == "formula" else None)
    return line.expr if line.kind == "formula" else None


def _cell(model: Model, line: Line, quarter: str, pack) -> Cell:
    if quarter in model.forecast:
        expr = expr_for(model, line, quarter)
        return _formula(model, line, quarter, expr) if expr else Cell(None, "missing")
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
        expr = expr_for(model, line, column)
        if expr:
            return _formula(model, line, column, expr)
        point = pack.get(line.source, {}).get(column)
        return Cell(point.value, "input", point.id, point.filed) if point else Cell(None, "missing")
    if line.fy == "formula" and line.expr:
        return _formula(model, line, column)
    return Cell(None, "missing")


def _formula(model: Model, line: Line, column: str, expr: Expr | None = None) -> Cell:
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
    readers = Readers(read_line, read_fy, model.assumptions.get)
    return Cell(evaluate(expr or line.expr, readers), "formula")
