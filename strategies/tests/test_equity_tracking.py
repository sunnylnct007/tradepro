"""Tracking-verdict logic: honest, conservative live-vs-backtest assessment."""
from tradepro_strategies.quant_engine.equity_tracking import assess_tracking

EXP = dict(expected_cagr_pct=8.9, drawdown_bound_pct=-13.0, min_days=20, band_pct=6.0)


def test_too_early_refuses_to_judge():
    v = assess_tracking(2.0, elapsed_days=5, **EXP)
    assert v.status == "TOO_EARLY"  # noise — no false-positive verdict


def test_drawdown_breach_flags_even_when_early():
    # A risk-profile failure is flagged regardless of how few days elapsed.
    v = assess_tracking(-15.0, elapsed_days=3, **EXP)
    assert v.status == "DRAWDOWN_BREACH"


def test_on_track_within_band():
    # ~180 days → expected ≈ +4.3%; actual +5% is within the ±6pp band.
    v = assess_tracking(5.0, elapsed_days=180, **EXP)
    assert v.status == "ON_TRACK", v


def test_behind_when_far_below_expectation():
    v = assess_tracking(-4.0, elapsed_days=180, **EXP)
    assert v.status == "BEHIND"


def test_ahead_when_far_above_expectation():
    v = assess_tracking(20.0, elapsed_days=180, **EXP)
    assert v.status == "AHEAD"
