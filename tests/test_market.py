"""Market and macro data under the as-of rule (acquisition/market.py, Store.series)."""

import json

from tenk_agent.acquisition.market import market_rows
from tenk_agent.asof import AsOf
from tenk_agent.store import Store


def raw_dir(tmp_path):
    raw = tmp_path / "raw"
    (raw / "prices").mkdir(parents=True)
    (raw / "macro").mkdir()
    prices = [
        {
            "date": f"2026-08-0{d}T00:00:00.000Z",
            "close": 70.0 + d,
            "adjClose": 70.0 + d,
            "volume": 1e6,
        }
        for d in (3, 4, 5)
    ]
    (raw / "prices" / "UBER.json").write_text(json.dumps({"rows": prices}))
    cpi = [  # Jan 2025 CPI, first print then revised a year later
        {"date": "2025-01-01", "value": "319.086", "realtime_start": "2025-02-12"},
        {"date": "2025-01-01", "value": "318.961", "realtime_start": "2026-02-13"},
    ]
    (raw / "macro" / "CPIAUCSL.json").write_text(json.dumps({"observations": cpi}))
    dgs = [
        {"date": "2026-08-04", "value": "4.63", "realtime_start": "x"},
        {"date": "2026-08-05", "value": "."},
    ]
    (raw / "macro" / "DGS10.json").write_text(json.dumps({"observations": dgs}))
    return raw


def store(tmp_path, asof):
    s = Store(tmp_path / "m.db", AsOf.today())
    s.enable_facts()
    s.add_market(market_rows(raw_dir(tmp_path)))
    return Store(tmp_path / "m.db", AsOf.of(asof))


def test_as_of_sees_the_previous_close(tmp_path):
    assert store(tmp_path, "2026-08-06").series("px:UBER:close")[-1] == ("2026-08-05", 75.0)
    assert (
        Store(tmp_path / "m.db", AsOf.of("2026-08-05")).series("px:UBER:close")[-1][0]
        == "2026-08-04"
    )


def test_revised_series_use_the_vintage_known_then(tmp_path):
    store(tmp_path, "2026-01-01")
    before = dict(Store(tmp_path / "m.db", AsOf.of("2026-01-01")).series("macro:CPIAUCSL"))
    after = dict(Store(tmp_path / "m.db", AsOf.of("2026-02-14")).series("macro:CPIAUCSL"))
    assert before["2025-01-01"] == 319.086 and after["2025-01-01"] == 318.961
    assert Store(tmp_path / "m.db", AsOf.of("2025-02-12")).series("macro:CPIAUCSL") == []


def test_unrevised_daily_series_are_published_the_next_day_and_gaps_skipped(tmp_path):
    s = store(tmp_path, "2026-08-05")
    assert s.series("macro:DGS10") == []  # the 08-04 yield is published on 08-05
    assert Store(tmp_path / "m.db", AsOf.of("2026-08-06")).series("macro:DGS10") == [
        ("2026-08-04", 4.63)
    ]
