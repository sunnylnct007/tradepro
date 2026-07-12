"""Unit tests for technical indicators."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from indicators import sma, rsi, avg_volume, recent_avg_volume


def _flat_bars(n, price=100.0, volume=500_000):
    return [{"t": i, "o": price, "h": price, "l": price, "c": price, "v": volume} for i in range(n)]


def _rising_bars(n, start=80.0, end=120.0):
    bars = []
    for i in range(n):
        p = start + (end - start) * i / (n - 1)
        bars.append({"t": i, "o": p, "h": p, "l": p, "c": p, "v": 1_000_000})
    return bars


class TestSMA:
    def test_flat(self):
        bars = _flat_bars(50, 100)
        assert sma(bars, 50) == 100.0

    def test_insufficient_bars(self):
        assert sma(_flat_bars(10), 50) is None

    def test_rising(self):
        bars = _rising_bars(50, 80, 120)
        result = sma(bars, 50)
        assert 99 < result < 101  # midpoint of 80–120


class TestRSI:
    def test_flat_bars_rsi_50(self):
        # Flat bars → no gains or losses → RSI should handle gracefully
        bars = _flat_bars(30, 100)
        result = rsi(bars, 14)
        assert result == 100.0  # avg_loss == 0 → RSI = 100

    def test_rising_bars_high_rsi(self):
        bars = _rising_bars(30, 80, 120)
        result = rsi(bars, 14)
        assert result is not None
        assert result > 60

    def test_insufficient_returns_none(self):
        assert rsi(_flat_bars(5), 14) is None


class TestVolume:
    def test_avg_volume(self):
        bars = _flat_bars(100, volume=1_000_000)
        assert avg_volume(bars, 20) == 1_000_000.0

    def test_recent_avg_volume(self):
        bars = _flat_bars(20, volume=500_000)
        bars[-1]["v"] = 2_000_000
        bars[-2]["v"] = 2_000_000
        bars[-3]["v"] = 2_000_000
        assert recent_avg_volume(bars, 3) == 2_000_000.0
