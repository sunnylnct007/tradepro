"""Intraday engine strategy-gating: auto-emission is OPT-IN.

Un-integrated textbook strategies (orb/vwap_mean_reversion/bollinger_bounce/
ma_crossover) stay registered but must NOT auto-emit orders on a bare
scheduled tick — they were producing T212 orders that never routed and were
auto-cancelled as stale. See _INTRADAY_AUTORUN_STRATEGY_NAMES.
"""
from tradepro_strategies.cli.intraday_engine import (
    _INTRADAY_AUTORUN_STRATEGY_NAMES,
    _resolve_enabled_strategies,
)


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
    out = _resolve_enabled_strategies({"strategy": "vwap_mean_reversion"})
    assert set(out) == {"vwap_mean_reversion"}


def test_settings_block_is_opt_in():
    # Only entries explicitly present AND enabled run; a name absent from the
    # block does NOT run (previously it defaulted to enabled).
    enabled = _resolve_enabled_strategies(
        {"strategies": {"vwap_mean_reversion": {"enabled": True}}}
    )
    assert set(enabled) == {"vwap_mean_reversion"}

    disabled = _resolve_enabled_strategies(
        {"strategies": {"vwap_mean_reversion": {"enabled": False}}}
    )
    assert "vwap_mean_reversion" not in disabled
