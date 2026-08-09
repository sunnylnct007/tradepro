"""Session-wide test safety net.

2026-08-08 incident: `test_regime_nan_guard.py` and `test_cache_garbage_bars.py`
exercised code paths (`_regime_ok`, `_drop_garbage_bars`) that fire-and-forget
POST to the central `run_log` API. Only ONE test in each file explicitly
mocked that call; the rest ran with real credentials present on this dev
machine and posted synthetic test data (fake symbols, 2026-01-02 dates,
literal "TEST" tickers) straight into the LIVE cockpit run log.

`log_run`/`log_runs` (tradepro_strategies/run_log.py) are deliberately
best-effort — they swallow all delivery errors so observability can never
break the operation it's observing — which is exactly why a blanket network
block here is safe: every real call site already treats a failed POST as a
no-op, so blocking it in tests changes no test's control flow, only stops
it leaving the process. No test in this suite is meant to hit a real
network endpoint; `_data_fn` / injected fakes are the sanctioned pattern for
data, and this fixture stops anything that fell through that pattern from
being able to leak.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def block_real_network_calls(monkeypatch):
    """Autouse for every test in this suite. A test that wants to assert on
    a specific network call still does its own `patch(...)` — that patch
    layers on top of this one and takes precedence within its `with` block,
    same as any nested mock.patch. This fixture is the fallback for
    everything that doesn't, so a leak fails closed instead of reaching
    the real API."""
    blocked = MagicMock()
    blocked.side_effect = AssertionError(
        "a test tried to make a real HTTP call (requests.post/get) — "
        "mock the call site explicitly (see tests/conftest.py)"
    )
    monkeypatch.setattr("requests.post", blocked, raising=False)
    monkeypatch.setattr("requests.get", blocked, raising=False)
