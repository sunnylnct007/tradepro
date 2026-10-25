"""The delivery path, which has lost four boards in a week.

    19 Oct  publish failed
    20 Oct  publish failed
    22 Oct  lane did not run
    23 Oct  publish failed — 338 names computed, board read 21 Oct for two days

Two faults, both fixed here. The retry could not fail fast, so four attempts
spanned 38 minutes entirely inside the outage. And the spool was a morgue: the
correct board sat on disk while the owner read a stale one.
"""
import json

import pytest

from tradepro_strategies.cli import options_screen as osc

ROWS = [{"symbol": "MSFT", "eligible": True}]


class _Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b


# ── failing fast ──────────────────────────────────────────────────────────
def test_a_hanging_attempt_is_abandoned_not_waited_on(monkeypatch):
    """THE 23 OCT BUG. requests' timeout governs the SOCKET, not getaddrinfo,
    so a DNS hang ignored it: attempt 1 at 16:43:04, attempt 2 at 17:11:20."""
    import time as _t
    calls = {"n": 0}

    def hangs(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            _t.sleep(5)          # longer than the deadline below
        return _Resp()

    monkeypatch.setattr(osc, "_PUBLISH_ATTEMPT_DEADLINE_S", 0.3)
    monkeypatch.setattr(osc, "_PUBLISH_BACKOFF_S", 0.01)
    monkeypatch.setattr(osc.requests, "post", hangs)
    started = _t.monotonic()
    r = osc._publish_with_retry("http://x/api", {}, {}, ROWS)
    elapsed = _t.monotonic() - started
    assert r.status_code == 200
    assert elapsed < 3, (
        f"a hung attempt held the retry for {elapsed:.1f}s — it must be "
        f"abandoned at the deadline, not joined")


def test_a_healthy_publish_is_not_delayed_by_the_deadline(monkeypatch):
    monkeypatch.setattr(osc.requests, "post", lambda *a, **kw: _Resp())
    assert osc._publish_with_retry("http://x/api", {}, {}, ROWS).status_code == 200


# ── self-healing ──────────────────────────────────────────────────────────
def _spool(tmp_path, monkeypatch, stamp):
    d = tmp_path / "unpublished"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"options-candidates-{stamp}.json").write_text(json.dumps(
        {"generated_at_utc": stamp, "candidates": ROWS}))
    monkeypatch.setattr(osc, "_PUBLISH_SPOOL", d)
    return d


def test_a_newer_spooled_board_is_republished(tmp_path, monkeypatch):
    d = _spool(tmp_path, monkeypatch, "2026-10-23T14:43:04")
    posted = []
    monkeypatch.setattr(osc.requests, "get",
                        lambda *a, **kw: _Resp(200, {"generated_at_utc": "2026-10-21T15:15:06"}))
    monkeypatch.setattr(osc.requests, "post",
                        lambda *a, **kw: (posted.append(kw.get("json")), _Resp())[1])
    assert osc._drain_spool("http://x/api", {}) == 1
    assert posted and posted[0]["generated_at_utc"] == "2026-10-23T14:43:04"
    assert not list(d.glob("*.json")), "a published spool file must not re-send"


def test_a_STALE_spooled_board_never_overwrites_a_fresher_one(tmp_path, monkeypatch):
    """The one way this could do harm. Compared on the payload's own stamp."""
    _spool(tmp_path, monkeypatch, "2026-10-19T21:19:23")
    monkeypatch.setattr(osc.requests, "get",
                        lambda *a, **kw: _Resp(200, {"generated_at_utc": "2026-10-23T14:43:04"}))
    monkeypatch.setattr(osc.requests, "post",
                        lambda *a, **kw: pytest.fail("published a stale board backwards"))
    assert osc._drain_spool("http://x/api", {}) == 0


def test_an_unreadable_live_board_publishes_NOTHING(tmp_path, monkeypatch):
    # Not knowing what is live means not knowing whether we would go backwards.
    _spool(tmp_path, monkeypatch, "2026-10-23T14:43:04")
    monkeypatch.setattr(osc.requests, "get",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("dns")))
    monkeypatch.setattr(osc.requests, "post",
                        lambda *a, **kw: pytest.fail("published without knowing the live stamp"))
    assert osc._drain_spool("http://x/api", {}) == 0


def test_a_failed_republish_leaves_the_file_for_next_time(tmp_path, monkeypatch):
    d = _spool(tmp_path, monkeypatch, "2026-10-23T14:43:04")
    monkeypatch.setattr(osc.requests, "get",
                        lambda *a, **kw: _Resp(200, {"generated_at_utc": "2026-10-21T15:15:06"}))
    monkeypatch.setattr(osc.requests, "post", lambda *a, **kw: _Resp(503))
    assert osc._drain_spool("http://x/api", {}) == 0
    assert list(d.glob("options-candidates-*.json")), "the work must survive"


def test_no_spool_directory_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(osc, "_PUBLISH_SPOOL", tmp_path / "nope")
    assert osc._drain_spool("http://x/api", {}) == 0
