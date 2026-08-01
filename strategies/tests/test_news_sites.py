"""Config-driven news-site crawl — the earnings-verification fallback."""
import datetime as d
import email.utils
from unittest.mock import patch, MagicMock

from tradepro_strategies import news_sites as ns


def _rss(items):
    rows = "".join(
        f"<item><title>{t}</title><pubDate>{email.utils.format_datetime(p)}</pubDate></item>"
        for t, p in items)
    return f'<?xml version="1.0"?><rss><channel>{rows}</channel></rss>'.encode()


def _mock_urlopen(payload):
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = payload
    return cm


def test_recent_earnings_mention_true():
    now = d.datetime.now(d.timezone.utc)
    xml = _rss([("Mastercard beats estimates as Q2 results top forecasts", now)])
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(xml)):
        assert ns.recent_earnings_mention("MA") is True


def test_old_or_unrelated_headlines_false():
    old = d.datetime.now(d.timezone.utc) - d.timedelta(days=30)
    now = d.datetime.now(d.timezone.utc)
    xml = _rss([
        ("Mastercard reports second-quarter earnings", old),   # too old
        ("Mastercard launches new card design", now),          # not earnings
    ])
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(xml)):
        assert ns.recent_earnings_mention("MA") is False


def test_all_feeds_down_returns_none_not_false():
    # An outage is UNKNOWN, never "no news" (fail-loud).
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        assert ns.recent_earnings_mention("MA") is None


def test_feeds_are_config_driven(monkeypatch):
    monkeypatch.setenv("TRADEPRO_NEWS_FEEDS",
                       "https://a.example/rss?s={symbol}, https://b.example/{symbol}.xml")
    assert ns.configured_feeds() == [
        "https://a.example/rss?s={symbol}", "https://b.example/{symbol}.xml"]
    monkeypatch.delenv("TRADEPRO_NEWS_FEEDS")
    assert list(ns.configured_feeds()) == list(ns.DEFAULT_FEEDS)
