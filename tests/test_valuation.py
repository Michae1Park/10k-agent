"""Valuation (research/valuation.py): DCF behaviour, ranking and determinism (layer 4)."""

import pytest

from tenk_agent.research import valuation

V = {"annual_fade": 0.9, "dcf_years": 10}


class Model:
    """The few things dcf() reads: forecast quarters and line values."""

    ticker = "TEST"

    def __init__(self, ufcf_per_quarter: float, growth: float = 0.1):
        self.forecast = [f"{y}Q{q}" for y in (2026, 2027) for q in (1, 2, 3, 4)][2:]
        self.values = {}
        for q in self.forecast:
            self.values |= {("adj_ebitda", q): ufcf_per_quarter + 10, ("sbc", q): 10.0,
                            ("operating_income", q): 0.0, ("capex", q): 0.0}
        self.values |= {("revenue", "FY2026"): 100.0, ("revenue", "FY2027"): 100 * (1 + growth)}

    def value(self, line, column):
        return self.values.get((line, column))


@pytest.mark.parametrize("flow", [50.0, 5.0])
def test_dcf_value_falls_as_wacc_rises(flow):
    m = Model(flow)
    evs = [valuation.dcf(m, "2026-09-01", w, 0.03, 0.2, V)["ev"] for w in (0.07, 0.08, 0.09, 0.1)]
    assert evs == sorted(evs, reverse=True) and evs[-1] > 0


def test_negative_cash_flows_give_a_negative_dcf():
    assert valuation.dcf(Model(-5.0), "2026-09-01", 0.08, 0.03, 0.2, V)["ev"] < 0


def test_terminal_value_share_grows_with_terminal_growth():
    m = Model(50.0)
    shares = [valuation.dcf(m, "2026-09-01", 0.09, g, 0.2, V)["terminal_share"]
              for g in (0.025, 0.03, 0.035)]
    assert shares == sorted(shares) and 0 < shares[0] < 1


def test_relative_ratings_by_thirds():
    returns = {t: {"return": r} for t, r in zip("ABCDEFGH", (0.4, 0.3, 0.2, 0.1, 0, -0.1, -0.2,
                                                              -0.3), strict=True)}
    assert valuation.rank(returns) == {"A": "Buy", "B": "Buy", "C": "Hold", "D": "Hold",
                                       "E": "Hold", "F": "Hold", "G": "Sell", "H": "Sell"}


def test_rounding_is_deterministic():
    assert valuation._rounded({"a": [0.1 + 0.2, 1 / 3]}) == {"a": [0.3, 0.3333333333]}


def test_market_value():
    m = valuation.Market(price=10.0, price_date="2026-08-31", shares=100.0, debt=300.0,
                         cash=50.0, interest=None, refs={})
    assert (m.market_cap, m.net_debt, m.ev) == (1000.0, 250.0, 1250.0)
