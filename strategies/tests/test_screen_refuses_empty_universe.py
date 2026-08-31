"""A screen with no universe must STOP, not publish an empty board.

THE DEFECT, 31 Aug 2026 — the first real run of the puts screen on Lambda.

`universe.py` resolves the universe as `parents[1]/"universe"/"tradeable.json"`,
a SIBLING of the tradepro_strategies package. `Dockerfile.lambda` copied the
package and the handler, so the image shipped with no universe file at all.

`harvest_symbols()` asks for it with `strict=False`, which returns an empty list
rather than raising. That is defensible for a harvest — it can still refresh
whatever the store holds — and wrong for a screen, which then evaluates nobody.

What the run actually did:

    scanned 0 recent reporters · 0 candidate(s)
    push -> HTTP 200
    result: {'ok': True, 'rc': 0}

Nine seconds, exit zero, no warning — and it REPLACED a board that had a priced
MRVL candidate on it. The same run locally scans 8 reporters and finds one.

This is the worst shape available: not a crash, not a refusal, but a confident
empty answer that overwrites a correct one. `load_universe` already states the
rule in its own docstring — "A missing universe file must stop a screen, not
silently restore the behaviour this module exists to end" — and this was the one
call site that had opted out of it.

The guard is deliberately at the SYMBOL COUNT, not at the candidate count. Zero
candidates from 244 names is a normal quiet day and must still publish. Zero
NAMES means the screen never ran.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli import post_earnings_puts as P


def test_an_empty_universe_raises_rather_than_returning_nothing(monkeypatch):
    """THE regression. Must not return {} and let the caller publish."""
    monkeypatch.setattr(P, "harvest_symbols", lambda *a, **kw: [], raising=False)
    import tradepro_strategies.universe as U
    monkeypatch.setattr(U, "harvest_symbols", lambda *a, **kw: [])

    with pytest.raises(RuntimeError) as ei:
        P._recent_reports("http://api.test")

    msg = str(ei.value)
    assert "no universe to screen" in msg, msg
    # The message must name the consequence, not just the condition — the whole
    # failure was that nobody could see what publishing would have destroyed.
    assert "empty board" in msg, msg
    assert "build_universe" in msg, msg


def test_a_populated_universe_does_not_raise(monkeypatch):
    """The guard must not fire on the normal path."""
    import tradepro_strategies.universe as U
    monkeypatch.setattr(U, "harvest_symbols", lambda *a, **kw: ["AAPL", "MSFT"])
    monkeypatch.setattr(
        "tradepro_strategies.earnings._calendar_store_events",
        lambda sym, base, **kw: {"store": {"totalRows": 10, "symbols": 5},
                                 "events": [{"report_date": "2026-08-27"}]})
    monkeypatch.setattr(
        "tradepro_strategies.earnings._store_is_authoritative", lambda meta: True)

    out = P._recent_reports("http://api.test")
    assert out == {"AAPL": "2026-08-27", "MSFT": "2026-08-27"}, out


def test_zero_candidates_from_a_real_universe_is_still_allowed(monkeypatch):
    """A quiet day is NOT the failure. The guard is on the symbol count, not the
    candidate count — 244 names and no qualifier is the rule working."""
    import tradepro_strategies.universe as U
    monkeypatch.setattr(U, "harvest_symbols", lambda *a, **kw: ["AAPL", "MSFT"])
    monkeypatch.setattr(
        "tradepro_strategies.earnings._calendar_store_events",
        lambda sym, base, **kw: {"store": {"totalRows": 10, "symbols": 5},
                                 "events": []})          # nobody has reported
    monkeypatch.setattr(
        "tradepro_strategies.earnings._store_is_authoritative", lambda meta: True)

    assert P._recent_reports("http://api.test") == {}


def test_the_universe_file_lives_beside_the_package_not_inside_it():
    """Pins the fact that made the container wrong. If this ever moves INSIDE
    tradepro_strategies, the extra COPY in Dockerfile.lambda becomes dead and
    should go with it."""
    import pathlib

    import tradepro_strategies
    from tradepro_strategies.universe import universe_path

    pkg = pathlib.Path(tradepro_strategies.__file__).resolve().parent
    assert not str(universe_path().resolve()).startswith(str(pkg)), (
        "the universe now lives inside the package — Dockerfile.lambda's "
        "separate `COPY universe` is redundant and should be removed")
