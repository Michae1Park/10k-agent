"""Outside data as forecast inputs (V4 design doc §8.3; spec §3.2 "Outside inputs").

research.yaml `forecast.outside` maps one starting growth rate (`<line>.g0`) per company to a
monthly outside series (NYC TLC trips, BTS airline passengers, FRED restaurant sales or CPI).
With `method: nowcast`, for each mapping:

  1. the series' y/y per month, from values visible at the as-of (dated and published before it,
     each at its latest visible vintage: Store.series);
  2. the window: the three months ending at the latest visible month, k months before the end
     of the first forecast quarter (k = 0 when the series already covers that quarter; TLC, BTS
     and FRED mostly lag, so k is usually 3 to 6);
  3. an OLS fit of the driver's history y/y (the `<line>` values) on the series' mean y/y over
     the window at the same lag k before each history quarter (at least `min_quarters`), so the
     fit uses the series as it is used at the as-of;
  4. a slope b ≤ 0 is not used (each mapping expects the driver to move with the series);
  5. g0 = (1 − w) × default + w × (a + b × x), x the window's mean y/y and w = `weight` × R²
     (a weak fit moves g0 little).

Every use and every skip is logged as an `info` exception. A guidance anchor that solves the same
assumption overrides the result in the first forecast quarter (management's guide wins). With
`method: off` (the ablation) nothing changes.
"""

import statistics

from tenk_agent.research.model.graph import Model
from tenk_agent.store import Store


def monthly_yoy(points: list[tuple[str, float]]) -> dict[str, float]:
    """{YYYY-MM: y/y} for monthly points (date YYYY-MM-DD) whose month a year earlier is known."""
    by_month = {d[:7]: v for d, v in points}
    out = {}
    for month, value in by_month.items():
        prior = by_month.get(f"{int(month[:4]) - 1}{month[4:]}")
        if prior:
            out[month] = value / prior - 1
    return out


def month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def month_name(index: int) -> str:
    return f"{index // 12}-{index % 12 + 1:02d}"


def quarter_end(quarter: str) -> int:
    return int(quarter[:4]) * 12 + 3 * int(quarter[5]) - 1


def window(yoy: dict[str, float], end: int) -> float | None:
    """Mean y/y of the three months ending at month index `end`; None if one is missing."""
    values = [yoy.get(month_name(end - i)) for i in (2, 1, 0)]
    return None if None in values else sum(values) / 3


def fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """(a, b, R²) of y = a + b·x by least squares."""
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return my, 0.0, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    a = my - b * mx
    ss = sum((y - my) ** 2 for y in ys)
    resid = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys, strict=True))
    return a, b, (1 - resid / ss) if ss else 0.0


def adjust(model: Model, store: Store, defaults: dict[str, float], settings: dict
           ) -> tuple[dict[str, float], list[dict]]:
    """The defaults with the mapped g0s moved toward the outside nowcast, and the log rows."""
    cfg = settings["forecast"].get("outside") or {}
    spec = (cfg.get("inputs") or {}).get(model.ticker)
    if cfg.get("method") in (None, False, "off") or not spec or not model.forecast:
        return defaults, []
    out, log = dict(defaults), []
    for item in spec if isinstance(spec, list) else [spec]:
        name, series = item["driver"], item["series"]
        line = name.removesuffix(".g0")
        if name not in out:
            log.append(_info(f"outside input {series} → {name}: not an assumption of the model"))
            continue
        yoy = monthly_yoy(store.series(series))
        first = model.forecast[0]
        if not yoy:
            log.append(_info(f"outside input {series} → {name}: no visible months; not used"))
            continue
        last = min(month_index(max(yoy)), quarter_end(first))
        lag = quarter_end(first) - last
        x_now = window(yoy, last)
        pairs = [(x, y) for q in model.history
                 if (x := window(yoy, quarter_end(q) - lag)) is not None
                 and (y := model.value(line, q)) is not None]
        need = cfg.get("min_quarters", 6)
        if x_now is None or len(pairs) < need:
            log.append(_info(f"outside input {series} → {name}: {len(pairs)} quarters fit at a "
                             f"{lag}-month lag, fewer than {need}; not used"))
            continue
        a, b, r2 = fit([x for x, _ in pairs], [y for _, y in pairs])
        where = (f"{first} from {month_name(last - 2)}–{month_name(last)} y/y {x_now:+.1%} "
                 f"({lag}-month lag); fit over {len(pairs)} quarters b {b:.2f} R² {r2:.2f}")
        if b <= 0:
            log.append(_info(f"outside input {series} → {name}: {where}; slope not positive, "
                             "not used"))
            continue
        w = cfg.get("weight", 0.5) * max(r2, 0.0)
        before = out[name]
        out[name] = (1 - w) * before + w * (a + b * x_now)
        log.append(_info(f"outside input {series} → {name}: {where}; weight {w:.2f}; "
                         f"g0 {before:+.2%} → {out[name]:+.2%}"))
    return out, log


def _info(item: str) -> dict:
    return {"severity": "info", "item": item}
