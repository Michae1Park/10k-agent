"""V4 market and macro data (design doc §4.2), each value with the date it became public.

  prices  Tiingo daily end-of-day per ticker and SPY (beta): close, split-adjusted close,
          volume. Public the evening of the trading day, so as-of D sees D-1's close.
          Licence: internal use only, so nothing here is committed or published (§11, §17.5)
  macro   FRED/ALFRED: CPIAUCSL, UMCSENT, RSFSDP with every vintage (realtime_start is when
          each value was published); DGS10 and DTWEXBGS are not revised, published the next day
  erp     Damodaran's monthly implied equity risk premium ("T12m with sustainable payout"),
          taken as published three days after the start of the month it describes

Raw responses are cached under <data_dir>/raw/prices/ and raw/macro/; market_rows() turns them
into rows for the store's `market` table: (series, date, value, published).
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TIINGO = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
FRED = "https://api.stlouisfed.org/fred/series/observations"
ERP_URL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
REVISED = ("CPIAUCSL", "UMCSENT", "RSFSDP")
UNREVISED = ("DGS10", "DTWEXBGS")
BENCHMARK = "SPY"
START = "2018-01-01"  # five years of monthly returns before the first 2023 as-of (beta)
ERP_COLUMN = "ERP (T12 m with sustainable payout)"
ERP_DELAY = timedelta(days=3)


def _day_after(day: str) -> str:
    return (date.fromisoformat(day[:10]) + timedelta(days=1)).isoformat()


def fetch_prices(tickers: list[str], raw: Path, token: str, refresh: bool = False) -> list[str]:
    """Daily prices per ticker (and SPY) from START to today into raw/prices/<T>.json."""
    problems = []
    for ticker in [*tickers, BENCHMARK]:
        path = raw / "prices" / f"{ticker}.json"
        if path.exists() and not refresh:
            continue
        response = httpx.get(
            TIINGO.format(ticker=ticker),
            timeout=60,
            params={"startDate": START, "format": "json"},
            headers={"Authorization": f"Token {token}"},
        )
        if response.status_code != 200:
            problems.append(f"Tiingo {ticker}: HTTP {response.status_code}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"retrieved_at": _now(), "rows": response.json()}))
        log.info("Prices %s: %d days", ticker, len(response.json()))
    return problems


def fetch_macro(raw: Path, key: str, refresh: bool = False) -> list[str]:
    """FRED series into raw/macro/<SERIES>.json; revised series with every vintage."""
    problems = []
    for series in (*REVISED, *UNREVISED):
        path = raw / "macro" / f"{series}.json"
        if path.exists() and not refresh:
            continue
        params = {
            "series_id": series,
            "api_key": key,
            "file_type": "json",
            "observation_start": START,
        }
        if series in REVISED:
            params |= {"realtime_start": START, "realtime_end": "9999-12-31"}
        response = httpx.get(FRED, params=params, timeout=60)
        if response.status_code != 200:
            problems.append(f"FRED {series}: HTTP {response.status_code} {response.text[:200]}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"retrieved_at": _now(), **response.json()}))
        log.info("Macro %s: %d observations", series, len(response.json()["observations"]))
    return problems


def fetch_erp(raw: Path, refresh: bool = False) -> list[str]:
    path = raw / "macro" / "ERPbymonth.xlsx"
    if path.exists() and not refresh:
        return []
    response = httpx.get(ERP_URL, timeout=60, follow_redirects=True)
    if response.status_code != 200:
        return [f"Damodaran ERP: HTTP {response.status_code}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    path.with_name(path.name + ".retrieved").write_text(_now())  # the xlsx can't hold a date
    return []


def market_rows(raw: Path) -> list[tuple[str, str, float, str]]:
    """(series, date, value, published) from the raw caches."""
    rows = []
    for path in sorted((raw / "prices").glob("*.json")):
        ticker = path.stem
        for r in json.loads(path.read_text())["rows"]:
            day = r["date"][:10]
            published = day  # the close is public that evening, before the next open
            for field, name in (
                ("close", "close"),
                ("adjClose", "adj_close"),
                ("volume", "volume"),
            ):
                if r.get(field) is not None:
                    rows.append(
                        (f"px:{ticker}:{name}", day, float(r[field]), published)
                    )
    for path in sorted((raw / "macro").glob("*.json")):
        series = path.stem
        for o in json.loads(path.read_text())["observations"]:
            if o["value"] in (".", ""):
                continue
            published = o["realtime_start"] if series in REVISED else _day_after(o["date"])
            rows.append((f"macro:{series}", o["date"], float(o["value"]), published))
    erp = raw / "macro" / "ERPbymonth.xlsx"
    if erp.exists():
        rows += erp_rows(erp)
    from tenk_agent.acquisition.crosscheck import rows as crosscheck_rows

    return rows + crosscheck_rows(raw)  # V4.7: cc:tlc:<license>, cc:bts:<series>


def erp_rows(path: Path) -> list[tuple[str, str, float, str]]:
    import warnings

    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the workbook's unsupported extensions
        sheet = openpyxl.load_workbook(path, data_only=True)["Historical ERP"]
    header, *body = list(sheet.iter_rows(values_only=True))
    column = header.index(ERP_COLUMN)
    out = []
    for row in body:
        month, value = row[0], row[column]
        if isinstance(month, datetime) and isinstance(value, int | float):
            out.append(
                (
                    "erp:damodaran",
                    month.date().isoformat(),
                    float(value),
                    (month.date() + ERP_DELAY).isoformat(),
                )
            )
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
