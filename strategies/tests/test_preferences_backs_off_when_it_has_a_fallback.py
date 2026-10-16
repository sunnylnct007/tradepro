"""The failure cooldown must apply when we HAVE a stale snapshot, not only when we don't.

THE BUG, 16 Oct 2026. The guard read:

    if self._snapshot is None and self._last_failure_at > 0 and <within cooldown>:
        return None

so the cooldown applied ONLY when there was nothing to fall back on. Backwards:
it backed off when it had no answer and hammered the backend when it had a
perfectly good stale one.

WHAT IT COST. The API was unreachable for ~26 minutes (08:40-09:06). The
momentum T212 lane made 1,208 preference fetches in that window — one per
symbol per bar, each paying a connection timeout, each logging twice (2,416
lines). The pass started at 08:40 and was still running five hours later, so
the lane never completed a pass, never pushed a snapshot, and the desk
correctly reported it as a stopped sleeve. A retry policy took a trading lane
down for a day.
"""
from __future__ import annotations

from tradepro_strategies.bar_cache.preferences import PreferencesLoader


class _Resp:
    ok = True
    status_code = 200
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


def _client(responses):
    """responses: list of payloads or Exception instances, consumed in order."""
    calls = {"n": 0}

    def _get(url, headers=None, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        r = responses[min(i, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return _Resp(r)

    c = PreferencesLoader("http://x", _http_get=_get, ttl_seconds=0.0)
    return c, calls


GOOD = {"preferences": [], "validProviders": ["ibkr", "yfinance"]}


def test_a_warm_client_stops_hammering_during_an_outage():
    """The 1,208-fetch case, in one test."""
    c, calls = _client([GOOD] + [ConnectionError("API down")] * 50)
    assert c.valid_providers() == ["ibkr", "yfinance"]      # 1 call: warms it
    first = calls["n"]
    for _ in range(30):                                     # the outage
        c.valid_providers()
    extra = calls["n"] - first
    assert extra <= 2, (
        f"made {extra} network attempts across 30 lookups during an outage — "
        "the cooldown is being skipped on the warm path again, which is what "
        "stalled the T212 lane for five hours")


def test_the_stale_snapshot_is_STILL_served_during_the_cooldown():
    """Backing off must not mean returning nothing — that would push every
    BarStore onto the default chain during a brief blip."""
    c, _ = _client([GOOD] + [ConnectionError("API down")] * 10)
    c.valid_providers()
    c.valid_providers()                                     # trips the failure
    assert c.valid_providers() == ["ibkr", "yfinance"], "stale snapshot was dropped"


def test_a_cold_client_still_returns_None_and_does_not_hammer():
    """No snapshot to serve: the caller falls back to the default chain, and we
    still must not retry per call."""
    c, calls = _client([ConnectionError("API down")] * 50)
    assert c.valid_providers() is None
    first = calls["n"]
    for _ in range(20):
        assert c.valid_providers() is None
    assert calls["n"] - first <= 2, "cold path lost its cooldown"


def test_recovery_still_works_once_the_cooldown_expires():
    """A cooldown that never lets go would be its own outage."""
    c, _ = _client([ConnectionError("API down"), GOOD])
    assert c.valid_providers() is None                      # cold + failed
    c._last_failure_at = 0.0                                # cooldown expires
    assert c.valid_providers() == ["ibkr", "yfinance"]
