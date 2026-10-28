"""The model book: a session's models at one as-of, for Research mode's tools and model: ids.

  model:<T>:<as_of>:base:<line>.<period>        the default forecast at the as-of (anchored to
                                                 guidance); rebuilt on demand, so always citable
  model:<T>:<as_of>:scratch-<n>:<line>.<period>  a run_scenario run in this session (design §7.2:
                                                 scratch runs are not versions)
  model:<T>:<as_of>:val-<version>:<field>        a valuation output (price_target, ev, …;
                                                 rating resolves to its text)
  model:<T>:<as_of>:var-<period>:<field>          variance_review's revenue bridge for a quarter
Ratios resolve, and the model tools show them, in percent.
Saved versions (v1, v2, …) come with Report mode (V4.6).
"""

from dataclasses import dataclass, field

from tenk_agent.research.forecast import Forecast, forecast, knobs
from tenk_agent.research.model.graph import Model
from tenk_agent.store import Store


@dataclass
class Book:
    store: Store
    settings: dict = field(default_factory=knobs)
    forecasts: dict[str, Forecast] = field(default_factory=dict)  # base, per ticker
    scratch: dict[str, Model] = field(default_factory=dict)  # "T:scratch-n" → model
    valuations: dict[str, dict] = field(default_factory=dict)  # "T:version" → valuation
    earlier: dict[str, "Book"] = field(default_factory=dict)  # as-of → a book then (variance)

    def at(self, as_of: str) -> "Book":
        """The book at an earlier as-of (a prior forecast); never a later one."""
        if as_of >= self.as_of:
            raise ValueError(f"{as_of} is not before this book's as-of {self.as_of}")
        if as_of not in self.earlier:
            from tenk_agent.asof import AsOf

            self.earlier[as_of] = Book(Store(self.store.path, AsOf.of(as_of)), self.settings)
        return self.earlier[as_of]

    @property
    def as_of(self) -> str:
        return self.store.asof.date.isoformat()

    def base(self, ticker: str) -> Forecast:
        if ticker not in self.forecasts:
            defaults = dict(self.settings, proposer=dict(self.settings["proposer"],
                                                         method="defaults"))
            self.forecasts[ticker] = forecast(self.store, ticker, defaults)
        return self.forecasts[ticker]

    def model(self, ticker: str, version: str) -> Model | None:
        if version == "base":
            return self.base(ticker).model
        return self.scratch.get(f"{ticker}:{version}")

    def add_scratch(self, model: Model) -> str:
        n = sum(1 for k in self.scratch if k.startswith(f"{model.ticker}:")) + 1
        version = f"scratch-{n}"
        self.scratch[f"{model.ticker}:{version}"] = model
        return version

    def valuation(self, ticker: str, version: str = "base") -> dict:
        key = f"{ticker}:{version}"
        if key not in self.valuations:
            from tenk_agent.research.valuation import value

            f = self.base(ticker)
            if version != "base":
                f = Forecast(self.scratch[key], f.pack, f.defaults, f.anchors)
            self.valuations[key] = value(self.store, f, self.settings, self._peer_cache())
        return self.valuations[key]

    def _peer_cache(self) -> dict:
        return {(t, self.as_of): f for t, f in self.forecasts.items()}

    def ref(self, ticker: str, version: str, line: str, period: str) -> str:
        return f"model:{ticker}:{self.as_of}:{version}:{line}.{period}"

    def resolve(self, ticker: str, as_of: str, version: str, key: str) -> dict | None:
        """{value, unit} for a model: id, or None."""
        if as_of < self.as_of and version == "base":
            return self.at(as_of).resolve(ticker, as_of, version, key)
        if as_of != self.as_of:
            return None
        if version.startswith("val-"):
            v = self.valuation(ticker, version[4:])
            for where in (v, v["multiples_2027e"], v["scenarios"]["base"]):
                if key in where:
                    value = where[key]
                    break
            else:
                return None
            if key == "rating":
                return {"text": value}
            if not isinstance(value, int | float):
                return None
            if key in ("return", "fcf_yield"):  # fractions, written as percentages
                return {"value": value * 100, "unit": "percent"}
            unit = "USD" if key in ("price_target", "price", "dcf_pt", "multiple_pt") else (
                "USD millions" if key in ("ev", "market_cap") else "")
            return {"value": value, "unit": unit}
        if version.startswith("var-"):
            from tenk_agent.agent.tools import variance

            found = variance(self, ticker, version[4:]).get("bridge") or {}
            return {"value": found[key], "unit": "USD millions"} if found.get(key) is not None \
                else None
        line, _, period = key.rpartition(".")
        model = self.model(ticker, version)
        if model is None or not any(x.name == line for x in model.lines):
            return None
        value, unit = model.value(line, period), model.line(line).unit
        if value is None:
            return None
        if unit == "fraction":  # ratios are written as percentages
            return {"value": value * 100, "unit": "percent"}
        return {"value": value, "unit": unit}
