"""Every stored daily row is RAW (split-adjusted only) with the dividend
adjustment in adj_factor (12 Nov 2026).

Histories interleaved IBKR rows (raw) with yfinance rows written
auto_adjust=True (dividend-adjusted) ~55 times per symbol, and adj_factor was
1.0 everywhere. Every switch was a false jump: ACN +68% on 2012-02-23. A
momentum ranking reads such a jump as a return.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradepro_strategies.cli.bar_cache_reconvention import convert, switch_jumps


def _yahoo(days, raw, factor):
    idx = pd.to_datetime(days)
    return pd.DataFrame({"open": raw, "high": raw, "low": raw, "close": raw,
                         "volume": [1] * len(raw), "adj close": np.array(raw) * factor,
                         "adj_factor": factor}, index=idx)


def _stored(days, closes, sources):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=14, minutes=30)
                            for d in days])
    c = np.array(closes, float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1,
                         "adj_factor": 1.0, "source": sources}, index=idx)


DAYS = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]


def test_a_dividend_adjusted_yahoo_row_goes_back_to_raw():
    # raw 100 every day; yahoo's adjusted close is 80 (20% of dividends since)
    y = _yahoo(DAYS, [100.0] * 4, np.array([0.8] * 4))
    s = _stored(DAYS, [100, 80, 100, 80], ["ibkr_web", "yfinance", "ibkr_web", "yfinance"])
    assert switch_jumps(s)[0] == 1.0          # every switch was a >10% jump

    out, st = convert(s, y)
    assert list(out["close"]) == [100.0] * 4   # one scale
    assert np.allclose(out["adj_factor"], 0.8)
    assert switch_jumps(out)[0] == 0.0
    assert st["ibkr_kept"] == 2 and st["replaced"] == 2


def test_ibkr_prices_are_never_replaced():
    y = _yahoo(DAYS, [100.0] * 4, np.array([1.0] * 4))
    s = _stored(DAYS, [101, 100, 100, 100], ["ibkr_web", "ibkr", "ibkr_web", "ibkr_web"])
    out, _ = convert(s, y)
    assert out["close"].iloc[0] == 101.0


def test_missing_sessions_inside_the_span_are_filled_and_none_removed():
    y = _yahoo(DAYS, [100.0, 101, 102, 103], np.array([1.0] * 4))
    s = _stored([DAYS[0], DAYS[3]], [100, 103], ["ibkr_web", "ibkr_web"])
    out, st = convert(s, y)
    assert st["gap_filled"] == 2
    assert len(out) == 4 and out.index.is_monotonic_increasing
    assert [str(t)[:10] for t in out.index] == DAYS
    # filled rows sit on the existing yfinance timestamp convention (NY midnight)
    assert out.index[1].tz is not None


def test_the_provider_now_writes_raw_with_the_factor():
    from tradepro_strategies.bar_cache.providers.yfinance_provider import YFinanceProvider
    df = _yahoo(DAYS, [100.0] * 4, np.array([0.9] * 4))
    df.columns = ["Open", "High", "Low", "Close", "Volume", "Adj Close", "x"]
    df = df.drop(columns="x")
    n = YFinanceProvider._normalise(df)
    assert np.allclose(n["close"], 100.0)
    assert np.allclose(n["adj_factor"], 0.9)
    assert "adj close" not in n.columns


def test_volume_comes_from_yahoo_even_on_ibkr_rows():
    y = _yahoo(DAYS, [100.0] * 4, np.array([1.0] * 4))
    y["volume"] = [500, 600, 700, 800]
    s = _stored(DAYS, [100] * 4, ["ibkr_web"] * 4)
    s["volume"] = [3500, 4200, 4900, 5600]          # 7x off, as MU was
    out, _ = convert(s, y)
    assert list(out["volume"]) == [500, 600, 700, 800]
    assert list(out["close"]) == [100.0] * 4
