"""SHOP regression (external review 9 Aug 2026, owner-approved).

The daily scan promoted SHOP to "verified BUY" three sessions after a +17%
earnings gap on ~3.6x volume, narrating RSI 71 as "recovering — bounce
zone", while EARNINGS_UNKNOWN (dead calendar feed) failed OPEN with a mild
penalty. Two fixes under test:

1. RSI cap: "recovering" requires RSI < overbought — an RSI >= 70 rebound
   is already over; entry is chasing.
2. Post-event gap quarantine: a >3-sigma single-session move on >=3x volume
   within the last 5 sessions demotes ANY BUY to WAIT — calendar-independent,
   so it holds exactly when the earnings feed is dead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradepro_strategies.market_state import _post_event_gap, market_state


def _shop_like_prices(gap_sessions_ago: int = 3) -> pd.DataFrame:
    """~300 calm sessions in a mild uptrend, then a -25% slide into a huge
    single-session gap up on 4x volume, then drift — SHOP's Aug 2026 shape.
    Deterministic (seeded) so the test can't flake."""
    rng = np.random.default_rng(7)
    n = 300
    base = 150.0 * np.cumprod(1 + rng.normal(0.0006, 0.012, n))
    closes = list(base)
    # slide into the event
    for _ in range(15):
        closes.append(closes[-1] * (1 + rng.normal(-0.012, 0.01)))
    closes.append(closes[-1] * 1.17)          # the event gap (+17%)
    for _ in range(gap_sessions_ago - 1):
        closes.append(closes[-1] * (1 + rng.normal(0.02, 0.008)))  # post-gap drift up
    vols = [10_000_000.0] * len(closes)
    vols[len(closes) - gap_sessions_ago] = 40_000_000.0            # 4x volume on the event
    idx = pd.bdate_range(end="2026-08-07", periods=len(closes))
    return pd.DataFrame({"close": closes, "volume": vols,
                         "high": [c * 1.01 for c in closes],
                         "low": [c * 0.99 for c in closes]}, index=idx)


class TestPostEventGapDetector:
    def test_detects_shop_like_gap(self):
        df = _shop_like_prices()
        g = _post_event_gap(df["close"], df["volume"])
        assert g is not None
        assert g["sessions_ago"] == 3
        assert g["ret_pct"] == pytest.approx(17.0, abs=0.5)
        assert g["vol_ratio"] == pytest.approx(4.0, abs=0.3)

    def test_calm_series_has_no_event(self):
        rng = np.random.default_rng(11)
        closes = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.01, 300))
        idx = pd.bdate_range(end="2026-08-07", periods=300)
        s = pd.Series(closes, index=idx)
        v = pd.Series([5e6] * 300, index=idx)
        assert _post_event_gap(s, v) is None

    def test_big_move_on_normal_volume_is_not_an_event(self):
        df = _shop_like_prices()
        flat_vol = pd.Series([10_000_000.0] * len(df), index=df.index)
        assert _post_event_gap(df["close"], flat_vol) is None

    def test_old_gap_outside_window_ignored(self):
        df = _shop_like_prices(gap_sessions_ago=3)
        # push the gap 8 sessions into the past with calm days
        last = float(df["close"].iloc[-1])
        extra_idx = pd.bdate_range(start=df.index[-1] + pd.Timedelta(days=1), periods=6)
        extra = pd.DataFrame({"close": [last * (1 + 0.001 * i) for i in range(1, 7)],
                              "volume": [10_000_000.0] * 6,
                              "high": [last * 1.01] * 6, "low": [last * 0.99] * 6},
                             index=extra_idx)
        df2 = pd.concat([df, extra])
        assert _post_event_gap(df2["close"], df2["volume"]) is None


class TestBuyQuarantineEndToEnd:
    def test_shop_like_tape_never_buys(self):
        state = market_state("SHOP", _shop_like_prices())
        assert state.post_event_gap is not None
        assert state.entry_signal != "BUY"
        if "EVENT QUARANTINE" in state.entry_reason:
            assert "session" in state.entry_reason

    def test_overbought_rsi_never_narrated_as_bounce(self):
        # Whatever the verdict, an RSI>=70 row must not read "bounce zone".
        state = market_state("SHOP", _shop_like_prices())
        if state.rsi_14 is not None and state.rsi_14 >= 70:
            assert "bounce zone" not in state.entry_reason
