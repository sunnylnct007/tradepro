"""Pins the ENTRY-QUALITY semantics of ichimoku_strength_score.

The score was inverted 2026-07-03: it used to rank by raw distance ABOVE the
kijun (rewarding the most EXTENDED names — buying tops, the mechanism behind
intraday_flat's churn). It now rewards PROXIMITY to the kijun (a fresh pullback),
coherent with the Today's Setups scanner. These tests fail loudly if it ever
regresses back to rewarding extension.
"""
from tradepro_strategies.paper.signal_bridge import ichimoku_strength_score


def _meta(kijun=100.0, cloud_top=99.0, cloud_bottom=97.0):
    return {"kijun_val": kijun, "cloud_top": cloud_top, "cloud_bottom": cloud_bottom}


def test_pullback_outranks_extension():
    """A name AT its kijun must score HIGHER than one extended far above it."""
    atr = 2.0
    meta = _meta(kijun=100.0)
    pullback = ichimoku_strength_score(last_close=100.4, metadata=meta, atr=atr)  # ~0.2 ATR above
    extended = ichimoku_strength_score(last_close=110.0, metadata=meta, atr=atr)  # ~5 ATR above
    assert pullback is not None and extended is not None
    assert pullback > extended, f"pullback {pullback} should outrank extended {extended}"


def test_more_extended_scores_lower_monotonically():
    """Score must strictly DECREASE as the name gets more extended above kijun."""
    atr, meta = 2.0, _meta(kijun=100.0)
    scores = [ichimoku_strength_score(last_close=c, metadata=meta, atr=atr)
              for c in (100.5, 102.0, 105.0, 112.0)]
    assert all(a > b for a, b in zip(scores, scores[1:])), scores


def test_below_kijun_is_non_positive():
    """Below the kijun (support breaking) must stay <= 0 so long-only callers drop it
    — the <=0 gate the intraday scanner relies on is preserved."""
    s = ichimoku_strength_score(last_close=98.0, metadata=_meta(kijun=100.0), atr=2.0)
    assert s is not None and s <= 0.0, s


def test_missing_meta_or_atr_returns_none():
    assert ichimoku_strength_score(last_close=100.0, metadata=_meta(), atr=None) is None
    assert ichimoku_strength_score(last_close=100.0, metadata=_meta(), atr=0.0) is None
    assert ichimoku_strength_score(last_close=100.0, metadata={"kijun_val": 100.0}, atr=2.0) is None
