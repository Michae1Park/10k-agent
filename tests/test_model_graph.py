"""The model graph, data pack and layer-2 checks (research/model/), on a synthetic company."""

import pytest
import yaml

from tenk_agent.research import datapack
from tenk_agent.research.datapack import Point
from tenk_agent.research.model import checks, graph

QUARTERS = ["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"]
TEMPLATE = {
    "lines": {
        "gb_a": {"kind": "input", "source": "kpi:gb.a", "unit": "USD millions"},
        "gb_b": {"kind": "input", "source": "kpi:gb.b", "unit": "USD millions"},
        "gross_bookings": {"kind": "input", "source": "kpi:gb", "unit": "USD millions"},
        "take_rate": {"kind": "formula", "expr": "revenue / gross_bookings", "unit": "fraction"},
        "gb_growth": {"kind": "formula", "expr": "gross_bookings / gross_bookings[-4] - 1",
                      "unit": "fraction", "fy": "none"},
    },
    "sums": {"gross_bookings": ["gb_a", "gb_b"]},
    "companies": {"TEST": None},
}
STATEMENTS = {"lines": {
    "revenue": {"kind": "input", "source": "q:revenue", "unit": "USD millions"},
    "total_assets": {"kind": "input", "source": "q:total_assets", "unit": "USD millions",
                     "fy": "last"},
    "equity_total": {"kind": "input", "source": "q:equity_total", "unit": "USD millions",
                     "fy": "last"},
    "mezzanine": {"kind": "input", "source": "q:mezzanine", "unit": "USD millions",
                  "fy": "last", "default": 0},
    "total_liabilities": {"kind": "input", "source": "q:total_liabilities",
                          "unit": "USD millions", "fy": "last"},
    "balance_gap": {"kind": "formula", "unit": "USD millions", "fy": "last",
                    "expr": "total_assets - total_liabilities - mezzanine - equity_total"},
}}


@pytest.fixture
def templates(tmp_path, monkeypatch):
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(TEMPLATE))
    (tmp_path / "statements.yaml").write_text(yaml.safe_dump(STATEMENTS))
    monkeypatch.setattr(graph, "TEMPLATES", tmp_path)


def pack(**changes) -> dict:
    def series(values, filed="2026-05-01"):
        return {q: Point(v, "USD millions", f"id:{q}", filed) for q, v in zip(QUARTERS, values,
                                                                              strict=False)}
    out = {
        "q:revenue": series([10, 11, 12, 13, 14]) | {"FY2025": Point(46, "USD millions", "fy",
                                                                     "2026-02-01")},
        "kpi:gb": series([100, 110, 120, 130, 140]),
        "kpi:gb.a": series([60, 66, 72, 78, 84]),
        "kpi:gb.b": series([40, 44, 48, 52, 56]),
        "q:total_assets": series([50] * 5),
        "q:total_liabilities": series([30] * 5),
        "q:equity_total": series([20] * 5),
        "q:mezzanine": {},
    }
    return out | changes


def model(p=None, as_of="2026-06-01"):
    return graph.build("TEST", "test", as_of, QUARTERS, p or pack())


def test_columns_and_formulas(templates):
    m = model()
    assert m.columns == [*QUARTERS[:4], "FY2025", "2026Q1"]
    assert m.value("take_rate", "2025Q2") == pytest.approx(0.1)
    assert m.value("revenue", "FY2025") == 46  # sum
    assert m.value("total_assets", "FY2025") == 50  # last
    assert m.value("take_rate", "FY2025") == pytest.approx(46 / 460)  # formula on FY totals
    assert m.value("gb_growth", "2026Q1") == pytest.approx(0.4)  # lag skips the FY column
    assert m.value("gb_growth", "2025Q4") is None and m.value("gb_growth", "FY2025") is None
    assert m.cells[("revenue", "2025Q1")].ref == "id:2025Q1"
    assert m.defaults == [f"mezzanine {q}" for q in QUARTERS]


def test_checks_pass_on_a_consistent_model(templates):
    p = pack()
    results = {c.name: c for c in checks.run(model(p), p, store=None)}
    assert {n: c.status for n, c in results.items()} == {
        "balance": "pass", "quarters_sum": "pass", "take_rate": "pass", "segments_sum": "pass",
        "no_future_input": "pass", "formula_only": "pass"}


@pytest.mark.parametrize("broken, check", [
    ({"q:equity_total": None}, "balance"),  # equity off by 5 in one quarter
    ({"kpi:gb.b": None}, "segments_sum"),
    ({"q:revenue": None}, "quarters_sum"),
])
def test_checks_fail_on_a_broken_model(templates, broken, check):
    p = pack()
    source = next(iter(broken))
    series = dict(p[source])
    if check == "quarters_sum":
        series["FY2025"] = Point(50, "USD millions", "fy", "2026-02-01")  # quarters sum to 46
    else:
        series["2025Q3"] = Point(series["2025Q3"].value + 5, "USD millions", "x", "2026-05-01")
    p[source] = series
    result = next(c for c in checks.run(model(p), p, store=None) if c.name == check)
    assert result.status == "fail" and result.failures


def test_no_future_input_fails_on_a_late_filing(templates):
    p = pack()
    p["q:revenue"] = dict(p["q:revenue"]) | {"2026Q1": Point(14, "USD millions", "x",
                                                             "2026-06-01")}
    result = checks.no_future_input(model(p, as_of="2026-06-01"))
    assert result.status == "fail" and result.failures == ["revenue 2026Q1 filed 2026-06-01"]


def test_missing_parts_skip_and_are_listed(templates):
    p = pack()
    p["kpi:gb.a"] = {q: v for q, v in p["kpi:gb.a"].items() if q != "2026Q1"}
    m = model(p)
    result = checks.segments_sum(m, TEMPLATE["sums"])
    assert result.status == "pass" and result.skipped == ["gross_bookings 2026Q1"]
    items = [e["item"] for e in checks.exceptions(m, [result])]
    assert "gb_a missing in 2026Q1" in items and any("skipped 1" in i for i in items)


def test_split_basis_follows_the_latest_filing():
    old, new = "2025-11-01", "2026-05-01"
    series = {"2025Q3": Point(33.0, "count millions", "a", old),
              "2025Q2": Point(815.0, "count millions", "b", new),
              "2026Q1": Point(794.0, "count millions", "c", new)}
    datapack._split_basis(series, [25.0], "shares")
    assert series["2025Q3"].value == 825.0 and "split_adjusted x25" in series["2025Q3"].derivation
    assert series["2026Q1"].value == 794.0
    eps = {"2025Q3": Point(84.0, "USD per share", "a", old),
           "2026Q1": Point(1.36, "USD per share", "c", new)}
    datapack._split_basis(eps, [25.0], "per_share")
    assert eps["2025Q3"].value == pytest.approx(3.36)
    before = {"2025Q3": Point(33.0, "count millions", "a", old),
              "2025Q2": Point(32.6, "count millions", "b", old)}
    datapack._split_basis(before, [25.0], "shares")  # an as-of before the split: unchanged
    assert before["2025Q3"].value == 33.0


def test_template_errors(templates, tmp_path):
    bad = dict(TEMPLATE, lines={**TEMPLATE["lines"],
                                "loop_a": {"kind": "formula", "expr": "loop_b", "unit": "x"},
                                "loop_b": {"kind": "formula", "expr": "loop_a", "unit": "x"}})
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="cycle"):
        graph.load_template("test", "TEST")
    with pytest.raises(ValueError, match="no binding"):
        graph.load_template("test", "OTHER")


def test_alternative_part_lists_take_the_first_fully_reported(templates):
    m = model()
    old = {"gross_bookings": [["gb_missing"], ["gb_a", "gb_b"]]}
    m.cells[("gb_missing", "2025Q1")] = graph.Cell(None, "missing")
    result = checks.segments_sum(m, old)
    assert result.status == "pass" and result.tested == len(m.columns)


def test_duplicate_labels_are_rejected(templates, tmp_path):
    bad = dict(TEMPLATE, lines={**TEMPLATE["lines"], "gb_c": {
        "kind": "input", "source": "kpi:gb.c", "unit": "USD millions", "label": "gb a"}})
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="share labels"):
        graph.load_template("test", "TEST")


def test_temporary_equity_tagged_per_class_of_stock_is_summed():
    class Facts:
        def facts(self, ticker, concepts, any_dims=False):
            axis = {datapack.CLASS_AXIS: "x:SeriesA"}
            return [
                {"start": None, "end": "2024-12-31", "dims": axis, "value": 180e6,
                 "filed": "2025-02-01", "id": "ixbrl:a:1"},
                {"start": None, "end": "2024-12-31", "dims": axis, "value": 186e6,
                 "filed": "2025-02-28", "id": "ixbrl:b:1"},  # the later copy wins
                {"start": None, "end": "2024-12-31", "dims": {datapack.CLASS_AXIS: "x:B",
                 "other": "y"}, "value": 1e9, "filed": "2025-02-28", "id": "no"},
            ]

    series = {"2024Q3": Point(1.0, "USD millions", "q", "2024-11-01")}
    datapack._by_class(Facts(), "CART", ["TemporaryEquity"], series)
    assert series["2024Q4"].value == 186.0 and series["2024Q4"].id == "ixbrl:b:1"
    assert series["2024Q3"].value == 1.0  # a reported value is kept
