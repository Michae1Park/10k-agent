"""S7 valuation (V4 design doc §6.2): market value, multiples, DCF, price target and rating.

  taskset -c 16-31 uv run python -m tenk_agent.research.valuation UBER BKNG 2026-09-01 [--all]

  market cap, EV  last close before the as-of × the latest diluted weighted-average shares;
                  EV = market cap + debt − cash − short-term investments (leases, equity stakes
                  and customer funds not counted: listed as exceptions)
  multiple PT     target multiple × 2027E adj. EBITDA → EV − net debt → per share; target = the
                  sector peers' median EV / 2027E adj. EBITDA, each peer's own model at the same
                  as-of with defaults (cached); bear/bull ∓/± 15%; outside the peer range flagged
                  (also shown: EV / (EBITDA − SBC), P/E, FCF yield)
  DCF             unlevered FCF = adj. EBITDA − SBC − tax on positive EBIT − capex (ΔNWC 0): the
                  model's forecast quarters, then years grown at the model's last annual revenue
                  growth fading by `annual_fade` a year, to `dcf_years`; Gordon terminal value;
                  mid-period discounting from the as-of. Ke = DGS10 + β × ERP (β: 60 monthly
                  returns vs SPY; one ERP, Damodaran's latest); WACC = Ke below a 10% debt
                  share, else weighted with Kd = interest / debt after tax. Grid: WACC ± 1 pt ×
                  terminal growth 2.5–3.5%; the terminal value's share of EV
  price target    0.6 × multiple PT + 0.4 × DCF PT per scenario, 25/50/25 weighted; a DCF whose
                  EV is not positive (unlevered FCF negative over the horizon: TRIP's continuing
                  operations) is no price: the multiple alone, an exception
  rating          return = PT / price − 1: Buy ≥ +15%, Sell ≤ −10%, else Hold; --all also ranks
                  the eight (top third Buy, bottom third Sell)

Every input is read at the as-of and carries its id (px:, macro:, q:, ixbrl:); identical inputs
give a byte-identical valuation.json (layer 4).
"""

import argparse
import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from tenk_agent.asof import AsOf
from tenk_agent.corpus import companies_path
from tenk_agent.research import datapack
from tenk_agent.research.forecast import Forecast, forecast, knobs, scenarios
from tenk_agent.research.model.graph import Model
from tenk_agent.store import Store

MARKET_SOURCES = {"q:debt", "q:debt_current", "q:short_term_investments", "q:interest_expense",
                  "q:cash"}


@dataclass
class Market:
    price: float
    price_date: str
    shares: float  # millions, diluted weighted average, the latest reported quarter
    debt: float
    cash: float  # cash and short-term investments
    interest: float | None  # trailing four quarters
    refs: dict[str, str]
    exceptions: list[str] = field(default_factory=list)

    @property
    def market_cap(self) -> float:
        return self.price * self.shares

    @property
    def net_debt(self) -> float:
        return self.debt - self.cash

    @property
    def ev(self) -> float:
        return self.market_cap + self.net_debt


def sector_of(ticker: str) -> str:
    return yaml.safe_load(companies_path().read_text())["companies"][ticker]["sector"]


def peers_of(ticker: str) -> list[str]:
    companies = yaml.safe_load(companies_path().read_text())["companies"]
    return sorted(t for t, c in companies.items() if c["sector"] == sector_of(ticker))


# --- Inputs --------------------------------------------------------------------------------


def market(store: Store, model: Model) -> Market:
    ticker = model.ticker
    prices = store.series(f"px:{ticker}:close")
    if not prices:
        raise ValueError(f"no price for {ticker} before {model.as_of}")
    day, price = prices[-1]
    shares_q = next(q for q in reversed(model.history) if model.value("shares_diluted", q))
    pack = datapack.build(store, ticker, MARKET_SOURCES)
    last = model.history[-1]

    def balance(source: str) -> tuple[float, str | None]:
        series = pack[source]
        q = next((q for q in reversed(model.history) if q in series), None)
        return (series[q].value, series[q].id) if q else (0.0, None)

    debt, debt_id = balance("q:debt")
    current, current_id = balance("q:debt_current")
    exceptions = []
    if debt_id and ":LongTermDebtNoncurrent:" in debt_id:  # only the noncurrent part tagged
        debt += current
    cash, cash_id = balance("q:cash")
    sti, sti_id = balance("q:short_term_investments")
    interest = [pack["q:interest_expense"][q].value for q in model.history[-4:]
                if q in pack["q:interest_expense"]]
    if not debt_id:
        exceptions.append(f"no debt tagged for {ticker} as of {last}: taken as 0")
    exceptions.append("EV excludes leases, equity stakes and customer funds")
    return Market(price, day, model.value("shares_diluted", shares_q), debt, cash + sti,
                  sum(interest) if len(interest) == 4 else None,
                  {"price": f"px:{ticker}:{day}", "shares": model.cells[
                      ("shares_diluted", shares_q)].ref or "", "debt": debt_id or "",
                   "cash": cash_id or "", "short_term_investments": sti_id or ""}, exceptions)


def beta(store: Store, ticker: str, months: int) -> tuple[float | None, int]:
    """OLS beta of monthly total returns (adjusted closes) against SPY."""
    def monthly(name: str) -> dict[str, float]:
        out = {}
        for day, value in store.series(name):
            out[day[:7]] = value  # the month's last close
        return out

    stock, index = monthly(f"px:{ticker}:adj_close"), monthly("px:SPY:adj_close")
    keys = sorted(set(stock) & set(index))[-(months + 1):]
    r_s = [stock[b] / stock[a] - 1 for a, b in zip(keys, keys[1:], strict=False)]
    r_m = [index[b] / index[a] - 1 for a, b in zip(keys, keys[1:], strict=False)]
    if len(r_s) < 24:
        return None, len(r_s)
    return statistics.covariance(r_s, r_m) / statistics.variance(r_m), len(r_s)


def cost_of_capital(store: Store, ticker: str, m: Market, tax_rate: float, v: dict) -> dict:
    rf_day, rf = store.series("macro:DGS10")[-1]
    erp_day, erp = store.series("erp:damodaran")[-1]
    b, months = beta(store, ticker, v["beta_months"])
    b = b if b is not None else 1.0
    ke = rf / 100 + b * erp
    weight = m.debt / (m.debt + m.market_cap) if m.debt else 0.0
    kd = m.interest / m.debt if m.interest and m.debt else None
    if weight < v["debt_weight_threshold"] or kd is None:
        wacc = ke
    else:
        wacc = (1 - weight) * ke + weight * kd * (1 - tax_rate)
    return {"rf": rf / 100, "rf_id": f"macro:DGS10:{rf_day}", "erp": erp,
            "erp_id": f"erp:damodaran:{erp_day}", "beta": b, "beta_months": months, "ke": ke,
            "kd": kd, "debt_weight": weight, "wacc": wacc}


# --- DCF -----------------------------------------------------------------------------------


def ufcf(model: Model, quarter: str, tax_rate: float) -> float | None:
    ebitda, sbc = model.value("adj_ebitda", quarter), model.value("sbc", quarter)
    ebit = model.value("operating_income", quarter)
    capex = model.value("capex", quarter) or 0.0  # ABNB tags none: 0, an exception
    if None in (ebitda, sbc, ebit):
        return None
    return ebitda - sbc - max(ebit, 0.0) * tax_rate - capex


def dcf(model: Model, as_of: str, wacc: float, growth: float, tax_rate: float, v: dict) -> dict:
    """EV from the forecast quarters' UFCF, then annual years, then a Gordon terminal value."""
    start = date.fromisoformat(as_of)
    flows = []  # (years from the as-of to mid-period, UFCF)
    for q in model.forecast:
        value = ufcf(model, q, tax_rate)
        if value is None:
            raise ValueError(f"{model.ticker} {q}: UFCF needs adj. EBITDA, SBC and EBIT")
        year, n = int(q[:4]), int(q[5])
        mid = date(year, 3 * n - 1, 15)
        flows.append(((mid - start).days / 365.25, value))
    last_year = int(model.forecast[-1][:4])
    annual = sum(v_ for _, v_ in flows[-4:])
    rev = [model.value("revenue", f"FY{y}") for y in (last_year - 1, last_year)]
    g = rev[1] / rev[0] - 1 if None not in rev and rev[0] else growth
    t_last = flows[-1][0]
    while (t_last + 1) * 1.0 <= v["dcf_years"]:
        g = growth + (g - growth) * v["annual_fade"]  # fade toward the terminal growth
        annual *= 1 + g
        t_last += 1
        flows.append((t_last - 0.375, annual))  # mid-year of the next forecast year
    explicit = sum(cf / (1 + wacc) ** t for t, cf in flows)
    terminal = annual * (1 + growth) / (wacc - growth) / (1 + wacc) ** t_last
    return {"ev": explicit + terminal, "terminal_share": terminal / (explicit + terminal),
            "years": len(flows)}


# --- Valuation -----------------------------------------------------------------------------


def target_multiple_base(peer_models: dict[str, Model], peer_markets: dict[str, Market],
                         year: int) -> dict:
    multiples = {}
    for t, model in peer_models.items():
        ebitda = model.value("adj_ebitda", f"FY{year}")
        if ebitda and ebitda > 0:
            multiples[t] = peer_markets[t].ev / ebitda
    values = sorted(multiples.values())
    return {"peers": multiples, "median": statistics.median(values) if values else None,
            "range": [values[0], values[-1]] if values else None}


def value(store: Store, f: Forecast, settings: dict, peer_cache: dict | None = None) -> dict:
    """valuation.json for one company (its forecast `f`), deterministic for given inputs."""
    v = settings["valuation"]
    model = f.model
    m = market(store, model)
    tax = model.assumptions.get("tax_rate", 0.21)
    capital = cost_of_capital(store, model.ticker, m, tax, v)
    year = v["target_year"]
    peers = peer_forecasts(store, model.ticker, settings, peer_cache)
    peers[model.ticker] = f
    peer_markets = {t: market(store, p.model) for t, p in peers.items()}
    others = {t: p.model for t, p in peers.items() if t != model.ticker}
    peer = target_multiple_base(others or {model.ticker: model},
                                peer_markets if others else {model.ticker: m}, year)
    exceptions = list(m.exceptions) + (["no other company in the sector: own multiple"]
                                       if not others else [])
    models = scenarios(f, settings)
    out_scenarios = {}
    for name, sm in models.items():
        ebitda = sm.value("adj_ebitda", f"FY{year}")
        k = {"bear": -1, "base": 0, "bull": 1}[name] * v["scenario_multiple"]
        multiple = peer["median"] * (1 + k) if peer["median"] else None
        flagged = bool(peer["range"] and multiple and not (
            peer["range"][0] <= multiple <= peer["range"][1]))
        multiple_pt = ((multiple * ebitda - m.net_debt) / m.shares
                       if multiple and ebitda else None)
        d = dcf(sm, model.as_of, capital["wacc"], v["terminal_growth"], tax, v)
        dcf_pt = (d["ev"] - m.net_debt) / m.shares
        usable = d["ev"] > 0
        if not usable:
            exceptions.append(f"{name} DCF EV {d['ev']:,.0f} not positive (unlevered FCF "
                              "negative): price target from the multiple alone")
        if multiple_pt is not None and usable:
            pt = v["weights"]["multiple"] * multiple_pt + v["weights"]["dcf"] * dcf_pt
        else:
            pt = multiple_pt if multiple_pt is not None else dcf_pt
        out_scenarios[name] = {"adj_ebitda": ebitda, "target_multiple": multiple,
                               "multiple_outside_peer_range": flagged,
                               "multiple_pt": multiple_pt, "dcf_ev": d["ev"],
                               "dcf_pt": dcf_pt, "dcf_used": usable,
                               "terminal_share": d["terminal_share"],
                               "price_target": pt}
        if flagged:
            exceptions.append(f"{name} target multiple {multiple:.1f}x outside the peer range")
    weights = settings["scenarios"]["weights"]
    pt = sum(weights[n] * out_scenarios[n]["price_target"] for n in weights)
    ret = pt / m.price - 1
    rating = ("Buy" if ret >= v["rating"]["buy"] else "Sell" if ret <= v["rating"]["sell"]
              else "Hold")
    base = models["base"]
    fy = f"FY{year}"
    shown = {
        "ev_to_ebitda": m.ev / base.value("adj_ebitda", fy),
        "ev_to_ebitda_less_sbc": m.ev / (base.value("adj_ebitda", fy) - base.value("sbc", fy)),
        "pe": m.price / base.value("eps_diluted", fy) if base.value("eps_diluted", fy) else None,
        "fcf_yield": (base.value("free_cash_flow", fy) / m.market_cap
                      if base.value("free_cash_flow", fy) is not None else None),
    }
    grid = {
        f"{w:.4f}|{g:.4f}": (dcf(base, model.as_of, w, g, tax, v)["ev"] - m.net_debt) / m.shares
        for w in (capital["wacc"] - v["wacc_grid_step"], capital["wacc"],
                  capital["wacc"] + v["wacc_grid_step"])
        for g in v["terminal_growth_grid"]
    }
    result = {
        "ticker": model.ticker, "as_of": model.as_of, "price": m.price,
        "price_date": m.price_date, "shares": m.shares, "market_cap": m.market_cap,
        "debt": m.debt, "cash_and_investments": m.cash, "net_debt": m.net_debt, "ev": m.ev,
        "refs": m.refs, "cost_of_capital": capital, "peer_multiples": peer,
        "multiples_2027e": shown, "scenarios": out_scenarios, "price_target": pt,
        "return": ret, "rating": rating, "dcf_grid": grid, "exceptions": exceptions,
    }
    result["inputs_hash"] = hashlib.sha256(json.dumps(
        {"assumptions": model.assumptions, "market": m.refs, "price": m.price,
         "wacc": capital["wacc"]}, sort_keys=True).encode()).hexdigest()[:16]
    return _rounded(result)


def peer_forecasts(store: Store, ticker: str, settings: dict, cache: dict | None) -> dict:
    """Sector peers' forecasts with defaults (no LLM), cached per (ticker, as_of)."""
    cache = {} if cache is None else cache
    defaults = dict(settings, proposer=dict(settings["proposer"], method="defaults"))
    out = {}
    for t in peers_of(ticker):
        if t == ticker:
            continue
        key = (t, store.asof.date.isoformat())
        if key not in cache:
            cache[key] = forecast(store, t, defaults)
        out[t] = cache[key]
    return out


def rank(valuations: dict[str, dict]) -> dict[str, str]:
    """--all: top third by return Buy, bottom third Sell, else Hold."""
    order = sorted(valuations, key=lambda t: valuations[t]["return"], reverse=True)
    third = len(order) // 3
    return {t: "Buy" if i < third else "Sell" if i >= len(order) - third else "Hold"
            for i, t in enumerate(order)}


def _rounded(value):
    """Floats to 10 significant digits: byte-identical output for identical inputs."""
    if isinstance(value, float):
        return float(f"{value:.10g}")
    if isinstance(value, dict):
        return {k: _rounded(x) for k, x in value.items()}
    if isinstance(value, list):
        return [_rounded(x) for x in value]
    return value


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("as_of")
    ap.add_argument("--all", action="store_true", help="also rank the companies given")
    ap.add_argument("--db", type=Path, default=Path("data/v4/build/parent_child.db"))
    ap.add_argument("--out", type=Path, default=Path("output/research"))
    args = ap.parse_args()
    store = Store(args.db, AsOf.of(args.as_of))
    settings = knobs()
    cache: dict = {}
    results = {}
    for ticker in args.tickers:
        key = (ticker, args.as_of)
        f = cache.get(key) or forecast(store, ticker, settings)
        cache[key] = f
        result = value(store, f, settings, cache)
        results[ticker] = result
        folder = args.out / ticker / args.as_of
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "valuation.json").write_text(json.dumps(result, indent=1, sort_keys=True))
        s = result["scenarios"]
        print(f"{ticker} price {result['price']:.2f}  PT {result['price_target']:.2f} "
              f"({result['return']:+.1%}) {result['rating']}  WACC "
              f"{result['cost_of_capital']['wacc']:.2%} β {result['cost_of_capital']['beta']:.2f}"
              f"  peer median {result['peer_multiples']['median']:.1f}x  bear/base/bull "
              f"{s['bear']['price_target']:.0f}/{s['base']['price_target']:.0f}/"
              f"{s['bull']['price_target']:.0f}  TV share {s['base']['terminal_share']:.0%}")
    if args.all:
        for t, r in rank(results).items():
            print(f"  relative {t}: {r}")


if __name__ == "__main__":
    main()
