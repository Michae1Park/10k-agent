"""What each SEC form looks like to the preprocessor (V4 design doc §4.4): its Items in order,
where the financial statements live, and the names shown in chunk headers and citations.

  10-K     Items once each; the LLM structure agent labels it (V0)
  10-Q     Items repeat per Part, so keys carry it: I.1 … I.4, II.1 … II.6; labeled by rules
  release  an 8-K's EX-99 exhibits (form "ER"): no Items, one pseudo-Item per exhibit
           (EX99, EX99.2, …); labeled by rules (design doc §4.4)
  proxy    a DEF 14A (form "DEF14A"): no Items, one pseudo-Item (PROXY) over the whole
           document; labeled by the release rules
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

TEN_Q = FormSpec(
    form="10-Q",
    items=tuple("I.1 I.2 I.3 I.4 II.1 II.1A II.2 II.3 II.4 II.5 II.6".split()),
    statement_homes=frozenset({"I.1"}),
    item_names={
        "I.1": "Financial Statements",
        "I.2": "Management's Discussion and Analysis",
        "I.3": "Market Risk",
        "I.4": "Controls and Procedures",
        "II.1": "Legal Proceedings",
        "II.1A": "Risk Factors",
        "II.2": "Unregistered Sales of Equity and Use of Proceeds",
        "II.5": "Other Information",
    },
)

RELEASE = FormSpec(
    form="ER",
    items=("EX99", "EX99.2", "EX99.3", "EX99.4"),
    statement_homes=frozenset({"EX99"}),
    item_names={"EX99": "Earnings Release", "EX99.2": "Shareholder Letter"},
)

PROXY = FormSpec(
    form="DEF14A",
    items=("PROXY",),
    statement_homes=frozenset(),
    item_names={"PROXY": "Proxy Statement"},
)

FORMS: dict[str, FormSpec] = {spec.form: spec for spec in (TEN_K, TEN_Q, RELEASE, PROXY)}


def form_spec(form: str) -> FormSpec:
    if form not in FORMS:
        raise KeyError(f"no FormSpec for {form!r}; known: {sorted(FORMS)}")
    return FORMS[form]
