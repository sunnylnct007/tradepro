"""Unit tests for swing screener gate + scoring logic."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import datetime
import pytest
from swing_screener import score_swing


def _bars(n=252, price=100.0, trend="up", volume=1_000_000):
    """Realistic bars: gentle uptrend with daily oscillation so RSI stays in range."""
    import math
    bars = []
    p = price * 0.85
    daily_drift = (price - p) / n * 0.6
    for i in range(n):
        # Small oscillation so RSI is realistic (not 100)
        noise = math.sin(i * 0.5) * price * 0.008
        p = p + daily_drift + noise
        p = max(p, price * 0.5)
        bars.append({"t": i, "o": p, "h": p * 1.005, "l": p * 0.995, "c": p, "v": volume})
    return bars


def _spy_bars():
    return _bars(252, 500)


def _future_earnings(days=30):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


class TestGate:
    def test_earnings_blackout_fails(self):
        soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        c = score_swing("AAPL", 150, _bars(), _spy_bars(), soon)
        assert not c.passed_gate()
        assert "earnings" in c.gate_fail_reason

    def test_low_volume_fails(self):
        c = score_swing("T", 50, _bars(volume=100_000), _spy_bars(), _future_earnings())
        assert not c.passed_gate()
        assert "avg vol" in c.gate_fail_reason

    def test_price_too_low_fails(self):
        c = score_swing("T", 5, _bars(price=5), _spy_bars(), _future_earnings())
        assert not c.passed_gate()

    def test_good_stock_passes(self):
        c = score_swing("AAPL", 150, _bars(), _spy_bars(), _future_earnings())
        assert c.passed_gate()
        assert c.score >= 5


class TestScoring:
    def test_uptrend_scores_trend_points(self):
        c = score_swing("AAPL", 150, _bars(), _spy_bars(), _future_earnings())
        assert c.passed_gate()
        # Uptrend bars → price above 50d and 200d MA → 3 pts for trend
        assert "ABOVE" in c.trend_status

    def test_minimum_score_returned(self):
        c = score_swing("AAPL", 150, _bars(), _spy_bars(), _future_earnings())
        if c.passed_gate():
            assert c.score >= 5
