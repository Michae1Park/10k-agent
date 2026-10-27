"""The as-of rule (V4 design doc §3): a run sees the world at the market open of its as-of date.

Data is visible when it became public strictly before that date. This module is the only place
that compares dates for visibility; everything else asks an AsOf.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta


def _day(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class AsOf:
    date: date

    @classmethod
    def of(cls, value: date | str) -> "AsOf":
        return cls(_day(value))

    @classmethod
    def today(cls) -> "AsOf":
        """V3 runs: everything filed up to yesterday (the V3 corpus is years old)."""
        return cls(date.today())

    @classmethod
    def after(cls, value: date | str) -> "AsOf":
        """The first as-of that sees something made public on `value`: the next day."""
        return cls(_day(value) + timedelta(days=1))

    def sees(self, published: date | str | None) -> bool:
        """True if data made public on `published` is visible. Undated data is never visible."""
        return published is not None and _day(published) < self.date

    def sql(self, alias: str, column: str = "filed") -> tuple[str, list[str]]:
        """A WHERE clause (and its parameter) keeping rows visible at this as-of."""
        return f"{alias}.{column} < ?", [self.date.isoformat()]

    def latest[T, K](
        self, rows: Iterable[T], key: Callable[[T], K], published: Callable[[T], str]
    ) -> dict[K, T]:
        """Per key, the visible row published last: a restatement replaces the original only
        once it is visible. Ties keep the first row seen."""
        best: dict[K, T] = {}
        for row in rows:
            when = published(row)
            if self.sees(when) and (
                key(row) not in best or _day(when) > _day(published(best[key(row)]))
            ):
                best[key(row)] = row
        return best

    def __str__(self) -> str:
        return self.date.isoformat()
