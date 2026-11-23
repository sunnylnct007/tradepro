"""A news read that FAILED is not "no news" (20 Nov 2026).

In the worst AI sell-off week of the year the desk's news read returned zero
headlines for NVDA, AMD and SPY: Yahoo answered "currently down", the Finnhub
fallback had no key on the Mac, and both failures were swallowed into [].
"""
from __future__ import annotations

import logging

from tradepro_strategies import news
from tradepro_strategies.news import NewsItem


def _item(t="Nvidia slides"):
    return NewsItem(title=t, publisher="Reuters", link=None,
                    published_at="2026-11-20T15:00:00+00:00", thumbnail=None)


def test_every_source_down_is_a_failure_with_reasons(monkeypatch):
    monkeypatch.setattr(news, "_fetch_yahoo", lambda s, l, a: ([], "yahoo: YFDataException: down"))
    monkeypatch.setattr(news, "_finnhub", lambda s, l, a: ([], "finnhub via api: HTTP 502"))
    r = news.read_news("NVDA")
    assert r.failed and r.items == []
    assert r.errors == ["yahoo: YFDataException: down", "finnhub via api: HTTP 502"]


def test_yahoo_down_falls_through_to_finnhub(monkeypatch):
    monkeypatch.setattr(news, "_fetch_yahoo", lambda s, l, a: ([], "yahoo: down"))
    monkeypatch.setattr(news, "_finnhub", lambda s, l, a: ([_item()], None))
    r = news.read_news("NVDA")
    assert not r.failed and r.source == "finnhub" and len(r.items) == 1
    assert r.errors == ["yahoo: down"]      # the failed source is still named


def test_a_quiet_source_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(news, "_fetch_yahoo", lambda s, l, a: ([], None))
    monkeypatch.setattr(news, "_finnhub", lambda s, l, a: ([], None))
    r = news.read_news("XYZ")
    assert not r.failed and r.items == []


def test_fetch_news_logs_a_failed_read_loudly(monkeypatch, caplog):
    monkeypatch.setattr(news, "_fetch_yahoo", lambda s, l, a: ([], "yahoo: down"))
    monkeypatch.setattr(news, "_finnhub", lambda s, l, a: ([], "finnhub via api: HTTP 502"))
    with caplog.at_level(logging.WARNING, logger="tradepro.news"):
        assert news.fetch_news("AMD") == []
    assert "could NOT be read" in caplog.text and "HTTP 502" in caplog.text


def test_no_local_key_uses_the_api(monkeypatch):
    monkeypatch.delenv("TRADEPRO_FINNHUB_API_KEY", raising=False)
    called = {}
    monkeypatch.setattr(news, "_finnhub_via_api",
                        lambda s, l, d: (called.setdefault("sym", s), ([_item()], None))[1])
    items, err = news._finnhub("AMD", 8, 14)
    assert called["sym"] == "AMD" and err is None and len(items) == 1


def test_mcp_tool_says_could_not_read(monkeypatch):
    from tradepro_strategies.mcp import tools
    monkeypatch.setattr(news, "_fetch_yahoo", lambda s, l, a: ([], "yahoo: down"))
    monkeypatch.setattr(news, "_finnhub", lambda s, l, a: ([], "finnhub via api: HTTP 502"))
    out = tools.get_news_with_sentiment("SPY")
    assert out["ok"] is False and "NOT 'no news'" in out["error"]
    assert out["news_read"]["errors"]


def test_the_via_api_fallback_uses_the_mcp_containers_api_url_first(monkeypatch):
    """23 Nov 2026: the remote MCP container has no ~/.tradepro/credentials;
    its Finnhub fallback must resolve the API from TRADEPRO_API_URL like
    every other MCP tool, else Yahoo-blocked + credentials-missing reads as
    'no news' for a symbol in a sell-off."""
    monkeypatch.setenv("TRADEPRO_API_URL", "http://api.test")
    monkeypatch.setenv("TRADEPRO_API_TOKEN", "tok")
    base, token = news._api_base_and_token()
    assert base == "http://api.test" and token == "tok"
    monkeypatch.delenv("TRADEPRO_API_URL")
    monkeypatch.setattr("tradepro_strategies.cli.push_to_api.load_credentials", lambda: ("http://laptop", "t2"))
    assert news._api_base_and_token() == ("http://laptop", "t2")
