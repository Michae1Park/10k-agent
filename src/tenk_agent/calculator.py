"""The calculator tool: all arithmetic goes through here, never through the model.

Either a named operation (pct_change, cagr, ratio, percent_of, sum, difference) or an
arithmetic expression over named inputs, e.g. "(new - old) / old * 100". Every result
comes back with the formula and inputs shown, so a calculated claim can be re-checked.
"""

import ast
import math
import operator
from dataclasses import dataclass

OPERATIONS = {
    "pct_change": (("old", "new"), "(new - old) / old * 100"),
    "cagr": (("start", "end", "years"), "((end / start) ** (1 / years) - 1) * 100"),
    "ratio": (("numerator", "denominator"), "numerator / denominator"),
    "percent_of": (("part", "total"), "part / total * 100"),
    "difference": (("a", "b"), "a - b"),
}

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCTIONS = {"abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt}


class CalculatorError(ValueError):
    pass


@dataclass
class Calculation:
    result: float
    formula: str
    inputs: dict[str, float]

    def to_dict(self) -> dict:
        return {"result": self.result, "formula": self.formula, "inputs": self.inputs}


def calculate(
    op: str | None = None, expression: str | None = None, inputs: dict | None = None
) -> Calculation:
    inputs = {name: _number(name, value) for name, value in (inputs or {}).items()}
    if op:
        if op == "sum":
            if not inputs:
                raise CalculatorError("sum needs at least one input")
            formula = " + ".join(inputs)
        elif op in OPERATIONS:
            required, formula = OPERATIONS[op]
            missing = [name for name in required if name not in inputs]
            if missing:
                raise CalculatorError(f"{op} needs inputs {list(required)}; missing {missing}")
        else:
            raise CalculatorError(f"unknown op {op!r}; use one of {[*OPERATIONS, 'sum']}")
    elif expression:
        formula = expression
    else:
        raise CalculatorError("give either op or expression")

    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise CalculatorError(f"invalid expression: {e.msg}") from e
    result = _eval(tree.body, inputs)
    if isinstance(result, complex) or not math.isfinite(result):
        raise CalculatorError(f"result is not a real number: {result}")
    return Calculation(round(result, 6), formula, inputs)


def _number(name: str, value) -> float:
    if isinstance(value, bool):
        raise CalculatorError(f"input {name!r} must be a number")
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError as e:
            raise CalculatorError(f"input {name!r} is not a number: {value!r}") from e
    if isinstance(value, int | float):
        return float(value)
    raise CalculatorError(f"input {name!r} must be a number")


def _eval(node: ast.AST, names: dict[str, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise CalculatorError(f"unknown name {node.id!r}; pass it in inputs")
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left, names), _eval(node.right, names)
        if isinstance(node.op, ast.Div) and right == 0:
            raise CalculatorError("division by zero")
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise CalculatorError("exponent too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, names))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        return float(_FUNCTIONS[node.func.id](*(_eval(a, names) for a in node.args)))
    raise CalculatorError(f"unsupported syntax: {ast.dump(node)[:60]}")
