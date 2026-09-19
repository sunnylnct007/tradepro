"""The desk check may only say OK on positive evidence.

Owner, 19 Sep 2026: *"how come we are having continuus issues"*. The answer
was that he was the monitoring — every defect that fortnight surfaced because
he asked, not because anything reported it. This file is the detector that
should have existed, and these tests exist because the detector is worthless
if it can itself print a green tick over a failure.

That is not hypothetical: the FIRST draft of desk_check printed

    [ok  ] Execution · ichimoku_equity_ibkr: 48 orders · 9 reached the broker
           · 6 filled · exits 3/27

— OK beside 24 exits that never left the OMS. The rule "not zero" is not the
same as "healthy", and the whole system had already been failing that way for
weeks.
"""
import pytest

from tradepro_strategies.cli import desk_check as dc
from tradepro_strategies.cli.desk_check import (
    BROKEN, OK, UNKNOWN, WARN, Check, verdict_of,
)


def _order(sid, side, state, broker_id):
    return {"strategyId": sid, "side": side, "state": state,
            "brokerOrderId": broker_id}


def _exec_checks(monkeypatch, orders):
    monkeypatch.setattr(dc, "_get", lambda *a, **k: orders)
    monkeypatch.setattr(dc, "LIVE_STRATEGIES", ("s1",))
    return dc.check_execution("http://x", None)


# ── the false-OK this file exists to prevent ──────────────────────────────
def test_a_lane_losing_most_of_its_orders_is_not_OK(monkeypatch):
    """The real ichimoku_equity_ibkr shape on 19 Sep: 48 orders, 9 reached
    the broker, 6 filled, exits 3/27. Some exits DO fill, so the BROKEN rule
    does not catch it — and the first draft printed OK over 24 lost exits."""
    orders = ([_order("s1", "SELL", "FILLED", "b")] * 3
              + [_order("s1", "SELL", "CANCELLED", None)] * 24
              + [_order("s1", "BUY", "FILLED", "b")] * 6
              + [_order("s1", "BUY", "CANCELLED", None)] * 15)
    (c,) = _exec_checks(monkeypatch, orders)
    assert c.status == WARN, f"expected WARN, got {c.status}: {c.detail}"
    assert "48 orders" in c.detail and "9 reached the broker" in c.detail
    assert "exits 3/27" in c.detail
    assert "19% of orders reached the broker" in c.fix


def test_nothing_reaching_the_broker_is_BROKEN(monkeypatch):
    orders = [_order("s1", "BUY", "CANCELLED", None)] * 20
    (c,) = _exec_checks(monkeypatch, orders)
    assert c.status == BROKEN
    assert "NOTHING has ever reached the broker" in c.detail


def test_entries_filling_while_exits_never_do_is_BROKEN(monkeypatch):
    """The swing shape: it can OPEN a position and never CLOSE one."""
    orders = ([_order("s1", "BUY", "FILLED", "b1")] * 12
              + [_order("s1", "SELL", "CANCELLED", None)] * 47)
    (c,) = _exec_checks(monkeypatch, orders)
    assert c.status == BROKEN
    assert "never CLOSED one" in c.fix


def test_a_healthy_lane_is_OK_and_states_its_numbers(monkeypatch):
    orders = ([_order("s1", "BUY", "FILLED", "b1")] * 41
              + [_order("s1", "SELL", "FILLED", "b2")] * 30)
    (c,) = _exec_checks(monkeypatch, orders)
    assert c.status == OK
    assert "71 orders" in c.detail and "exits 30/30" in c.detail


def test_no_orders_at_all_is_UNKNOWN_not_OK(monkeypatch):
    """An absence is not evidence of health."""
    (c,) = _exec_checks(monkeypatch, [])
    assert c.status == UNKNOWN


def test_an_unreadable_oms_is_UNKNOWN_not_OK(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("oms down")
    monkeypatch.setattr(dc, "_get", _boom)
    (c,) = dc.check_execution("http://x", None)
    assert c.status == UNKNOWN


# ── boards ────────────────────────────────────────────────────────────────
def test_a_stale_board_is_BROKEN_and_says_do_not_trade(monkeypatch):
    monkeypatch.setattr(dc, "BOARDS", {"wheel": ("Wheel", 30)})
    monkeypatch.setattr(dc, "_get", lambda *a, **k: {
        "asOfUtc": "2026-09-17T16:00:00Z",
        "artifact": {"candidates_v2": [{"eligible": True}] * 13}})
    monkeypatch.setattr(dc, "_hours_since", lambda _: 54.0)
    (c,) = dc.check_boards("http://x", None)
    assert c.status == BROKEN
    assert "54h old" in c.detail and "13 marked eligible" in c.detail
    assert "DO NOT TRADE" in c.fix


def test_a_board_with_no_timestamp_is_UNKNOWN(monkeypatch):
    """Unknowable age must never render as fresh."""
    monkeypatch.setattr(dc, "BOARDS", {"wheel": ("Wheel", 30)})
    monkeypatch.setattr(dc, "_get", lambda *a, **k: {"artifact": {}})
    (c,) = dc.check_boards("http://x", None)
    assert c.status == UNKNOWN


# ── jobs ──────────────────────────────────────────────────────────────────
def test_a_job_exiting_nonzero_is_BROKEN(monkeypatch):
    """The --strangle-dte breakage: exit 2, every run, for two days."""
    monkeypatch.setattr(dc, "SCHEDULED_JOBS", ("com.tradepro.option-chain-capture",))
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "-\t2\tcom.tradepro.option-chain-capture\n"})())
    (c,) = dc.check_jobs()
    assert c.status == BROKEN
    assert "exited 2" in c.detail


def test_a_job_that_is_not_scheduled_at_all_is_UNKNOWN(monkeypatch):
    monkeypatch.setattr(dc, "SCHEDULED_JOBS", ("com.tradepro.missing",))
    monkeypatch.setattr(dc.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": ""})())
    (c,) = dc.check_jobs()
    assert c.status == UNKNOWN
    assert "not scheduled at all" in c.detail


def test_a_job_exiting_zero_is_OK(monkeypatch):
    monkeypatch.setattr(dc, "SCHEDULED_JOBS", ("com.tradepro.trade-alerts",))
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "123\t0\tcom.tradepro.trade-alerts\n"})())
    (c,) = dc.check_jobs()
    assert c.status == OK


# ── the harness itself ────────────────────────────────────────────────────
def test_a_check_that_crashes_becomes_UNKNOWN_rather_than_vanishing(monkeypatch):
    """A lane that disappears from the report looks fine by absence."""
    def _boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(dc, "check_data", _boom)
    monkeypatch.setattr(dc, "check_boards", lambda *a, **k: [])
    monkeypatch.setattr(dc, "check_execution", lambda *a, **k: [])
    monkeypatch.setattr(dc, "check_jobs", lambda: [])
    monkeypatch.setattr(dc, "check_round_trips", lambda *a, **k: [])
    checks = dc.run_checks("http://x", None)
    assert [c.status for c in checks] == [UNKNOWN]
    assert "kaboom" in checks[0].detail


@pytest.mark.parametrize("statuses,expected_prefix", [
    ([OK, OK], "USABLE —"),
    ([OK, WARN], "USABLE WITH CAVEATS"),
    ([OK, UNKNOWN], "UNVERIFIED"),
    ([OK, WARN, UNKNOWN, BROKEN], "BROKEN"),
    ([UNKNOWN, BROKEN], "BROKEN"),
])
def test_the_worst_status_decides_the_verdict(statuses, expected_prefix):
    checks = [Check("l", s, "d") for s in statuses]
    assert verdict_of(checks).startswith(expected_prefix)


def test_unknown_counts_as_bad_so_it_cannot_be_ignored():
    assert Check("l", UNKNOWN, "d").bad is True
    assert Check("l", BROKEN, "d").bad is True
    assert Check("l", OK, "d").bad is False


def test_every_failing_line_carries_a_number():
    """A warning without its number is a mood, not a warning."""
    import re
    checks = [Check("Board", BROKEN, "54h old (limit 30h) — 82 rows"),
              Check("Job", BROKEN, "last run exited 2")]
    for c in checks:
        assert re.search(r"\d", c.detail), f"no number in: {c.detail}"
