"""The screen's publish must survive a network blink.

20 Oct 2026: the screen ran, computed all 82 rows, and threw the day away —

    NameResolutionError: Failed to resolve 'tradepro.showsoldprice.com'
    wheel screen: FAILED (exit 1)

The host was fine either side of it; the Mac's DNS blinked. One unretried POST
at the end of a multi-minute job turned a blink into a lost trading day, and
the owner opened a board showing the previous run's date.
"""
import json

import pytest

from tradepro_strategies.cli import options_screen as osc

ROWS = [{"symbol": "MSFT", "eligible": True}, {"symbol": "KO", "eligible": False}]
PAYLOAD = {"generated_at_utc": "2026-10-21T09:00:00Z", "candidates": ROWS}


class _Resp:
    status_code = 200


def test_a_transient_failure_is_retried_and_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("Failed to resolve 'tradepro.showsoldprice.com'")
        return _Resp()

    monkeypatch.setattr(osc.requests, "post", flaky)
    monkeypatch.setattr(osc.time, "sleep", lambda s: None)
    r = osc._publish_with_retry("http://x/api", PAYLOAD, {}, ROWS)
    assert r.status_code == 200
    assert calls["n"] == 3, "should have retried until it got through"


def test_the_first_attempt_is_not_delayed(monkeypatch):
    # A healthy publish must not pay the retry cost.
    slept = []
    monkeypatch.setattr(osc.requests, "post", lambda url, **kw: _Resp())
    monkeypatch.setattr(osc.time, "sleep", lambda s: slept.append(s))
    osc._publish_with_retry("http://x/api", PAYLOAD, {}, ROWS)
    assert slept == []


def test_total_failure_still_raises(monkeypatch, tmp_path):
    # DELIBERATE. A screen that could not publish has NOT succeeded. Swallowing
    # it would convert a visible failure into a silently stale board, which is
    # far worse — the owner would read yesterday's numbers as today's.
    monkeypatch.setattr(osc.requests, "post",
                        lambda url, **kw: (_ for _ in ()).throw(OSError("dns")))
    monkeypatch.setattr(osc.time, "sleep", lambda s: None)
    monkeypatch.setattr(osc, "_PUBLISH_SPOOL", tmp_path / "unpublished")
    with pytest.raises(OSError):
        osc._publish_with_retry("http://x/api", PAYLOAD, {}, ROWS)


def test_total_failure_keeps_the_work(monkeypatch, tmp_path):
    spool = tmp_path / "unpublished"
    monkeypatch.setattr(osc.requests, "post",
                        lambda url, **kw: (_ for _ in ()).throw(OSError("dns")))
    monkeypatch.setattr(osc.time, "sleep", lambda s: None)
    monkeypatch.setattr(osc, "_PUBLISH_SPOOL", spool)
    with pytest.raises(OSError):
        osc._publish_with_retry("http://x/api", PAYLOAD, {}, ROWS)
    files = list(spool.glob("options-candidates-*.json"))
    assert len(files) == 1, "a day's screen must be recoverable, not destroyed"
    assert json.loads(files[0].read_text())["candidates"] == ROWS


def test_a_spool_failure_does_not_mask_the_real_error(monkeypatch, tmp_path):
    # If the disk is also unavailable, the caller must still see the NETWORK
    # error — that is the diagnosis. A spool problem is a footnote.
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("x")
    monkeypatch.setattr(osc.requests, "post",
                        lambda url, **kw: (_ for _ in ()).throw(OSError("dns blink")))
    monkeypatch.setattr(osc.time, "sleep", lambda s: None)
    monkeypatch.setattr(osc, "_PUBLISH_SPOOL", blocked / "sub")
    with pytest.raises(OSError, match="dns blink"):
        osc._publish_with_retry("http://x/api", PAYLOAD, {}, ROWS)


def test_an_http_error_status_is_not_retried(monkeypatch):
    # A 500 is the SERVER's answer, not a transport failure. Retrying it would
    # hammer a struggling API; the status is returned and logged as-is.
    calls = {"n": 0}

    class _Err:
        status_code = 500

    def once(url, **kw):
        calls["n"] += 1
        return _Err()

    monkeypatch.setattr(osc.requests, "post", once)
    monkeypatch.setattr(osc.time, "sleep", lambda s: None)
    r = osc._publish_with_retry("http://x/api", PAYLOAD, {}, ROWS)
    assert r.status_code == 500 and calls["n"] == 1
