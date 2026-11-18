"""A trading lane never downloads bars: the nightly harvest is the one
downloader (owner ruling 11 Nov). On 18 Nov the rotation lane's 18:03 run spent
88 minutes asking IBKR for SKHY months that do not exist, because the swing
chassis read history through a fetch-on-miss path."""
import datetime as dt

import pandas as pd

import tradepro_strategies.ibkr_bars as IB
import tradepro_strategies.paper.strategies.mean_reversion_swing as M
from tradepro_strategies.paper.strategy import Bar


class _Frame:
    def __init__(self, df):
        self.df, self.coverage_complete, self.provider_used, self.rows_expected = df, True, "cache", len(df)


class _Store:
    def __init__(self, df):
        self.df, self.calls = df, []

    def get(self, *a, **k):
        self.calls.append(k)
        return _Frame(self.df)


def _bar(day):
    return Bar(symbol="MU", timestamp=dt.datetime.combine(day, dt.time(15), dt.UTC),
               open=1, high=1, low=1, close=1, volume=1, timeframe_seconds=86_400)


def _closes(last_day, n=300):
    idx = pd.bdate_range(end=last_day, periods=n)
    return pd.DataFrame({"close": [100.0 + i * 0.1 for i in range(n)]}, index=idx)


def test_the_chassis_asks_the_store_for_stored_bars_only(monkeypatch):
    store = _Store(_closes("2026-11-17"))
    monkeypatch.setattr(IB, "bar_store", lambda: store)
    monkeypatch.setattr(IB, "route_asset_class", lambda s: "us_etf")
    s = M.MeanReversionSwingStrategy(strategy_id="t")
    closes, err = s._history("MU", _bar(dt.date(2026, 11, 18)))
    assert err is None and len(closes) == 300
    assert store.calls and all(k.get("skip_fetch") is True for k in store.calls)


def test_an_empty_store_never_falls_through_to_the_downloading_legacy_cache(monkeypatch):
    store = _Store(pd.DataFrame({"close": []}))
    monkeypatch.setattr(IB, "bar_store", lambda: store)
    monkeypatch.setattr(IB, "route_asset_class", lambda s: "us_etf")
    import tradepro_strategies.cache as C

    def boom(*a, **k):
        raise AssertionError("the legacy cache downloads on a miss")
    monkeypatch.setattr(C, "ensure_cached", boom)
    s = M.MeanReversionSwingStrategy(strategy_id="t")
    closes, err = s._history("MU", _bar(dt.date(2026, 11, 18)))
    assert closes is None and "holds nothing" in err


def test_a_series_the_harvest_stopped_writing_is_refused_not_traded(monkeypatch):
    store = _Store(_closes("2026-11-06"))
    monkeypatch.setattr(IB, "bar_store", lambda: store)
    monkeypatch.setattr(IB, "route_asset_class", lambda s: "us_etf")
    s = M.MeanReversionSwingStrategy(strategy_id="t")
    closes, err = s._history("MU", _bar(dt.date(2026, 11, 18)))
    assert closes is None and "2026-11-06" in err


def test_a_long_weekend_is_not_stale(monkeypatch):
    store = _Store(_closes("2026-11-26"))           # Thu before Thanksgiving Fri+weekend
    monkeypatch.setattr(IB, "bar_store", lambda: store)
    monkeypatch.setattr(IB, "route_asset_class", lambda s: "us_etf")
    s = M.MeanReversionSwingStrategy(strategy_id="t")
    closes, err = s._history("MU", _bar(dt.date(2026, 11, 30)))
    assert err is None
