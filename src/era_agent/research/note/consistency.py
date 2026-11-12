"""Consistency check before export (spec §2.3 requirement 5): code, not the LLM, checks the note.

  rating_thresholds  the draft rating matches the return thresholds in research.yaml (spec §3.3)
  section1_numbers   every number in section 1 equals the same number in its section table:
                     the price target, return and rating as section 6 prints them, and each
                     figure in the takeaways as a code-built table or line prints it
  debated_drivers    every debated driver (section 3) has a row in the section 7 sensitivities
  guidance_in_2_2    a guidance event (S2) is matched by guided lines in section 2.2
  open_issues        every warning and error in the numbered exception log is listed, by its
                     X-id, in section 10 (run after numbering: `open_issues`)

A failed check becomes an exception row (spec §2.3 requirement 6); the pipeline gives the
takeaways one repair turn, and never rewrites the other sections.
"""

from dataclasses import dataclass, field

from era_agent.research.note import trace
from era_agent.research.note.facts import Facts
from era_agent.research.note.sections import NO_GUIDANCE, Section, Slot


@dataclass
class Result:
    name: str
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def text_of(section: Section) -> str:
    return "\n".join(b.text if isinstance(b, Slot) else b for b in section.blocks)


def rating_for(ret: float, thresholds: dict) -> str:
    return "Buy" if ret >= thresholds["buy"] else "Sell" if ret <= thresholds["sell"] else "Hold"


def check(sections: list[Section], facts: Facts, valuation: dict, thresholds: dict,
          debates: list[dict], sensitivities: list[dict], events: list[dict]) -> list[Result]:
    """The checks before the exception log is numbered (all but open_issues)."""
    by_number = {s.number: s for s in sections}
    out = []

    r = Result("rating_thresholds")
    expected = rating_for(valuation["return"], thresholds)
    if valuation["rating"] != expected:
        r.failures.append(f"rating {valuation['rating']} but a {valuation['return']:+.1%} "
                          f"return gives {expected}")
    out.append(r)

    r = Result("section1_numbers")
    s1, s6 = by_number.get(1), by_number.get(6)
    s6_text = text_of(s6) if s6 else ""
    for label in ("12-month price target", "Implied return", "Draft rating"):
        f = next((x for x in facts.items if x.label == label), None)
        if f is not None and f.text() not in s6_text:
            r.failures.append(f"section 1 {label} {f.text()} is not in section 6")
    printed = "\n".join(b for s in sections for b in s.blocks if isinstance(b, str))
    shown = Facts([f for f in facts.items if not isinstance(f.value, str)
                   and f.text() in printed])
    for b in (s1.blocks if s1 else []):
        if isinstance(b, Slot) and b.text:
            r.failures += [f"takeaway figure {u.text} is in no section table"
                           for u in trace.check(b.text, shown, b.key)]
    out.append(r)

    r = Result("debated_drivers")
    covered = {x["driver"] for x in sensitivities}
    r.failures += [f"debated driver {d['driver']} has no section 7 sensitivity"
                   for d in debates if d.get("driver") and d["driver"] not in covered]
    out.append(r)

    r = Result("guidance_in_2_2")
    s2 = text_of(by_number[2]) if 2 in by_number else ""
    guided = [e for e in events if e.get("type") == "guidance"]
    if guided and NO_GUIDANCE in s2:
        r.failures += [f"guidance event not in 2.2: {e['summary']}" for e in guided]
    out.append(r)

    return out


def open_issues(section10: Section, numbered: list[dict]) -> Result:
    text = text_of(section10)
    return Result("open_issues", [f"open issue {x['id']} not in section 10" for x in numbered
                                  if x["severity"] in ("warning", "error")
                                  and x["id"] not in text])


def as_exceptions(results: list[Result]) -> list[dict]:
    return [{"severity": "error", "item": f"consistency {r.name}: {failure}"}
            for r in results for failure in r.failures]
