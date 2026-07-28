"""News-catalyst signal — surface event-driven names without fabricating one."""
from tradepro_strategies.gates.catalyst_signal import (
    CatalystConfig,
    detect_news_catalyst,
)

CFG = CatalystConfig()


def _d(today, avg, sent):
    return detect_news_catalyst(articles_today=today, articles_30d_avg=avg, mean_sentiment=sent, cfg=CFG)


def test_spike_positive_is_bullish():
    c = _d(9, 0.3, 0.4)   # 9 vs 0.3 avg = 30×, positive
    assert c.active and c.direction == "bullish" and c.strength > 0


def test_spike_negative_is_bearish():
    c = _d(8, 0.5, -0.3)
    assert c.active and c.direction == "bearish"


def test_spike_flat_sentiment_is_neutral_event():
    c = _d(10, 0.5, 0.02)
    assert c.active and c.direction == "neutral"


def test_no_spike_inactive():
    # 2 articles vs 1.0 avg = 2× < 3× threshold
    c = _d(2, 1.0, 0.5)
    assert not c.active and c.direction == "none"


def test_too_few_articles_inactive():
    # ratio huge (2 vs 0.1) but below the min-articles floor → not a real event
    c = _d(2, 0.1, 0.5)
    assert not c.active


def test_missing_data_inactive_never_fabricated():
    assert not _d(None, None, None).active
    assert not _d(0, 0.0, 0.0).active


def test_baseline_floor_prevents_noise_explosion():
    # 3 articles vs a 0.0 baseline: floored to 0.3 → 10× spike, but sentiment
    # present → active. Confirms the floor is applied (no div-by-zero, no inf).
    c = _d(3, 0.0, 0.3)
    assert c.active and c.spike_ratio == 10.0


def test_none_baseline_inactive_never_fabricates_spike():
    # No real 30d baseline (we don't store news-volume history) → NEVER a spike,
    # even with many articles today + strong sentiment. This is the guard against
    # the fabricated len/30 baseline that made every recent-news name a "catalyst".
    c = detect_news_catalyst(articles_today=9, articles_30d_avg=None,
                             mean_sentiment=0.5, cfg=CFG)
    assert not c.active and c.direction == "none"
    assert "baseline" in c.reason
