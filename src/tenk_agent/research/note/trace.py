"""Number trace and lint (design doc §10.2): every number in the note's text must be a fact.

A number traces when it equals a fact's value at the precision it is written, after units
("$9.7B" traces to 9,712 USD millions; "+3.2%" to 3.21 percent). Years, quarters, section and
list numbers, and F-ids are not figures. Untraced numbers are the lint's errors: the writer
gets one repair turn, then they stay as exceptions (never silently dropped).
"""

import re
from dataclasses import dataclass

from tenk_agent.research.note.facts import Facts

NUMBER = re.compile(r"(?<![\w.])(?P<sign>[+\-−])?\$?"
                    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
                    r"(?P<suffix>\s?(?:%|pts?|bps|x|B|M|bn|billion|million)\b|%)?")
NOT_FIGURES = re.compile(r"\b\d{4}-\d\d-\d\d\b|\b(?:FY)?(?:19|20)\d\d(?:Q[1-4]|E|A)?\b"
                         r"|\bQ[1-4]\b|\bF-\d+\b|\bX-\d+\b|\bE-\d+\b|\bsections? \d+(?:\.\d+)?\b"
                         r"|\b\d+(?:\.\d+)?/\d+\b|\b\d\.\d+\s+(?=[A-Z])|^\s*\d+\.\s")


@dataclass
class Untraced:
    text: str
    where: str


def figures(text: str) -> list[tuple[str, float, str]]:
    """(as written, value in the fact's base units, kind) for each figure in a text."""
    cleaned = NOT_FIGURES.sub(lambda m: " " * len(m.group(0)), text)
    out = []
    for m in NUMBER.finditer(cleaned):
        raw = m.group(0).strip()
        value = float(m["num"].replace(",", ""))
        if m["sign"] in ("-", "−"):
            value = -value
        suffix = (m["suffix"] or "").strip().lower()
        if suffix in ("b", "bn", "billion"):
            out.append((raw, value * 1e3, "money_millions"))
        elif suffix in ("m", "million"):
            out.append((raw, value, "money_millions"))
        elif suffix == "%":
            out.append((raw, value, "percent"))
        elif suffix in ("pt", "pts"):
            out.append((raw, value, "points"))
        elif suffix == "bps":
            out.append((raw, value / 100, "points"))
        elif suffix == "x":
            out.append((raw, value, "x"))
        else:
            out.append((raw, value, "plain"))
    return out


def _decimals(raw: str) -> int:
    digits = re.search(r"\d[\d,]*(?:\.(\d+))?", raw)
    return len(digits.group(1) or "") if digits else 0


def traces(raw: str, value: float, kind: str, facts: Facts) -> bool:
    slack = 0.5 * 10 ** -_decimals(raw)
    for f in facts.items:
        if isinstance(f.value, str):
            continue
        candidates = [f.value]
        if kind == "money_millions" and f.unit in ("USD millions", "count millions"):
            scale = 1e3 if raw.rstrip().endswith(("B", "bn", "billion")) else 1.0
            if abs(abs(value) - abs(f.value)) <= slack * scale:
                return True
            continue
        if kind == "points" and f.unit not in ("percent", "points"):
            continue
        if kind == "plain" and f.unit in ("USD millions", "count millions"):
            candidates += [f.value / 1e3]
        if f.unit == "weight":  # stored as a fraction, printed as a percentage
            candidates = [f.value * 100]
        if any(abs(abs(value) - abs(c)) <= slack + 1e-9 for c in candidates):
            return True
    return False


def check(text: str, facts: Facts, where: str) -> list[Untraced]:
    """The numbers in a text that trace to no fact."""
    return [Untraced(raw, where) for raw, value, kind in figures(text)
            if not traces(raw, value, kind, facts)]
