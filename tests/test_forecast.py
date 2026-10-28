"""Forecast columns, assumptions and guidance anchors (research/model/graph.py and
research/forecast.py)."""

import shutil

import pytest
import yaml

from tenk_agent.research import forecast as fc
from tenk_agent.research.datapack import Point
from tenk_agent.research.export import workbook
from tenk_agent.research.model import checks, graph

HISTORY = ["2024Q1", "2024Q2", "2024Q3", "2024Q4",
           "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
FUTURE = ["2026Q3", "2026Q4", "2027Q1"]
TEMPLATE = {
    "lines": {
        "volume": {"kind": "input", "source": "kpi:volume", "unit": "count millions"},
        "price": {"kind": "input", "source": "kpi:price", "unit": "USD"},
        "gross_bookings": {"kind": "input", "source": "kpi:gb", "unit": "USD millions",
                           "forecast": "volume * price"},
        "revenue": {"kind": "input", "source": "q:revenue", "unit": "USD millions",
                    "forecast": "gross_bookings * take_rate"},
        "take_rate": {"kind": "formula", "expr": "revenue / gross_bookings", "unit": "fraction"},
    },
    "forecast": {"growth": ["volume", "price"], "ratios": {"take_rate": "take_rate"}},
    "anchors": [{"guide": "revenue", "line": "revenue", "basis": "level",
                 "solve": "take_rate.shift0"}],
    "companies": {"TEST": None},
}
SETTINGS = {"forecast": {"fade": 0.9, "ratio_bounds": {"take_rate": 0.005, "margin": 0.02},
                         "tax_rate_bounds": [0.1, 0.3], "guidance_point": "mid"}}


@pytest.fixture
def templates(tmp_path, monkeypatch):
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(TEMPLATE))
    (tmp_path / "statements.yaml").write_text(yaml.safe_dump({"lines": {}}))
    monkeypatch.setattr(graph, "TEMPLATES", tmp_path)


def series(values, unit="USD millions"):
    return {q: Point(v, unit, f"id:{q}", "2026-08-01") for q, v in zip(HISTORY, values,
                                                                       strict=True) if v}


def pack(gb_missing=False):
    volume = [9, 10, 11, 12, 10, 11, 12, 13, 11, 12.1]
    price = [95, 95, 95, 95, 100, 100, 100, 100, 105, 105]
    gb = [v * p for v, p in zip(volume, price, strict=True)]
    if gb_missing:
        gb[6] = None  # 2025Q3 not extracted (ABNB)
    return {"kpi:volume": series(volume), "kpi:price": series(price), "kpi:gb": series(gb),
            "q:revenue": series([0.1 * g if g else 120.0 for g in gb])}


def model(p=None):
    m = graph.build("TEST", "test", "2026-09-01", HISTORY, p or pack(), FUTURE)
    m.assumptions = fc.default_assumptions(m, SETTINGS)
    graph.evaluate_cells(m, p or pack())
    return m


def test_growth_drivers_start_at_the_last_yoy_and_fade(templates):
    m = model()
    assert m.assumptions["volume_g.g0"] == pytest.approx(0.1)  # 2026Q2 vs 2025Q2
    assert m.value("volume_g", "2026Q3") == pytest.approx(0.1)
    assert m.value("volume_g", "2026Q4") == pytest.approx(0.09)
    assert m.value("volume", "2026Q3") == pytest.approx(12 * 1.1)
    assert m.value("gross_bookings", "2026Q3") == pytest.approx(12 * 1.1 * 100 * 1.05)


def test_ratios_hold_last_years_quarter_within_bounds(templates):
    m = model()
    assert m.value("take_rate", "2026Q3") == pytest.approx(0.1)
    m.assumptions["take_rate.shift0"] = 0.5  # far outside: clamped to the 8-quarter max + 50 bps
    graph.evaluate_cells(m, pack())
    assert m.value("take_rate", "2026Q3") == pytest.approx(0.105)


def test_a_missing_prior_year_quarter_steps_from_two_years_back(templates):
    p = pack(gb_missing=True)
    m = model(p)
    assert m.value("gross_bookings", "2025Q3") is None
    assert m.value("take_rate", "2026Q3") is not None  # first(take_rate[-4], take_rate[-8])


def test_forecast_columns_hold_formulas_only(templates):
    m = model()
    result = checks.formula_only(m)
    assert result.status == "pass" and result.tested > 0
    m.cells[("volume", "2026Q3")] = graph.Cell(1.0, "input")
    assert checks.formula_only(m).status == "fail"


def test_an_anchor_hits_the_guided_midpoint(templates):
    class Store:
        def guidance(self, ticker):
            return [{"metric": "revenue", "segment": "total", "target_period": "2026Q3",
                     "low": 130.0, "high": 134.0, "issued_in": "2026Q2"}]

    m = model()
    result = fc.Forecast(m, pack(), dict(m.assumptions))
    anchors = fc.anchor(m, pack(), Store(), TEMPLATE["anchors"], SETTINGS, result)
    assert anchors[0].reached and m.value("revenue", "2026Q3") == pytest.approx(132.0)
    assert m.assumptions["take_rate.shift0"] == pytest.approx(anchors[0].after)


def test_an_anchor_out_of_bounds_is_logged_not_forced(templates):
    class Store:
        def guidance(self, ticker):
            return [{"metric": "revenue", "segment": "total", "target_period": "2026Q3",
                     "low": 500.0, "high": 500.0, "issued_in": "2026Q2"}]

    m = model()
    result = fc.Forecast(m, pack(), dict(m.assumptions))
    anchors = fc.anchor(m, pack(), Store(), TEMPLATE["anchors"], SETTINGS, result)
    assert not anchors[0].reached and m.value("take_rate", "2026Q3") <= 0.105 + 1e-12
    assert any("not reached" in e["item"] for e in result.exceptions)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="needs LibreOffice")
def test_forecast_parity_with_libreoffice(templates, tmp_path):
    m = model(pack(gb_missing=True))
    checked, differ = workbook.parity(m, workbook.write(m, tmp_path / "f.xlsx"))
    assert differ == [] and checked > 40
