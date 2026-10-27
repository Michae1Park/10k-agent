"""What each SEC form looks like to the preprocessor (V4 design doc §4.4): its Items in order,
where the financial statements live, and the names shown in chunk headers and citations.

Only the 10-K so far; the 10-Q and the earnings release (EX-99) follow in migration step M6.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FormSpec:
    form: str  # the dei:DocumentType value
    items: tuple[str, ...]  # Item keys, in the form's order (validator V2)
    statement_homes: frozenset[str]  # Items that may hold the statements (validator V6)
    item_names: dict[str, str] = field(default_factory=dict)  # for headers and citations


TEN_K = FormSpec(
    form="10-K",
    items=tuple("1 1A 1B 1C 2 3 4 5 6 7 7A 8 9 9A 9B 9C 10 11 12 13 14 15 16".split()),
    # Some filers put the statements in Item 15 (assembly step A3 moves them into Item 8).
    statement_homes=frozenset({"8", "15"}),
    item_names={
        "1": "Business",
        "1A": "Risk Factors",
        "7": "Management's Discussion and Analysis",
        "7A": "Market Risk",
        "8": "Financial Statements",
    },
)

FORMS: dict[str, FormSpec] = {spec.form: spec for spec in (TEN_K,)}


def form_spec(form: str) -> FormSpec:
    if form not in FORMS:
        raise KeyError(f"no FormSpec for {form!r}; known: {sorted(FORMS)}")
    return FORMS[form]
