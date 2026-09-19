"""The sentry must catch the 17-18 Sep signature — and stay quiet on holidays.

The incident: placement dead code for two sessions, every surface green,
`placed` NULL instead of False the only trace. These tests replay that exact
shape, the 16 Sep contrast day (all refused, all RECORDED — which must PASS),
and the windows where a check is simply not assessable yet.
"""
import datetime as dt

from tradepro_strategies.cli import lane_sentry as LS

US = {"SPX", "XSP", "SPY", "NDX", "QQQ", "GOLD"}
S = dt.date(2026, 9, 17)


def _row(market="SPX", placed=None, state="open", date="2026-09-17"):
    return {"market": market, "exchange_date": f"{date}T00:00:00",
            "session_state": state, "placed": placed}


def test_the_dead_code_signature_fails_loud():
    """17-18 Sep exactly: rows present, session open, placed NULL on all."""
    rows = [_row(m, placed=None) for m in ("SPX", "XSP", "QQQ")] * 2
    st, detail = LS.check_strangle_placement_attempted(rows, S, US)
    assert st == "fail"
    assert "6 of 6" in detail and "NULL" in detail, detail


def test_a_day_of_recorded_refusals_passes():
    """16 Sep exactly: every placement FAILED but every failure was RECORDED
    (placed=False). 'We tried and could not' is the lane working."""
    rows = [_row(m, placed=False) for m in ("SPX", "XSP", "QQQ", "SPY")] * 4
    st, detail = LS.check_strangle_placement_attempted(rows, S, US)
    assert st == "ok"
    assert "16" in detail and "refused-with-reason" in detail, detail


def test_one_null_among_verdicts_still_fails():
    """Partial death is death: NULL rows are named, with the count."""
    rows = [_row("SPX", placed=True), _row("XSP", placed=None)]
    st, detail = LS.check_strangle_placement_attempted(rows, S, US)
    assert st == "fail" and "1 of 2" in detail and "XSP" in detail


def test_holiday_skips_instead_of_failing():
    rows = [_row("SPX", placed=None, state="closed")]
    st, detail = LS.check_strangle_placement_attempted(rows, S, US)
    assert st == "skip" and "holiday" in detail


def test_india_rows_never_gate_the_us_check():
    """BANKNIFTY has no placement path; its NULL must not fail the sentry."""
    rows = [_row("BANKNIFTY", placed=None), _row("SPX", placed=True)]
    st, _ = LS.check_strangle_placement_attempted(rows, S, US)
    assert st == "ok"


def test_missing_decisions_fail_with_the_count():
    st, detail = LS.check_strangle_decisions([_row(date="2026-09-16")], S)
    assert st == "fail" and detail.startswith("0 decision rows")


def test_capture_needs_both_expiries():
    legs_one = [{"capture_date": "2026-09-17", "expiry": "2026-09-24"}] * 50
    st, detail = LS.check_chain_capture(legs_one, S, "^XSP")
    assert st == "fail" and "only 1 expiry" in detail
    legs_two = legs_one + [{"capture_date": "2026-09-17", "expiry": "2026-10-08"}] * 40
    st, detail = LS.check_chain_capture(legs_two, S, "^XSP")
    assert st == "ok" and "90 legs across 2 expiries" in detail


def test_expected_session_rolls_back_correctly():
    # Saturday 19 Sep 13:00Z -> Friday 18th
    assert LS.expected_session(dt.datetime(2026, 9, 19, 13, tzinfo=dt.timezone.utc)) == dt.date(2026, 9, 18)
    # Monday 08:00Z (before the lanes fire) -> Friday
    assert LS.expected_session(dt.datetime(2026, 9, 21, 8, tzinfo=dt.timezone.utc)) == dt.date(2026, 9, 18)
    # Monday 16:00Z -> Monday itself
    assert LS.expected_session(dt.datetime(2026, 9, 21, 16, tzinfo=dt.timezone.utc)) == dt.date(2026, 9, 21)
