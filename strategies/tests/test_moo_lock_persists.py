"""The MOO once-per-session lock must survive a daemon restart.

THE BUG THIS PINS (found 22 Aug 2026 in the live OMS, not in code review):
`_moo_fired` was an in-memory set, and the paper daemon runs on a */15 cron —
every run is a FRESH PROCESS, so the set was empty each time and the
"at most one decision per symbol per session" contract was void.

KO, 28-29 July 2026, straight from the order log:
    28 Jul  11:13 → 13:29   TEN identical BUY 18 orders, each "superseded by
                            newer order", ~15 minutes apart
    28 Jul  13:37           BUY 15 FILLED @ 89.21   (only 15 of 18 filled)
    29 Jul  07:45 → 11:42   FIFTEEN identical SELL 18 orders, superseded
    29 Jul  15:11           SELL 18 FILLED
    29 Jul  15:22           BUY  18 FILLED  <- bought straight back, 11 min later
    29 Jul  15:22           SELL 15 FILLED @ 90.19

Sold, re-bought and sold again inside eleven minutes. That churn is the live
record: T212 -$376 at a 20% win rate with losses 4.7x wins.

The signal was fixed 2026-06-03. The EXECUTION LOOP never was.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy


@pytest.fixture
def state_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("TRADEPRO_STATE_DIR", d)
        yield Path(d)


def _strategy():
    return IchimokuEquityStrategy(strategy_id="ichimoku_equity")


class TestLockSurvivesRestart:
    def test_a_fresh_process_sees_the_earlier_decision(self, state_dir):
        """THE regression. Two separate strategy objects = two daemon runs."""
        run1 = _strategy()
        run1.on_session_start("2026-07-28")
        run1._moo_fired.add("KO_US_EQ")
        run1._persist_moo_fired("KO_US_EQ")

        run2 = _strategy()                       # the 15-minutes-later process
        run2.on_session_start("2026-07-28")
        assert "KO_US_EQ" in run2._moo_fired, (
            "a restarted daemon must NOT re-decide a symbol it already acted on "
            "— this is exactly what produced 10 duplicate KO BUYs on 28 Jul")

    def test_a_new_session_starts_clean(self, state_dir):
        run1 = _strategy()
        run1.on_session_start("2026-07-28")
        run1._persist_moo_fired("KO_US_EQ")

        run2 = _strategy()
        run2.on_session_start("2026-07-29")       # next session
        assert "KO_US_EQ" not in run2._moo_fired, (
            "the lock is per SESSION — a new day must be free to trade again")

    def test_symbols_are_independent(self, state_dir):
        run1 = _strategy()
        run1.on_session_start("2026-07-28")
        run1._persist_moo_fired("KO_US_EQ")

        run2 = _strategy()
        run2.on_session_start("2026-07-28")
        assert "KO_US_EQ" in run2._moo_fired
        assert "AAPL_US_EQ" not in run2._moo_fired

    def test_accumulates_across_several_restarts(self, state_dir):
        for sym in ("KO_US_EQ", "AAPL_US_EQ", "MSFT_US_EQ"):
            s = _strategy()
            s.on_session_start("2026-07-28")
            s._persist_moo_fired(sym)
        final = _strategy()
        final.on_session_start("2026-07-28")
        assert final._moo_fired == {"KO_US_EQ", "AAPL_US_EQ", "MSFT_US_EQ"}


class TestFailsOpenNotClosed:
    def test_unreadable_lock_does_not_freeze_the_strategy(self, state_dir, monkeypatch):
        """A corrupt lock file must let trading CONTINUE. A duplicate order is
        recoverable; a strategy that silently stops trading is not."""
        s = _strategy()
        s.on_session_start("2026-07-28")
        s._moo_lock_path("2026-07-28").write_text("{ not json")
        s2 = _strategy()
        s2.on_session_start("2026-07-28")
        assert s2._moo_fired == set()          # proceeds unlocked, does not raise

    def test_unwritable_state_dir_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("TRADEPRO_STATE_DIR", "/proc/nonexistent-cannot-create")
        s = _strategy()
        s.on_session_start("2026-07-28")       # must not raise
        s._persist_moo_fired("KO_US_EQ")       # must not raise


class TestOnDisk:
    def test_lock_is_per_strategy(self, state_dir):
        a = IchimokuEquityStrategy(strategy_id="ichimoku_equity")
        b = IchimokuEquityStrategy(strategy_id="ichimoku_equity_ibkr")
        a.on_session_start("2026-07-28"); a._persist_moo_fired("KO_US_EQ")
        b.on_session_start("2026-07-28")
        assert "KO_US_EQ" not in b._moo_fired, "sleeves must not share a lock"

    def test_file_contents_are_inspectable(self, state_dir):
        s = _strategy()
        s.on_session_start("2026-07-28")
        s._persist_moo_fired("KO_US_EQ")
        data = json.loads(s._moo_lock_path("2026-07-28").read_text())
        assert data == ["KO_US_EQ"]
