"""Steps for eps_tracker.feature.

Uses a temporary directory via context._snapshot_dir so all file I/O
is isolated — no writes to ~/.tradepro/eps_snapshots/. The ticker_factory
injection seam avoids any yfinance network calls.
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from behave import given, then, when


# ── helpers ────────────────────────────────────────────────────────

def _snapshots_dir(context) -> Path:
    if not hasattr(context, "_snapshot_dir"):
        context._snapshot_dir = tempfile.mkdtemp()
    return Path(context._snapshot_dir)


def _snapshot_path(context, symbol: str) -> Path:
    return _snapshots_dir(context) / f"{symbol.upper()}.json"


def _write_snapshots(context, symbol: str, entries: list[dict]) -> None:
    _snapshot_path(context, symbol).write_text(json.dumps(entries, indent=2))


def _ticker_factory(eps: float | None):
    """Return a factory callable that produces a mock Ticker with given forwardEps."""
    def factory(sym):
        mock = MagicMock()
        mock.info = {"forwardEps": eps}
        return mock
    return factory


def _call_record_snapshot(context, symbol: str, ticker_factory=None):
    """Call record_snapshot with the test snapshot dir patched in."""
    from tradepro_strategies import eps_tracker as mod
    with patch.object(mod, "_SNAPSHOT_DIR", _snapshots_dir(context)):
        return mod.record_snapshot(symbol, ticker_factory=ticker_factory)


def _call_get_eps_revision(context, symbol: str):
    from tradepro_strategies import eps_tracker as mod
    with patch.object(mod, "_SNAPSHOT_DIR", _snapshots_dir(context)):
        return mod.get_eps_revision(symbol)


# ── Given ─────────────────────────────────────────────────────────

@given("a temporary snapshot directory")
def step_tmp_dir(context):
    _snapshots_dir(context)  # ensure created


@given("a ticker factory returning forwardEps={eps:g} for \"{symbol}\"")
def step_ticker_factory(context, eps, symbol):
    context._ticker_factory = _ticker_factory(float(eps))
    context._symbol = symbol


@given("a ticker factory returning forwardEps=None for \"{symbol}\"")
def step_ticker_factory_none(context, symbol):
    context._ticker_factory = _ticker_factory(None)
    context._symbol = symbol


@given("a temporary snapshot directory with one snapshot for \"{symbol}\" dated \"{dated}\" eps={eps:g}")
def step_one_snapshot(context, symbol, dated, eps):
    _snapshots_dir(context)
    _write_snapshots(context, symbol, [{"symbol": symbol.upper(), "date": dated, "forward_eps": eps}])
    context._symbol = symbol


@given("a temporary snapshot directory with {n:d} snapshots for \"{symbol}\"")
def step_n_snapshots(context, n, symbol):
    entries = [
        {"symbol": symbol.upper(), "date": (date(2024, 1, 1) + timedelta(weeks=i)).isoformat(), "forward_eps": 5.0 + i * 0.01}
        for i in range(n)
    ]
    _write_snapshots(context, symbol, entries)
    context._symbol = symbol


@given("a temporary snapshot directory with snapshots for \"{symbol}\"")
def step_snapshots_table(context, symbol):
    _snapshots_dir(context)
    entries = []
    for row in context.table:
        entries.append({"symbol": symbol.upper(), "date": row["date"], "forward_eps": float(row["forward_eps"])})
    _write_snapshots(context, symbol, entries)
    context._symbol = symbol


@given("a temporary snapshot directory with 1 snapshot for \"{symbol}\" dated today")
def step_one_snapshot_today(context, symbol):
    _snapshots_dir(context)
    _write_snapshots(context, symbol, [
        {"symbol": symbol.upper(), "date": date.today().isoformat(), "forward_eps": 5.0}
    ])
    context._symbol = symbol


# ── When ──────────────────────────────────────────────────────────

@when("I call record_snapshot for \"{symbol}\"")
def step_call_record_snapshot(context, symbol):
    factory = getattr(context, "_ticker_factory", None)
    context._record_result = _call_record_snapshot(context, symbol, ticker_factory=factory)


@when("I call record_snapshot for \"{symbol}\" twice on the same day")
def step_call_record_twice(context, symbol):
    factory = getattr(context, "_ticker_factory", None)
    _call_record_snapshot(context, symbol, ticker_factory=factory)
    _call_record_snapshot(context, symbol, ticker_factory=factory)


@when("I call get_eps_revision for \"{symbol}\"")
def step_call_get_revision(context, symbol):
    context._revision = _call_get_eps_revision(context, symbol)


# ── Then ──────────────────────────────────────────────────────────

@then("the snapshot file for \"{symbol}\" exists")
def step_snapshot_exists(context, symbol):
    assert _snapshot_path(context, symbol).exists(), (
        f"snapshot file for {symbol} not found"
    )


@then("the file contains {n:d} entry with forward_eps {eps:g}")
def step_file_one_entry(context, n, eps):
    symbol = getattr(context, "_symbol", None)
    entries = json.loads(_snapshot_path(context, symbol).read_text())
    assert len(entries) == n, f"expected {n} entry, got {len(entries)}"
    assert entries[0]["forward_eps"] == eps


@then("the result is None")
def step_result_none(context):
    assert context._record_result is None


@then("no snapshot file is created for \"{symbol}\"")
def step_no_snapshot_file(context, symbol):
    assert not _snapshot_path(context, symbol).exists(), (
        f"expected no snapshot file for {symbol}"
    )


@then("the snapshot file for \"{symbol}\" contains exactly {n:d} entry")
def step_file_exactly_n(context, symbol, n):
    entries = json.loads(_snapshot_path(context, symbol).read_text())
    assert len(entries) == n, f"expected {n} entry/entries, got {len(entries)}"


@then("the snapshot file for \"{symbol}\" contains {n:d} entries")
def step_file_n_entries(context, symbol, n):
    entries = json.loads(_snapshot_path(context, symbol).read_text())
    assert len(entries) == n, f"expected {n} entries, got {len(entries)}"


@then("the snapshot file for \"{symbol}\" has at most {n:d} entries")
def step_file_at_most_n(context, symbol, n):
    entries = json.loads(_snapshot_path(context, symbol).read_text())
    assert len(entries) <= n, f"expected at most {n} entries, got {len(entries)}"


@then("direction is \"{expected}\"")
def step_direction(context, expected):
    assert context._revision["direction"] == expected, (
        f"expected direction={expected!r}, got {context._revision['direction']!r}"
    )


@then("revision_pct is approximately {expected:f}")
def step_revision_pct(context, expected):
    actual = context._revision["revision_pct"]
    assert actual is not None, "revision_pct is None"
    assert abs(actual - expected) < 1.5, (
        f"expected revision_pct ≈ {expected}, got {actual}"
    )


@then("delta_90d is approximately {expected:f}")
def step_delta_90d(context, expected):
    actual = context._revision["delta_90d"]
    assert actual is not None
    assert abs(actual - expected) < 0.1, f"expected delta_90d ≈ {expected}, got {actual}"


@then("current_estimate is {expected:f}")
def step_current_estimate(context, expected):
    assert context._revision["current_estimate"] == expected, (
        f"expected current_estimate={expected}, got {context._revision['current_estimate']}"
    )


@then("snapshots_count is {n:d}")
def step_snapshots_count(context, n):
    assert context._revision["snapshots_count"] == n, (
        f"expected snapshots_count={n}, got {context._revision['snapshots_count']}"
    )


@then("as_of is \"{expected}\"")
def step_as_of(context, expected):
    assert context._revision["as_of"] == expected, (
        f"expected as_of={expected!r}, got {context._revision['as_of']!r}"
    )
