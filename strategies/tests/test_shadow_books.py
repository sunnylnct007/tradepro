"""SHADOW_BOOKS_V1 — the replay and the verdicts, on synthetic bars.

What these pin: fills at the NEXT open on both legs; an un-exited trade is
OPEN and out of the record; a corrupt bar drops the trade and NAMES it; the
pre-registered marks (too few / PASS / FAIL / between) read as written; the
module can never place an order.
"""
from __future__ import annotations

import ast
import pathlib

from tradepro_strategies import shadow_books as B

SRC = pathlib.Path(B.__file__)


class Rule:
    """A toy rule: enter when the close is 10 below the previous; exit on the
    first close at or above fill, or after 3 bars."""
    MIN_BARS = 2
    MAX_HOLD = 3

    # Assigned, not `def entry_signal`: test_one_rule_one_object scans for that
    # token and this toy is a test fixture, not a rule.
    entry_signal = staticmethod(lambda c, i: i >= 1 and c[i] <= c[i - 1] - 10)

    @staticmethod
    def exit_decision(c, i, *, fill_price, bars_held):
        if c[i] >= fill_price:
            return True, "target"
        if bars_held >= 3:
            return True, "timeout"
        return False, None


def _series(closes, opens=None):
    dates = [f"2026-10-{d:02d}" for d in range(1, len(closes) + 1)]
    return dates, (opens or list(closes)), list(closes)


def test_fills_at_the_next_open_on_both_legs():
    # signal on bar 1 (100 -> 90); buy at open[2]=91; the fill bar's own close (89) is checked
    # and does not exit; bar 3 closes 95 >= 91 -> sell at open[4]=96
    d, o, c = _series([100, 90, 89, 95, 97, 97], opens=[100, 90, 91, 93, 96, 97])
    closed, open_, dropped = B.replay(Rule, "X", d, o, c, "2026-10-01", {})
    assert dropped == [] and open_ == []
    t, = closed
    assert (t["entry_date"], t["entry"], t["exit_date"], t["exit"], t["exit_why"]) == ("2026-10-03", 91, "2026-10-05", 96, "target")
    assert abs(t["pct"] - ((96 / 91 - 1) - B.COST) * 100) < 1e-3


def test_an_unexited_trade_is_open_and_out_of_the_record():
    d, o, c = _series([100, 90, 84, 83], opens=[100, 90, 86, 84])
    closed, open_, dropped = B.replay(Rule, "X", d, o, c, "2026-10-01", {})
    assert closed == [] and len(open_) == 1
    assert open_[0]["entry"] == 86 and open_[0]["mark"] == 83 and open_[0]["exit_signalled"] is None


def test_an_exit_on_the_last_close_is_still_open_it_sells_at_an_open_not_yet_seen():
    d, o, c = _series([100, 90, 85, 95], opens=[100, 90, 87, 95])
    closed, open_, _ = B.replay(Rule, "X", d, o, c, "2026-10-01", {})
    assert closed == [] and open_[0]["exit_signalled"] == "target"


def test_a_corrupt_bar_drops_the_trade_and_names_it():
    d, o, c = _series([100, 90, 91, 10, 92, 92], opens=[100, 90, 93, 10, 92, 92])
    closed, open_, dropped = B.replay(Rule, "X", d, o, c, "2026-10-01", {})
    assert closed == [] and open_ == []
    assert dropped and "X 2026-10-04" in dropped[0]


def test_a_corrupt_exit_open_drops_the_trade_too():
    # fill 91 on bar 2; its close 92 >= 91 exits; the sell open on bar 3 is 10 — fiction, dropped and named
    d, o, c = _series([100, 90, 92, 92, 92, 92], opens=[100, 90, 91, 10, 92, 92])
    closed, open_, dropped = B.replay(Rule, "X", d, o, c, "2026-10-01", {})
    assert closed == [] and dropped and "exit open 10" in dropped[0]


def test_signals_before_the_record_start_are_ignored():
    d, o, c = _series([100, 90, 92, 95, 97, 97])
    closed, open_, _ = B.replay(Rule, "X", d, o, c, "2026-10-03", {})
    assert closed == [] and open_ == []


def test_verdicts_read_as_pre_registered():
    spec = {"min_n": 50, "pass_mean": 0.49, "pass_win": 60.0}
    assert B.verdict_daily({"trades": 48, "avg_pct": 5.0, "win_pct": 90}, spec) == "too few (48 of 50)"
    assert B.verdict_daily({"trades": 50, "avg_pct": -0.1, "win_pct": 70}, spec) == "FAIL"
    assert B.verdict_daily({"trades": 50, "avg_pct": 0.6, "win_pct": 65}, spec) == "PASS"
    assert B.verdict_daily({"trades": 50, "avg_pct": 0.6, "win_pct": 55}, spec).startswith("below the mark")
    assert B.verdict_daily({"trades": 50, "avg_pct": 0.3, "win_pct": 70}, spec).startswith("below the mark")
    ctl = {"since_start_pct": 5.0, "worst_dd_pct": -10.0}
    assert B.verdict_rotation({"decisions": 5, "since_start_pct": 9, "worst_dd_pct": -1}, ctl).startswith("too few")
    assert B.verdict_rotation({"decisions": 6, "since_start_pct": 3.0, "worst_dd_pct": -6.0}, ctl) == "PASS"
    assert B.verdict_rotation({"decisions": 6, "since_start_pct": 0.0, "worst_dd_pct": -12.0}, ctl) == "FAIL"
    assert B.verdict_rotation({"decisions": 6, "since_start_pct": 0.0, "worst_dd_pct": -6.0}, ctl).startswith("below the mark")
    assert B.verdict_intraday({"trades": 10, "avg_pct": 3}) == "too few (10 of 50)"
    assert B.verdict_intraday({"trades": 50, "avg_pct": 0.2}) == "PASS"


def test_no_verdict_until_the_first_entries_could_have_timed_out():
    spec = {"rule": Rule, "min_n": 1, "pass_mean": 0.1, "pass_win": None}   # Rule.MAX_HOLD == 3
    assert B.verdict_daily({"trades": 9, "avg_pct": -5.0, "win_pct": 10}, spec, 2).startswith("deferred")
    assert B.verdict_daily({"trades": 9, "avg_pct": -5.0, "win_pct": 10}, spec, 3) == "FAIL"


def test_the_closed_record_warns_while_it_is_censored():
    assert B._censoring_caveat([{"pct": 1}] * 9, [{"entry_date": "2026-10-01"}], {"rule": Rule}, 2) is not None
    spec = {"rule": Rule}
    assert B._censoring_caveat([{"pct": 1}], [{"entry_date": "2026-10-01"}] * 2, spec).startswith("2 trades are still OPEN")
    assert B._censoring_caveat([{"pct": 1}] * 5, [{"entry_date": "2026-10-01"}], spec) is None
    assert B._censoring_caveat([{"pct": 1}], [], spec) is None


def test_the_module_has_no_broker_import_and_a_scoreboard_names_a_failed_book():
    tree = ast.parse(SRC.read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
            {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(("paper" in m or "broker" in m or "oms" in m or "requests" in m) for m in names), names
    rows = B.scoreboard({"errors": {"daily": "RuntimeError: SPY has no daily bars"}})
    assert rows == [{"book": "daily", "name": "daily", "n": None, "verdict": "NOT COMPUTED — RuntimeError: SPY has no daily bars"}]
