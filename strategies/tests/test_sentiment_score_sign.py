"""Sentiment score sign-reconciliation — pins the guard that protects the
demotion logic from models that label correctly but sign the number wrong.

Context: `tradepro-sentiment-score` stores the LLM's numeric score, and
compare.py's sentiment demotion keys off that number (mean ≤ -0.30 → BUY→WAIT).
gemma3:12b was observed returning the right LABEL with the wrong SIGN (e.g.
classification="negative", score=+0.75). _reconcile_score_sign makes the label
authoritative for the sign so a negative headline can never store a positive
sentiment (which would silently let a BUY through).
"""
from tradepro_strategies.cli.sentiment_score import _reconcile_score_sign


def test_negative_label_with_positive_number_flips_sign():
    # The gemma bug we caught: "negative" + +0.75 must not store positive.
    assert _reconcile_score_sign("negative", 0.75) == -0.75
    assert _reconcile_score_sign("strongly_negative", 0.9) == -0.9


def test_positive_label_with_negative_number_flips_sign():
    assert _reconcile_score_sign("positive", -0.3) == 0.3
    assert _reconcile_score_sign("strongly_positive", -0.6) == 0.6


def test_agreeing_sign_is_unchanged():
    assert _reconcile_score_sign("positive", 0.6) == 0.6
    assert _reconcile_score_sign("negative", -0.4) == -0.4


def test_neutral_is_passed_through():
    assert _reconcile_score_sign("neutral", 0.2) == 0.2
    assert _reconcile_score_sign("neutral", -0.2) == -0.2


def test_near_zero_magnitude_uses_per_tier_fallback():
    # A non-neutral label with a ~0 number must not be silently zeroed.
    assert _reconcile_score_sign("negative", 0.0) == -0.45
    assert _reconcile_score_sign("strongly_positive", -0.01) == 0.85


def test_sign_is_consistent_for_every_classification():
    for cls in ("strongly_negative", "negative"):
        assert _reconcile_score_sign(cls, 0.5) < 0
    for cls in ("positive", "strongly_positive"):
        assert _reconcile_score_sign(cls, -0.5) > 0
