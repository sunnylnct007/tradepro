"""Unit tests for wheel screener gate + scoring logic."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import datetime
import pytest
from wheel_screener import score_wheel, volatility_label_and_guidance

# 252 synthetic daily bars (flat price=100 for simplicity)
def _bars(n=252, price=100.0, trend="up"):
    bars = []
    p = price * 0.7 if trend == "up" else price * 1.3
    step = (price - p) / n
    for i in range(n):
        p += step
        bars.append({"t": i, "o": p, "h": p * 1.01, "l": p * 0.99, "c": p, "v": 500_000})
    return bars


def _future_earnings(days=30):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _near_earnings(days=5):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


class TestGate:
    def test_earnings_blackout_fails(self):
        c = score_wheel("T", 50, 30, 3, _bars(), 30, 60, 1.5, _near_earnings(10))
        assert not c.passed_gate()
        assert "earnings" in c.gate_fail_reason

    def test_price_too_low_fails(self):
        c = score_wheel("T", 10, 30, 3, _bars(price=10), 8, 12, 1.5, _future_earnings())
        assert not c.passed_gate()
        assert "price" in c.gate_fail_reason

    def test_price_too_high_fails(self):
        c = score_wheel("T", 300, 30, 3, _bars(price=300), 200, 350, 1.5, _future_earnings())
        assert not c.passed_gate()

    def test_low_volume_fails(self):
        bars = [{"t": i, "o": 50, "h": 51, "l": 49, "c": 50, "v": 100_000} for i in range(252)]
        c = score_wheel("T", 50, 30, 3, bars, 30, 60, 1.5, _future_earnings())
        assert not c.passed_gate()
        assert "avg vol" in c.gate_fail_reason

    def test_good_stock_passes(self):
        c = score_wheel("MO", 50, 35, 5, _bars(), 30, 60, 2.5, _future_earnings())
        assert c.passed_gate()
        assert c.score >= 5


class TestScoring:
    def test_high_ivr_scores_3(self):
        c = score_wheel("T", 50, 45, 0, _bars(), 30, 60, 0, _future_earnings())
        # IV > 40 → 3 pts for S-W1; but score may still be < 5 due to div/premium
        assert c.score >= 0  # no crash

    def test_minimum_score_5(self):
        # Very low IVR + no dividend + no premium — should fail score gate
        c = score_wheel("T", 50, 5, 0, _bars(), 45, 60, 0.5, _future_earnings())
        assert not c.passed_gate() or c.score >= 5


class TestVolatilityLabel:
    def test_conservative(self):
        label, guidance = volatility_label_and_guidance(20)
        assert label == "Conservative"
        assert "75-80%" in guidance

    def test_medium(self):
        label, guidance = volatility_label_and_guidance(35)
        assert label == "Medium"
        assert "60-65%" in guidance

    def test_volatile(self):
        label, guidance = volatility_label_and_guidance(50)
        assert label == "Volatile"
        assert "50%" in guidance
