"""Steps for signal_ledger.feature.

Uses a temp-file ledger (tmp_path via tempfile) so nothing touches
the real ~/.tradepro/signal_ledger.jsonl. All scenarios run without
any network or scheduler dependency.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from behave import given, then, when

from tradepro_strategies.signal_ledger import SignalLedger, SignalRecord


# ── helpers ────────────────────────────────────────────────────────

def _make_ledger(context) -> SignalLedger:
    if not hasattr(context, "_tmp_dir"):
        context._tmp_dir = tempfile.mkdtemp()
    path = Path(context._tmp_dir) / "test_ledger.jsonl"
    # Always start each scenario with a clean file — Behave's before_scenario
    # hook does not clear _tmp_dir, so the file can accumulate records from
    # previous scenarios in the same feature run.
    if path.exists():
        path.unlink()
    context._ledger_path = path
    ledger = SignalLedger(path=path)
    context.ledger = ledger
    return ledger


def _open_signal(symbol: str, entry_price: float = 100.0,
                 expires_days: int | None = 5) -> SignalRecord:
    return SignalRecord.new(
        source="COMPASS",
        symbol=symbol,
        universe="test",
        score=75.0,
        conviction="MEDIUM",
        entry_price=entry_price,
        stop_price=entry_price * 0.95,
        target_price=entry_price * 1.10,
        expires_days=expires_days,
    )


# ── Given ─────────────────────────────────────────────────────────

@given("a fresh temp-file ledger")
def step_fresh_ledger(context):
    _make_ledger(context)


@given("a fresh temp-file ledger with one OPEN signal for \"{symbol}\" entry_price={entry:f}")
def step_fresh_ledger_one_signal(context, symbol, entry):
    ledger = _make_ledger(context)
    sig = _open_signal(symbol, entry_price=entry)
    ledger.append(sig)
    context.last_signal_id = sig.signal_id


@given("a fresh temp-file ledger with one OPEN signal with expires_days={days:d}")
def step_fresh_ledger_with_expiry(context, days):
    ledger = _make_ledger(context)
    sig = _open_signal("TESTSYM", expires_days=days)
    ledger.append(sig)
    context.last_signal_id = sig.signal_id


@given("a signal with expires_days=-1 (already expired)")
def step_expired_signal(context):
    if not hasattr(context, "ledger"):
        _make_ledger(context)
    sig = _open_signal("EXPIRESYM", expires_days=-1)
    context.ledger.append(sig)
    context.last_signal_id = sig.signal_id


@given("a fresh temp-file ledger with closed signals")
def step_ledger_with_closed_signals(context):
    ledger = _make_ledger(context)
    context._closed_count = 0
    for row in context.table:
        source  = row["source"]
        outcome = row["outcome"]
        ret_pct = float(row["return_pct"])
        sig = SignalRecord.new(
            source=source, symbol="MULTI", universe="test",
            entry_price=100.0, stop_price=95.0, target_price=110.0,
            expires_days=None,
        )
        sig.status = "CLOSED"
        sig.outcome = outcome
        sig.closed_at = datetime.now(timezone.utc).isoformat()
        sig.exit_price = 100.0 + ret_pct  # synthetic
        sig.return_pct = ret_pct
        sig.holding_days = 3
        ledger.append(sig)
        context._closed_count += 1


@given("a fresh temp-file ledger with signals from both COMPASS and CATALYST")
def step_ledger_mixed_sources(context):
    ledger = _make_ledger(context)
    for source in ("COMPASS", "COMPASS", "CATALYST"):
        sig = SignalRecord.new(
            source=source, symbol="SYM", universe="test",
            entry_price=100.0, stop_price=95.0, target_price=110.0, expires_days=None,
        )
        sig.status = "CLOSED"
        sig.outcome = "HIT_TARGET"
        sig.closed_at = datetime.now(timezone.utc).isoformat()
        sig.return_pct = 2.0
        sig.holding_days = 2
        ledger.append(sig)
    context._compass_count = 2
    context._catalyst_count = 1


@given("a fresh temp-file ledger with signals for AAPL and MSFT")
def step_ledger_two_symbols(context):
    ledger = _make_ledger(context)
    for symbol in ("AAPL", "AAPL", "MSFT"):
        sig = SignalRecord.new(
            source="COMPASS", symbol=symbol, universe="test",
            entry_price=100.0, stop_price=95.0, target_price=110.0, expires_days=None,
        )
        sig.status = "CLOSED"
        sig.outcome = "HIT_TARGET"
        sig.closed_at = datetime.now(timezone.utc).isoformat()
        sig.return_pct = 3.0
        sig.holding_days = 2
        ledger.append(sig)
    context._aapl_count = 2


@given("a fresh temp-file ledger with signals closed 10 days ago and 60 days ago")
def step_ledger_old_new(context):
    ledger = _make_ledger(context)
    now = datetime.now(timezone.utc)
    for days_back in (10, 60):
        sig = SignalRecord.new(
            source="COMPASS", symbol="SYM", universe="test",
            entry_price=100.0, stop_price=95.0, target_price=110.0, expires_days=None,
        )
        sig.status = "CLOSED"
        sig.outcome = "HIT_TARGET"
        closed = (now - timedelta(days=days_back)).isoformat()
        sig.closed_at = closed
        sig.return_pct = 2.0
        sig.holding_days = days_back
        ledger.append(sig)


@given("a fresh temp-file ledger with one OPEN and one CLOSED signal")
def step_ledger_open_and_closed(context):
    ledger = _make_ledger(context)
    open_sig = _open_signal("OPEN", expires_days=5)
    ledger.append(open_sig)
    closed_sig = SignalRecord.new(
        source="COMPASS", symbol="CLOSED", universe="test",
        entry_price=100.0, stop_price=95.0, target_price=110.0, expires_days=None,
    )
    closed_sig.status = "CLOSED"
    closed_sig.outcome = "HIT_TARGET"
    closed_sig.closed_at = datetime.now(timezone.utc).isoformat()
    closed_sig.return_pct = 5.0
    closed_sig.holding_days = 3
    ledger.append(closed_sig)
    context._open_symbol = "OPEN"
    context._closed_symbol = "CLOSED"


@given("a SignalRecord with entry_price={ep:f} stop_price={sp:f} target_price={tp:f}")
def step_signal_for_rr(context, ep, sp, tp):
    context.rr_signal = SignalRecord.new(
        source="COMPASS", symbol="RR", universe="test",
        entry_price=ep, stop_price=sp, target_price=tp, expires_days=None,
    )


# ── When ──────────────────────────────────────────────────────────

@when("a COMPASS signal is appended for \"{symbol}\" with score {score:f} and entry_price {ep:f}")
def step_append_signal(context, symbol, score, ep):
    sig = SignalRecord.new(
        source="COMPASS", symbol=symbol, universe="test",
        score=score, conviction="MEDIUM",
        entry_price=ep, stop_price=ep * 0.95, target_price=ep * 1.10,
        expires_days=5,
    )
    context.ledger.append(sig)
    context.last_signal_id = sig.signal_id


@when("two COMPASS signals are appended for different symbols")
def step_append_two(context):
    context._ids = []
    for sym in ("AAPL", "MSFT"):
        sig = _open_signal(sym)
        context.ledger.append(sig)
        context._ids.append(sig.signal_id)


@when("SignalRecord.new is called with source \"{source}\"")
def step_bad_source(context, source):
    context._raised = None
    try:
        SignalRecord.new(source=source, symbol="X", universe="test")
    except ValueError as exc:
        context._raised = exc


@when("close_signal is called with outcome \"{outcome}\" exit_price={price:f}")
def step_close_signal(context, outcome, price):
    context._close_result = context.ledger.close_signal(
        context.last_signal_id, outcome=outcome, exit_price=price
    )
    context._closed_outcome = outcome
    context._exit_price = price


@when("close_signal is called for signal_id \"{sid}\" with outcome \"{outcome}\"")
def step_close_unknown(context, sid, outcome):
    context._close_result = context.ledger.close_signal(sid, outcome=outcome)


@when("close_signal is called with outcome \"{outcome}\"")
def step_close_bad_outcome(context, outcome):
    context._raised = None
    try:
        context.ledger.close_signal(context.last_signal_id, outcome=outcome)
    except ValueError as exc:
        context._raised = exc


@when("expire_stale is called")
def step_expire_stale(context):
    context._expire_count = context.ledger.expire_stale()


@when("compute_stats is called")
def step_compute_stats_no_filter(context):
    context._stats = context.ledger.compute_stats()


@when("compute_stats is called with source \"{source}\"")
def step_compute_stats_by_source(context, source):
    context._stats = context.ledger.compute_stats(source=source)


@when("compute_stats is called with symbol \"{symbol}\"")
def step_compute_stats_by_symbol(context, symbol):
    context._stats = context.ledger.compute_stats(symbol=symbol)


@when("compute_stats is called with lookback_days={days:d}")
def step_compute_stats_lookback(context, days):
    context._stats = context.ledger.compute_stats(lookback_days=days)


@when("load_open is called")
def step_load_open(context):
    context._open_records = context.ledger.load_open()


@when("load_closed is called")
def step_load_closed(context):
    context._closed_records = context.ledger.load_closed()


@when("implied_rr is accessed")
def step_implied_rr(context):
    context._rr = context.rr_signal.implied_rr


# ── Then ──────────────────────────────────────────────────────────

@then("load_all returns {n:d} record")
def step_load_all_count(context, n):
    records = context.ledger.load_all()
    assert len(records) == n, f"expected {n} record(s), got {len(records)}"


@then("the record has status \"{expected}\"")
def step_record_status(context, expected):
    records = context.ledger.load_all()
    assert len(records) >= 1
    match = next((r for r in records if r.signal_id == context.last_signal_id), None)
    if match is None:
        # After close, the status may have changed; reload
        all_records = context.ledger.load_all()
        match = next((r for r in all_records if r.signal_id == context.last_signal_id), all_records[0])
    assert match.status == expected, f"expected status={expected!r}, got {match.status!r}"


@then("the signal has status \"{expected}\"")
def step_signal_status(context, expected):
    records = context.ledger.load_all()
    match = next((r for r in records if r.signal_id == context.last_signal_id), None)
    assert match is not None, f"signal_id {context.last_signal_id} not found"
    assert match.status == expected, f"expected {expected!r}, got {match.status!r}"


@then("the record has source \"{expected}\"")
def step_record_source(context, expected):
    records = context.ledger.load_all()
    assert any(r.source == expected for r in records), (
        f"no record with source={expected!r}"
    )


@then("the record has symbol \"{expected}\"")
def step_record_symbol(context, expected):
    records = context.ledger.load_all()
    assert any(r.symbol == expected for r in records), (
        f"no record with symbol={expected!r}"
    )


@then("each record has a distinct signal_id")
def step_distinct_ids(context):
    assert len(context._ids) == len(set(context._ids)), "signal IDs are not unique"


@then("the record has a non-empty fired_at timestamp")
def step_fired_at(context):
    records = context.ledger.load_all()
    assert len(records) >= 1
    assert records[0].fired_at, "fired_at is empty"


@then("outcome is \"{expected}\"")
def step_outcome(context, expected):
    records = context.ledger.load_all()
    match = next((r for r in records if r.signal_id == context.last_signal_id), None)
    if match is None:
        all_records = context.ledger.load_all()
        match = all_records[0]
    assert match.outcome == expected, f"expected outcome={expected!r}, got {match.outcome!r}"


@then("return_pct is approximately {expected:f}")
def step_return_pct(context, expected):
    records = context.ledger.load_all()
    match = next((r for r in records if r.signal_id == context.last_signal_id), None)
    assert match is not None
    assert match.return_pct is not None
    assert abs(match.return_pct - expected) < 0.2, (
        f"expected return_pct ≈ {expected}, got {match.return_pct}"
    )


@then("holding_days is at least {n:d}")
def step_holding_days(context, n):
    records = context.ledger.load_all()
    match = next((r for r in records if r.signal_id == context.last_signal_id), None)
    assert match is not None
    assert match.holding_days >= n


@then("the return value is False")
def step_false_return(context):
    assert context._close_result is False


@then("a ValueError is raised")
def step_value_error(context):
    assert isinstance(context._raised, ValueError), (
        f"expected ValueError, got {context._raised!r}"
    )


@then("expire_stale returns {n:d}")
def step_expire_count(context, n):
    assert context._expire_count == n, (
        f"expected expire_stale()={n}, got {context._expire_count}"
    )


@then("the signal still has status \"{expected}\"")
def step_still_open(context, expected):
    records = context.ledger.load_all()
    assert len(records) == 1
    assert records[0].status == expected


@then("total_closed is {n:d}")
def step_total_closed(context, n):
    assert context._stats["total_closed"] == n, (
        f"expected total_closed={n}, got {context._stats['total_closed']}"
    )


@then("hit_rate_pct is None")
def step_hit_rate_none(context):
    assert context._stats["hit_rate_pct"] is None


@then("expectancy_pct is None")
def step_expectancy_none(context):
    assert context._stats["expectancy_pct"] is None


@then("hit_rate_pct is {expected:f}")
def step_hit_rate(context, expected):
    assert context._stats["hit_rate_pct"] == expected, (
        f"expected hit_rate_pct={expected}, got {context._stats['hit_rate_pct']}"
    )


@then("expectancy_pct is positive")
def step_expectancy_positive(context):
    exp = context._stats["expectancy_pct"]
    assert exp is not None and exp > 0, (
        f"expected positive expectancy, got {exp}"
    )


@then("only COMPASS signals appear in total_closed")
def step_only_compass(context):
    assert context._stats["total_closed"] == context._compass_count


@then("only AAPL signals appear in total_closed")
def step_only_aapl(context):
    assert context._stats["total_closed"] == context._aapl_count


@then("only the signal closed 10 days ago is counted")
def step_lookback_count(context):
    assert context._stats["total_closed"] == 1


@then("the result contains only the OPEN signal")
def step_only_open(context):
    assert len(context._open_records) == 1
    assert context._open_records[0].symbol == context._open_symbol


@then("the result contains only the CLOSED signal")
def step_only_closed(context):
    assert len(context._closed_records) == 1
    assert context._closed_records[0].symbol == context._closed_symbol


@then("the value is {expected:f}")
def step_rr_value(context, expected):
    assert context._rr == expected, f"expected RR={expected}, got {context._rr}"
