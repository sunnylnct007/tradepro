"""Pins the live intraday spike guard (bar_bus.sanitize_intraday_bars).

Regression for the phantom-tick false stop: Yahoo's 1-minute feed emitted a bar
wildly off both neighbours (memory: UNH ~$377) which fired a false stop. The
guard must drop the ISOLATED spike while leaving genuine moves/gaps untouched.
"""
from datetime import datetime, timedelta, timezone

from tradepro_strategies.paper.bar_bus import sanitize_intraday_bars
from tradepro_strategies.paper.strategy import Bar


def _bars(closes: list[float], symbol: str = "UNH") -> list[Bar]:
    t0 = datetime(2026, 7, 3, 14, 30, tzinfo=timezone.utc)
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(
            symbol=symbol, timestamp=t0 + timedelta(minutes=i),
            open=c, high=c, low=c, close=c, volume=1000, timeframe_seconds=60,
        ))
    return out


def _closes(bars: list[Bar]) -> list[float]:
    return [b.close for b in bars]


def test_isolated_down_spike_dropped():
    # UNH ~500 with a phantom 377 (~-25%) that reverts — the false-stop case.
    bars = _bars([500, 501, 377, 502, 503])
    assert _closes(sanitize_intraday_bars(bars)) == [500, 501, 502, 503]


def test_isolated_up_spike_dropped():
    bars = _bars([100, 100.5, 130, 101, 101.5])  # +29% lone spike
    assert _closes(sanitize_intraday_bars(bars)) == [100, 100.5, 101, 101.5]


def test_genuine_trend_untouched():
    # A steady climb — no bar is isolated from BOTH neighbours; keep all.
    bars = _bars([100, 103, 106, 109, 112])
    assert _closes(sanitize_intraday_bars(bars)) == [100, 103, 106, 109, 112]


def test_genuine_gap_that_holds_untouched():
    # A real +15% gap that PERSISTS (news): neighbours don't agree across it,
    # so it's not an isolated spike — must be kept.
    bars = _bars([100, 100, 115, 116, 117])
    assert _closes(sanitize_intraday_bars(bars)) == [100, 100, 115, 116, 117]


def test_small_moves_untouched():
    bars = _bars([100, 102, 105, 103, 106])  # all < 12% steps
    assert _closes(sanitize_intraday_bars(bars)) == [100, 102, 105, 103, 106]


def test_too_short_unchanged():
    bars = _bars([100, 377])
    assert _closes(sanitize_intraday_bars(bars)) == [100, 377]
