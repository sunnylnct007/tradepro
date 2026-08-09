"""Regression coverage for `today_setups._why()`'s "consider" narrative.

Before 2026-08-08 every ⭐ CONSIDER name got the literal phrase "support
hold, not a knife" regardless of the volume_ratio it was ALSO flagged with
two clauses later in the same sentence — so a THIN (0.4x volume) name
claimed confirmed buyer demand in one breath and flagged no real
participation in the next. The claim is now conditional on the same
thin_volume flag the sentence already reports.
"""
from __future__ import annotations

from tradepro_strategies.cli.today_setups import _why


def _consider(**overrides) -> dict:
    base = dict(
        classification="consider", signal="BUY", kijun=100.0, dist_atr=0.2,
        off_10d_high_pct=-2.3, range_pctile=54, momentum_3m_pct=7.0,
        momentum_10d_pct=1.0, atr_pct=2.3, volume_ratio=1.1, thin_volume=False,
    )
    base.update(overrides)
    return base


def test_confirmed_volume_gets_support_hold_claim():
    text = _why(_consider(thin_volume=False, volume_ratio=1.1))
    assert "support hold, not a knife" in text


def test_thin_volume_does_not_get_support_hold_claim():
    text = _why(_consider(thin_volume=True, volume_ratio=0.45))
    assert "support hold, not a knife" not in text
    assert "hasn't confirmed" in text


def test_thin_volume_narrative_still_reports_the_thin_flag():
    # The sentence must not contradict itself: no demand-confirmed claim
    # AND the THIN flag both present at once.
    text = _why(_consider(thin_volume=True, volume_ratio=0.45))
    assert "THIN" in text
    assert "support hold, not a knife" not in text


def test_non_consider_classifications_are_unaffected():
    extended = dict(classification="extended", range_pctile=95, momentum_3m_pct=80.0, kijun=100.0)
    text = _why(extended)
    assert "EXTENDED" in text
