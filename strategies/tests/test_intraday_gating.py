"""Intraday engine strategy-gating: auto-emission is OPT-IN.

Un-integrated textbook strategies (bollinger_bounce/ma_crossover, and
historically vwap_mean_reversion and orb — both now gone from the registry)
must NOT auto-emit orders on a bare scheduled tick — they were producing T212 orders that never routed and were
auto-cancelled as stale. See _INTRADAY_AUTORUN_STRATEGY_NAMES.
"""
import pytest

import tradepro_strategies.paper.strategies as _strategies
from tradepro_strategies.cli.intraday_engine import (
    _INTRADAY_AUTORUN_STRATEGY_NAMES,
    _resolve_enabled_strategies,
)

# A REGISTERED strategy to exercise name resolution with — taken FROM the
# registry, not named here.
#
# These tests only need "some name the registry knows"; they are about the
# engine's gating, not about any one strategy. They used to hardcode
# vwap_mean_reversion, which was deleted (21 Aug 2026), then orb, which was
# retired (13 Oct 2026) — each time the fixture went stale and the test failed
# for a reason that had nothing to do with what it checks. Deriving it means
# that cannot happen a third time.
_REGISTERED = sorted(set(_strategies.available()) - set(_INTRADAY_AUTORUN_STRATEGY_NAMES))
pytestmark = pytest.mark.skipif(
    not _REGISTERED,
    reason="no registered non-autorun strategy to resolve — nothing to gate",
)
A_REGISTERED_STRATEGY = _REGISTERED[0] if _REGISTERED else ""


def test_bare_tick_auto_runs_only_the_allowlist():
    # No explicit strategy + no settings block (a plain scheduled tick) →
    # run only the integrated allowlist (empty by design → nothing).
    assert _resolve_enabled_strategies({}) == {
        n: {} for n in _INTRADAY_AUTORUN_STRATEGY_NAMES
    }


def test_vwap_does_not_auto_emit_on_bare_tick():
    # The specific regression: vwap_mean_reversion must not auto-run.
    assert "vwap_mean_reversion" not in _resolve_enabled_strategies({})


def test_explicit_scan_runs_exactly_that_strategy():
    out = _resolve_enabled_strategies({"strategy": A_REGISTERED_STRATEGY})
    assert set(out) == {A_REGISTERED_STRATEGY}


def test_deleted_strategy_request_resolves_to_nothing():
    # A settings block naming a strategy that no longer exists must resolve
    # to an empty run (fail-loud upstream), never a crash or a silent
    # substitute.
    assert _resolve_enabled_strategies(
        {"strategy": "vwap_mean_reversion"}) == {}


def test_settings_block_is_opt_in():
    # Only entries explicitly present AND enabled run; a name absent from the
    # block does NOT run (previously it defaulted to enabled).
    enabled = _resolve_enabled_strategies(
        {"strategies": {A_REGISTERED_STRATEGY: {"enabled": True}}}
    )
    assert set(enabled) == {A_REGISTERED_STRATEGY}

    disabled = _resolve_enabled_strategies(
        {"strategies": {A_REGISTERED_STRATEGY: {"enabled": False}}}
    )
    assert A_REGISTERED_STRATEGY not in disabled
