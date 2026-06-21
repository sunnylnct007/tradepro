"""Per-symbol bar-data quality score — 'good for today's decision?'.

Pins the no-false-positives rule for data: stale/missing/partial data must NOT
read as good-for-today. Pure, box-not-needed.
"""
from datetime import date
from tradepro_strategies.bar_cache.quality import score_symbol

ASOF = date(2026, 6, 22)  # Monday


def _q(**kw):
    base = dict(canonical="X", coverage_end=date(2026, 6, 19), provider="ibkr",
                missing_days_count=0, as_of=ASOF)
    base.update(kw)
    return score_symbol(**base)


def test_good_when_fresh_complete_ibkr():
    q = _q()
    assert q.score == "GOOD" and q.good_for_today is True and q.days_behind == 3


def test_missing_when_no_coverage():
    q = _q(coverage_end=None)
    assert q.score == "MISSING" and q.good_for_today is False and q.days_behind is None


def test_stale_when_too_far_behind():
    q = _q(coverage_end=date(2026, 6, 1))  # 21 days behind
    assert q.score == "STALE" and q.good_for_today is False
    assert "pending" in q.reason.lower()


def test_partial_when_internal_gaps():
    q = _q(missing_days_count=3)
    assert q.score == "PARTIAL" and q.good_for_today is False


def test_bronze_when_yfinance_but_fresh():
    q = _q(provider="yfinance")
    assert q.score == "BRONZE" and q.good_for_today is True  # usable, flagged
    assert "not IBKR" in q.reason or "not the credible" in q.reason


def test_weekend_freshness_allowance():
    # Fri close (06-19) read on Mon (06-22) = 3d behind → still GOOD, not STALE.
    q = _q(coverage_end=date(2026, 6, 19))
    assert q.score == "GOOD"
