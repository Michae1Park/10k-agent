"""The model's formula language (V4 design doc §5.1): one expression, two evaluators.

  gross_bookings * take_rate          arithmetic over lines of the same period
  trips[-4] * (1 + trips_g)           a line k quarters back (an Excel cell k quarter columns left)
  fy(revenue)                         the fiscal year's four quarters summed (FY columns)
  min(a, b) · max(a, b) · if_(c, a, b)
  first(a, b)                         a, or b where a can't be computed (an input missing, a zero
                                      divisor); Excel: IFERROR(IF(COUNT(a's cells)=n, a, b), b)
  @fade                               an assumption (a cell on the Assumptions tab)

Parsed with Python's `ast`, never `eval`: anything else is a DslError. `evaluate` computes a
value; `to_excel` writes the same expression as an Excel formula, so the workbook recomputes
what Python did (the parity test, tests/test_model_workbook.py).
"""

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

FUNCTIONS = {"min": "MIN", "max": "MAX", "if_": "IF", "abs": "ABS"}
OPERATORS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^"}
COMPARE = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "=", ast.NotEq: "<>"}
BINARY = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
          ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a**b}
ASSUMPTION = re.compile(r"@([A-Za-z_][\w.]*)")


class DslError(ValueError):
    pass


@dataclass(frozen=True)
class Expr:
    text: str
    tree: ast.expr

    @property
    def lines(self) -> set[str]:
        """The lines it reads (same period or lagged), for ordering the graph."""
        return {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)
                and n.id not in (*FUNCTIONS, "fy", "first") and not n.id.startswith("A__")}

    @property
    def same_period(self) -> set[str]:
        """Lines read at the expression's own period (lags and fy() excluded): the edges that
        must not form a cycle."""
        out: set[str] = set()

        def walk(node, lagged):
            if isinstance(node, ast.Subscript):
                walk(node.value, True)
                return
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "fy":
                return
            if isinstance(node, ast.Name) and not lagged and node.id in self.lines:
                out.add(node.id)
            for child in ast.iter_child_nodes(node):
                walk(child, lagged)

        walk(self.tree, False)
        return out

    @property
    def assumptions(self) -> set[str]:
        return {n.id[3:].replace("__", ".") for n in ast.walk(self.tree)
                if isinstance(n, ast.Name) and n.id.startswith("A__")}


def parse(text: str) -> Expr:
    """An expression, checked: only the constructs above."""
    source = ASSUMPTION.sub(lambda m: "A__" + m[1].replace(".", "__"), text.strip())
    try:
        tree = ast.parse(source, mode="eval").body
    except SyntaxError as e:
        raise DslError(f"{text!r}: {e.msg}") from None
    for node in ast.walk(tree):
        _check(node, text)
    return Expr(text, tree)


def _check(node: ast.AST, text: str) -> None:
    allowed = (ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Call, ast.Subscript,
               ast.Compare, ast.Load, ast.USub, ast.UAdd, *OPERATORS, *COMPARE)
    if not isinstance(node, allowed):
        raise DslError(f"{text!r}: {type(node).__name__} is not allowed")
    if isinstance(node, ast.Constant) and not isinstance(node.value, int | float):
        raise DslError(f"{text!r}: only numbers")
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None)
        if name not in (*FUNCTIONS, "fy", "first") or node.keywords:
            raise DslError(f"{text!r}: unknown function {name!r}")
        if name == "fy" and (len(node.args) != 1 or not isinstance(node.args[0], ast.Name)):
            raise DslError(f"{text!r}: fy() takes one line")
        if name == "first" and len(node.args) != 2:
            raise DslError(f"{text!r}: first(a, b)")
        if name == "if_" and len(node.args) != 3:
            raise DslError(f"{text!r}: if_(condition, then, else)")
    if isinstance(node, ast.Subscript):
        lag = node.slice
        if not (isinstance(node.value, ast.Name) and isinstance(lag, ast.UnaryOp)
                and isinstance(lag.op, ast.USub) and isinstance(lag.operand, ast.Constant)
                and isinstance(lag.operand.value, int) and lag.operand.value > 0):
            raise DslError(f"{text!r}: a lag is line[-k], k a positive whole number")


# Readers: how an evaluator looks a line up. line(name, lag) → value; fy(name) → the year's sum;
# assumption(name) → value. The graph supplies them per period.
@dataclass
class Readers:
    line: Callable[[str, int], float | None]
    fy: Callable[[str], float | None]
    assumption: Callable[[str], float | None]


def evaluate(expr: Expr, read: Readers) -> float | None:
    """The value, or None when an input is missing (shown blank, never as 0)."""
    try:
        return _eval(expr.tree, read)
    except _Missing:
        return None
    except ZeroDivisionError:
        return None


class _Missing(Exception):
    pass


def _eval(node, read: Readers) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id.startswith("A__"):
            return _need(read.assumption(node.id[3:].replace("__", ".")))
        return _need(read.line(node.id, 0))
    if isinstance(node, ast.Subscript):
        return _need(read.line(node.value.id, node.slice.operand.value))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, read)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, read), _eval(node.right, read)
        return BINARY[type(node.op)](a, b)
    if isinstance(node, ast.Compare):
        a = _eval(node.left, read)
        b = _eval(node.comparators[0], read)
        return float({ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b, ast.GtE: a >= b,
                      ast.Eq: a == b, ast.NotEq: a != b}[type(node.ops[0])])
    name = node.func.id
    if name == "fy":
        return _need(read.fy(node.args[0].id))
    if name == "if_":
        return _eval(node.args[1] if _eval(node.args[0], read) else node.args[2], read)
    if name == "first":
        try:
            return _eval(node.args[0], read)
        except (_Missing, ZeroDivisionError):
            return _eval(node.args[1], read)
    args = [_eval(a, read) for a in node.args]
    return {"min": min, "max": max, "abs": abs}[name](*args)


def _need(value):
    if value is None:
        raise _Missing
    return float(value)


# Excel: cell(name, lag) → "C12"; fy(name) → "C12:F12"; assumption(name) → "Assumptions!$C$4".
@dataclass
class Cells:
    cell: Callable[[str, int], str | None]
    fy: Callable[[str], str | None]
    assumption: Callable[[str], str]


def to_excel(expr: Expr, cells: Cells) -> str | None:
    """The formula ("=C12*C30"), or None when a lag reaches before the first column."""
    try:
        return "=" + _xl(expr.tree, cells, top=True)
    except _Missing:
        return None


def _xl(node, cells: Cells, top: bool = False) -> str:
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        if node.id.startswith("A__"):
            return cells.assumption(node.id[3:].replace("__", "."))
        return _ref(cells.cell(node.id, 0))
    if isinstance(node, ast.Subscript):
        return _ref(cells.cell(node.value.id, node.slice.operand.value))
    if isinstance(node, ast.UnaryOp):
        return ("-" if isinstance(node.op, ast.USub) else "") + _xl(node.operand, cells)
    if isinstance(node, ast.BinOp):
        text = f"{_xl(node.left, cells)}{OPERATORS[type(node.op)]}{_xl(node.right, cells)}"
        return text if top else f"({text})"
    if isinstance(node, ast.Compare):
        return (f"{_xl(node.left, cells)}{COMPARE[type(node.ops[0])]}"
                f"{_xl(node.comparators[0], cells)}")
    name = node.func.id
    if name == "fy":
        return f"SUM({_ref(cells.fy(node.args[0].id))})"
    if name == "first":
        try:
            b = _xl(node.args[1], cells, top=True)
        except _Missing:  # b reads before the first column: only a can give a value
            return _xl(node.args[0], cells)
        try:
            a = _xl(node.args[0], cells, top=True)
        except _Missing:  # a reads before the first column: Python falls back to b as well
            return b
        refs = _refs(node.args[0], cells)
        return f"IFERROR(IF(COUNT({','.join(refs)})={len(refs)},{a},{b}),{b})"
    return f"{FUNCTIONS[name]}({','.join(_xl(a, cells, top=True) for a in node.args)})"


def _refs(node, cells: Cells) -> list[str]:
    """The cells an expression reads (lines and assumptions), for first()'s COUNT."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript):
            out.append(cells.cell(n.value.id, n.slice.operand.value))
        elif isinstance(n, ast.Name) and n.id.startswith("A__"):
            out.append(cells.assumption(n.id[3:].replace("__", ".")))
        elif isinstance(n, ast.Name) and n.id not in (*FUNCTIONS, "fy", "first") and not any(
                isinstance(p, ast.Subscript) and p.value is n for p in ast.walk(node)):
            out.append(cells.cell(n.id, 0))
    return list(dict.fromkeys(out))


def _ref(ref: str | None) -> str:
    if ref is None:
        raise _Missing
    return ref
