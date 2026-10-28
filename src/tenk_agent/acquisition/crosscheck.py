"""Cross-check data (V4 design doc §4.2, V4.7): outside series that test the companies' KPIs.

  taskset -c 16-31 uv run python -m tenk_agent.acquisition.crosscheck [--since 2023-01]

  NYC TLC   high-volume for-hire trips per month and license (HV0003 Uber, HV0005 Lyft), from
            fhvhv_tripdata_YYYY-MM.parquet (~0.5 GB each): downloaded one month at a time,
            aggregated, deleted. Published = the file's Last-Modified (TLC lags ~2 months).
  BTS       monthly US airline passengers (Monthly Transportation Statistics, data.bts.gov),
            total, domestic and international, not seasonally adjusted. The rows carry no
            publish date: published = month end + 90 days (BTS lags ~3 months), an estimate.

Writes <data>/raw/crosscheck/tlc/<YYYY-MM>.json and bts.json; acquisition/market.py loads
them into the market table as cc:tlc:<license> and cc:bts:<series>.
"""

import argparse
import json
import tempfile
import urllib.request
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

TLC = "https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_{month}.parquet"
BTS = ("https://data.bts.gov/resource/crem-w557.json?$select=date,"
       "u_s_airline_traffic_total_non_seasonally_adjusted,"
       "u_s_airline_traffic_domestic_non_seasonally_adjusted,"
       "u_s_airline_traffic_international_non_seasonally_adjusted&$order=date&$limit=5000")
LICENSES = {"HV0003": "UBER", "HV0005": "LYFT"}  # confirmed on the first file (design §17.5)
BTS_LAG_DAYS = 90
AGENT = "tenk-agent research (crosscheck fetch)"


def months(since: str, until: date) -> list[str]:
    year, month = int(since[:4]), int(since[5:7])
    out = []
    while (year, month) <= (until.year, until.month):
        out.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def fetch_tlc(raw: Path, since: str = "2023-01") -> list[str]:
    """Aggregate every published month not cached yet; returns the months added."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    folder = raw / "crosscheck" / "tlc"
    folder.mkdir(parents=True, exist_ok=True)
    added = []
    for month in months(since, date.today()):
        out = folder / f"{month}.json"
        if out.exists():
            continue
        request = urllib.request.Request(TLC.format(month=month), headers={"User-Agent": AGENT})
        try:
            response = urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):  # not published yet
                break
            raise
        published = parsedate_to_datetime(response.headers["Last-Modified"]).date().isoformat()
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            while chunk := response.read(1 << 22):
                tmp.write(chunk)
            tmp.flush()
            licenses = pq.read_table(tmp.name, columns=["hvfhs_license_num"])[0]
            counts = pc.value_counts(licenses).to_pylist()
        trips = {c["values"]: c["counts"] for c in counts}
        out.write_text(json.dumps({"month": month, "published": published, "trips": trips,
                                   "source": TLC.format(month=month)}))
        added.append(month)
        print(f"TLC {month}: {sum(trips.values()):,} trips, published {published}")
    return added


def fetch_bts(raw: Path) -> int:
    request = urllib.request.Request(BTS, headers={"User-Agent": AGENT})
    rows = json.loads(urllib.request.urlopen(request, timeout=120).read())
    (raw / "crosscheck").mkdir(parents=True, exist_ok=True)
    (raw / "crosscheck" / "bts.json").write_text(json.dumps(
        {"fetched": date.today().isoformat(), "source": BTS, "rows": rows}))
    return len(rows)


def rows(raw: Path) -> list[tuple[str, str, float, str]]:
    """(series, date, value, published) for the market table."""
    out = []
    for path in sorted((raw / "crosscheck" / "tlc").glob("*.json")):
        d = json.loads(path.read_text())
        for license, trips in d["trips"].items():
            out.append((f"cc:tlc:{license}", f"{d['month']}-01", float(trips), d["published"]))
    bts = raw / "crosscheck" / "bts.json"
    if bts.exists():
        for r in json.loads(bts.read_text())["rows"]:
            day = r["date"][:10]
            month_end = (date.fromisoformat(day).replace(day=28) + timedelta(days=4))
            month_end -= timedelta(days=month_end.day)
            published = (month_end + timedelta(days=BTS_LAG_DAYS)).isoformat()
            for key, name in (("total", "airline_passengers"),
                              ("domestic", "airline_passengers_domestic"),
                              ("international", "airline_passengers_international")):
                value = r.get(f"u_s_airline_traffic_{key}_non_seasonally_adjusted")
                if value not in (None, ""):
                    out.append((f"cc:bts:{name}", day, float(value), published))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/v4"))
    ap.add_argument("--since", default="2023-01")
    ap.add_argument("--skip-tlc", action="store_true")
    args = ap.parse_args()
    print(f"BTS: {fetch_bts(args.data / 'raw')} months")
    if not args.skip_tlc:
        print(f"TLC: {len(fetch_tlc(args.data / 'raw', args.since))} months added")


if __name__ == "__main__":
    main()
