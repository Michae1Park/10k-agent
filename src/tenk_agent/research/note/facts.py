"""Facts: every figure the note can show, registered once with its source (design doc §10.2).

Code computes and formats every number; the writer only places facts (by F-id) and writes
prose around them. The facts become the evidence log (E-rows), and the number trace
(research/note/trace.py) checks every number in the note against them.
"""

from dataclasses import asdict, dataclass, field

SCALE_WORDS = {"USD billions": (1e3, "B"), "USD millions": (1.0, "M")}


@dataclass
class Fact:
    id: str  # F-12
    label: str  # "2027E adj. EBITDA"
    value: float | str
    unit: str  # USD millions, USD, percent, x, count millions, text
    source: str  # a citable id (model:, kpi:, q:, px:, guide:, …) or "computed from F-3, F-4"
    period: str = ""
    filed: str = ""

    def text(self, digits: int | None = None) -> str:
        """The figure as the note prints it."""
        v = self.value
        if isinstance(v, str):
            return v
        if self.unit == "USD millions":
            sign, v = ("-" if v < 0 else ""), abs(v)
            return (f"{sign}${v / 1e3:,.{1 if digits is None else digits}f}B" if v >= 1e3
                    else f"{sign}${v:,.0f}M")
        if self.unit == "USD":
            return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"
        if self.unit == "USD per share":
            return f"${v:,.2f}"
        if self.unit == "percent":
            signed = any(w in self.label for w in ("change", "variance", "return"))
            return f"{v:+.1f}%" if signed else f"{v:.1f}%"
        if self.unit == "points":
            return f"{v:+.1f} pts"
        if self.unit == "weight":
            return f"{v * 100:.0f}%"
        if self.unit == "count":
            return f"{v:,.0f}"
        if self.unit == "x":
            return f"{v:.1f}x"
        if self.unit == "count millions":
            return f"{v / 1e3:,.2f}B" if abs(v) >= 1e3 else f"{v:,.1f}M"
        return f"{v:,.2f}"


@dataclass
class Facts:
    items: list[Fact] = field(default_factory=list)

    def add(self, label: str, value, unit: str, source: str, period: str = "",
            filed: str = "") -> Fact:
        fact = Fact(f"F-{len(self.items) + 1}", label, value, unit, source, period, filed)
        self.items.append(fact)
        return fact

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self.items if f.id == fact_id), None)

    def listing(self) -> str:
        """The fact list a writer reads: id, label, the figure as printed, and its source."""
        return "\n".join(f"{f.id} | {f.label} | {f.text()} | {f.source}" for f in self.items)

    def to_json(self) -> list[dict]:
        return [asdict(f) for f in self.items]
