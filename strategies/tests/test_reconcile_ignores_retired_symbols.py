"""The chart-store reconcile must compare the ACTIVE universe, not history.

THE NOISE, 2 Oct 2026. The nightly harvest exited 1 with:

    FATAL: chart store still diverges from the golden store after repair
    MISSING from the chart store: 12
      BZ=F, CL=F, GC=F, HG=F, KC=F, NG=F, PA=F, PL=F, SI=F, ZC=F …

None of those is in the universe. They are Yahoo futures tickers that left
it long ago, still sitting in the append-only golden store, and never pushed
to a chart store keyed on IBKR symbols. The repair dutifully pushed 0 rows
because there is nothing current to push, and the job went red — as it would
have every night, for ever.

A job that is red every day is worse than no job: it is how a REAL divergence
gets ignored. This check exists to prove the charts show what the strategies
read, and a symbol no strategy reads cannot violate that.

Retired names are still COUNTED AND NAMED in the output. Silently dropping
them would recreate the problem this tool exists to solve — a copy nobody
checks is a second source.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradepro_strategies.cli import bar_store_reconcile as R


@pytest.fixture
def golden(tmp_path):
    """A golden store holding one active name and one retired one."""
    base = tmp_path / "bar_cache"
    for sym in ("AAPL", "BZ=F"):
        (base / "us_etf" / sym / "1d").mkdir(parents=True)
    return base


def _patch(monkeypatch, *, active, chart_has, golden_date="2026-09-30"):
    monkeypatch.setattr(
        R, "chart_store_last_bars",
        lambda api_base, token, resolution: {
            s: dt.date.fromisoformat(golden_date) for s in chart_has})
    monkeypatch.setattr(
        R, "golden_last_bar",
        lambda base_dir, sym, asset, resolution: dt.date.fromisoformat(golden_date))
    import tradepro_strategies.universe as U
    monkeypatch.setattr(U, "universe_symbols", lambda strict=False: list(active))


def test_a_retired_symbol_is_not_reported_as_missing(golden, monkeypatch):
    """The exact 2 Oct failure: BZ=F absent from charts is NOT a divergence."""
    _patch(monkeypatch, active={"AAPL"}, chart_has={"AAPL"})
    r = R.reconcile(base_dir=golden, asset="us_etf", resolution="1d",
                    api_base="http://api", token=None, max_lag=2)
    assert r["missing"] == [], (
        "a symbol that left the universe was reported as chart-store drift — "
        "this is what made the nightly harvest red every night")
    assert r["symbols"] == 1
    assert [s.upper() for s in r["retired"]] == ["BZ=F"]


def test_retired_symbols_are_named_not_silently_dropped(golden, monkeypatch):
    """Counted and named. A copy nobody checks is a second source."""
    _patch(monkeypatch, active={"AAPL"}, chart_has={"AAPL"})
    r = R.reconcile(base_dir=golden, asset="us_etf", resolution="1d",
                    api_base="http://api", token=None, max_lag=2)
    assert r["retired"], "retired names vanished from the report entirely"


def test_a_REAL_divergence_on_an_active_symbol_still_fails(golden, monkeypatch):
    """The guard must still bite. Narrowing the scope must not blunt it."""
    _patch(monkeypatch, active={"AAPL"}, chart_has=set())   # AAPL missing
    r = R.reconcile(base_dir=golden, asset="us_etf", resolution="1d",
                    api_base="http://api", token=None, max_lag=2)
    assert [m[0] for m in r["missing"]] == ["AAPL"], (
        "an ACTIVE symbol missing from the chart store must still be reported")


def test_unreadable_universe_checks_everything(golden, monkeypatch):
    """Fail-closed: if the universe cannot be read, compare the lot.

    An unreadable universe must not silently shrink the comparison to nothing
    and report a clean bill of health.
    """
    _patch(monkeypatch, active={"AAPL"}, chart_has={"AAPL"})
    import tradepro_strategies.universe as U
    monkeypatch.setattr(
        U, "universe_symbols",
        lambda strict=False: (_ for _ in ()).throw(RuntimeError("no universe")))
    r = R.reconcile(base_dir=golden, asset="us_etf", resolution="1d",
                    api_base="http://api", token=None, max_lag=2)
    assert r["symbols"] == 2, "fell back to checking fewer symbols, not more"
    assert [m[0].upper() for m in r["missing"]] == ["BZ=F"]
