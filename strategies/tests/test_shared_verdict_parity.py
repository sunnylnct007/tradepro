"""Parity + invariants for the extracted canonical verdict module.

Two jobs:
  1. Pin the centralized threshold constants to their values, so the
     single-source-of-truth can't silently drift (the whole point of
     extracting them out of scattered magic default-args).
  2. Exercise the documented verdict CHAIN ordering (compute_bucket →
     sentiment_demotion → swing_strict → horizon_and_range → conviction
     → cap → earnings) end-to-end on representative rows, so a future
     reorder/regression is caught here, not on a live trading surface.
"""
from tradepro_strategies import shared_verdict as sv


# --- 1. thresholds are the single source of truth ---------------------- #

def test_threshold_constants_pinned():
    assert sv.VOLUME_CONFIRM_RATIO == 1.2
    assert sv.EARNINGS_SUPPRESS_DAYS == 7
    assert sv.SENTIMENT_WAIT_MEAN == -0.30
    assert sv.SENTIMENT_WAIT_MIN_NEG == 2
    assert sv.SENTIMENT_AVOID_MEAN == -0.45
    assert sv.SENTIMENT_AVOID_MIN_NEG == 3
    assert sv.SWING_STRICT_MEAN == -0.05
    assert sv.SWING_STRICT_NEUTRAL_BAND == 0.10
    assert sv.EXTREME_RANGE_PCT == 85.0


def _run_chain(*, price_verdict, long_count, total, market_state,
               mean=None, mat_neg=None, horizon=None, range_pct=None,
               days_until_earnings=None):
    """Mirror of compare._attach_bucket_and_rationale's verdict chain."""
    bucket, reason = sv.compute_bucket(
        price_verdict=price_verdict, price_reason=None,
        long_count=long_count, total=total)
    bucket, reason, sent_dem = sv.apply_sentiment_demotion(
        bucket=bucket, reason=reason, mean=mean, material_negative_count=mat_neg)
    bucket, reason, swing_dem = sv.apply_swing_strict_demotion(
        bucket=bucket, reason=reason, mean=mean, material_negative_count=mat_neg)
    bucket, reason, horizon_dem = sv.apply_horizon_and_range_demotion(
        bucket=bucket, reason=reason, horizon_classification=horizon,
        range_pct=range_pct)
    conviction, _ = sv.compute_conviction(
        bucket=bucket, market_state=market_state,
        sentiment_demoted=sent_dem, horizon_demoted=(swing_dem or horizon_dem))
    bucket, reason, _ = sv.cap_bucket_at_low_conviction(
        bucket=bucket, reason=reason, conviction=conviction)
    bucket, reason, conviction, _ = sv.apply_earnings_suppressor(
        bucket=bucket, reason=reason, conviction=conviction,
        days_until_earnings=days_until_earnings)
    return bucket, conviction


_TREND_OK = {"above_sma_200": True, "ichimoku_cloud_position": "ABOVE_CLOUD",
             "volume_ratio_20d": 1.5}


def test_clean_buy_with_volume_is_high_conviction_buy():
    bucket, conv = _run_chain(
        price_verdict="BUY", long_count=4, total=5, market_state=_TREND_OK)
    assert bucket == "BUY"
    assert conv == "HIGH"


def test_hostile_sentiment_forces_avoid():
    bucket, _ = _run_chain(
        price_verdict="BUY", long_count=4, total=5, market_state=_TREND_OK,
        mean=-0.6, mat_neg=4)
    assert bucket == "AVOID"


def test_buy_into_earnings_window_is_suppressed_to_wait():
    bucket, _ = _run_chain(
        price_verdict="BUY", long_count=4, total=5, market_state=_TREND_OK,
        days_until_earnings=3)
    assert bucket == "WAIT"


def test_broken_trend_caps_buy_at_wait_low_conviction():
    broken = {"above_sma_200": False, "ichimoku_cloud_position": "BELOW_CLOUD"}
    bucket, conv = _run_chain(
        price_verdict="BUY", long_count=4, total=5, market_state=broken)
    assert conv == "LOW"
    assert bucket == "WAIT"   # BUG-001 conviction veto


def test_buy_at_top_of_range_without_swing_buy_demotes_to_wait():
    bucket, _ = _run_chain(
        price_verdict="BUY", long_count=4, total=5, market_state=_TREND_OK,
        horizon={"swing": {"signal": "WATCH"}}, range_pct=96.0)
    assert bucket == "WAIT"
