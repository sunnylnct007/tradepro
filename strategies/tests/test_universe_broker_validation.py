"""Per-broker universe validation — _validate_universe_against_broker.

The Wikipedia→DB universe says what we'd like to trade; the broker catalog says
what the broker will accept. Their intersection is the live tradeable set. These
tests pin the filter logic deterministically with an injected catalog (no live
T212 fetch / no creds), covering: drop-untradeable, sizing-map pruning,
fail-open on catalog-unavailable, and per-broker pass-through.
"""
from __future__ import annotations

import argparse

import tradepro_strategies.cli.paper_session as ps
from tradepro_strategies.cli.paper_session import _validate_universe_against_broker


def _args(broker="t212", per_cap=None, sleeves_map=None):
    return argparse.Namespace(
        broker=broker,
        per_symbol_capital=per_cap,
        sleeves_map=sleeves_map,
    )


def _patch_catalog(monkeypatch, catalog):
    # The Mac validation reads the tradeable set from the BACKEND (the Mac has
    # no direct T212 creds); patch that source.
    monkeypatch.setattr(ps, "_fetch_t212_tradeable_symbols", lambda: catalog)


def test_drops_symbols_not_in_t212_catalog(monkeypatch):
    # JEF + AAPL are listed on T212; WFRD/LFUS/COHR are not → dropped.
    _patch_catalog(monkeypatch, {"AAPL", "AMZN", "JEF", "NOW"})
    kept = _validate_universe_against_broker(
        _args(), ["AAPL", "WFRD", "LFUS", "JEF", "COHR"]
    )
    assert kept == ["AAPL", "JEF"]


def test_prunes_per_symbol_capital_and_sleeves_in_lockstep(monkeypatch):
    _patch_catalog(monkeypatch, {"AAPL", "JEF"})
    per_cap = {"AAPL": 833.0, "WFRD": 249.0, "JEF": 249.0, "LFUS": 249.0}
    sleeves = {"high_beta": {"symbols": ["AAPL", "WFRD", "JEF", "LFUS"], "size": 30}}
    args = _args(per_cap=per_cap, sleeves_map=sleeves)
    kept = _validate_universe_against_broker(args, ["AAPL", "WFRD", "JEF", "LFUS"])
    assert kept == ["AAPL", "JEF"]
    # Sizing maps must never reference a dropped name.
    assert set(args.per_symbol_capital) == {"AAPL", "JEF"}
    assert args.sleeves_map["high_beta"]["symbols"] == ["AAPL", "JEF"]
    assert set(args.dropped_untradeable) == {"WFRD", "LFUS"}


def test_fail_open_when_catalog_unavailable(monkeypatch):
    # Catalog fetch failed (None) → trade the FULL universe, never halt the desk.
    _patch_catalog(monkeypatch, None)
    syms = ["AAPL", "WFRD", "LFUS"]
    args = _args()
    kept = _validate_universe_against_broker(args, syms)
    assert kept == syms              # full universe traded
    assert args.dropped_untradeable == []   # nothing dropped on fail-open


def test_fx_pairs_and_qualified_tickers_pass_through(monkeypatch):
    # FX pairs + already-suffixed tickers aren't US equities — keep them.
    _patch_catalog(monkeypatch, {"AAPL"})
    kept = _validate_universe_against_broker(
        _args(), ["AAPL", "EURUSD", "GBPJPY", "GLD_US_EQ"]
    )
    assert kept == ["AAPL", "EURUSD", "GBPJPY", "GLD_US_EQ"]


def test_non_t212_broker_is_passthrough(monkeypatch):
    # IG / IBKR validate via their own epic maps — not this T212 path.
    called = {"n": 0}
    monkeypatch.setattr(
        ps, "_fetch_t212_tradeable_symbols",
        lambda: called.__setitem__("n", called["n"] + 1) or {"AAPL"},
    )
    syms = ["EURUSD", "GBPUSD", "WFRD"]
    kept = _validate_universe_against_broker(_args(broker="ig"), syms)
    assert kept == syms
    assert called["n"] == 0  # T212 catalog never consulted for a non-T212 broker
