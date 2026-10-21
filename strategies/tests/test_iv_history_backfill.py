"""The 52-week IV backfill — the one thing standing between us and IV-Rank.

IV-Rank is the gate the owner actually asked for: "no hardcoded cosntandt but
variableper symbol". A rank is defined entirely by the name's own history, so
it cannot be tuned.

It has never been reachable. The window is accumulated one session at a time
and, measured 21 Oct 2026 after ten weeks, stood at NVDA 41 days, MSFT 31,
PG 31, WDC 15, CRWV 0 — against a 60-day minimum. Until it matures the risk
engine falls back to the IV/HV ratio bridge, which the owner disproved on WDC.

IBKR serves the whole year in one request, outside market hours. These tests
cover the parsing, because the fetch itself needs a live Gateway.
"""
import pytest

from tradepro_strategies.quant_engine.options.iv_rank import fetch_iv_history


class _Bar:
    def __init__(self, date, close):
        self.date, self.close = date, close


# A sentinel contract, so no test needs ib_insync imported at all — see the
# note in fetch_iv_history about eventkit grabbing the event loop on import.
CONTRACT = object()


class _IB:
    """Minimal stand-in: records the request, returns canned bars."""
    def __init__(self, bars):
        self.bars, self.kwargs = bars, None

    def qualifyContracts(self, *a):
        return list(a)

    def reqHistoricalData(self, contract, **kw):
        self.kwargs = kw
        return self.bars


def test_returns_dated_pairs_oldest_first():
    ib = _IB([_Bar("2026-10-19", 0.31), _Bar("2026-10-20", 0.34)])
    out = fetch_iv_history("MSFT", ib, contract=CONTRACT)
    assert out == [("2026-10-19", 0.31), ("2026-10-20", 0.34)]


def test_asks_for_implied_volatility_not_price():
    # The whole point. A plain price history would silently populate the IV
    # dataset with CLOSES, and every rank computed from it would be garbage
    # that looked perfectly well-formed.
    ib = _IB([_Bar("2026-10-20", 0.3)])
    fetch_iv_history("MSFT", ib, lookback="1 Y", contract=CONTRACT)
    assert ib.kwargs["whatToShow"] == "OPTION_IMPLIED_VOLATILITY"
    assert ib.kwargs["durationStr"] == "1 Y"
    assert ib.kwargs["barSizeSetting"] == "1 day"


def test_a_junk_bar_is_dropped_not_fatal():
    # The store refuses iv outside (0, 5] and rejects the WHOLE batch for one
    # bad row. Dropping it here keeps a year of good data for the sake of one
    # point; keeping it would lose the lot.
    ib = _IB([_Bar("2026-10-18", 0.0), _Bar("2026-10-19", 9.9),
              _Bar("2026-10-20", 0.33), _Bar("2026-10-21", None)])
    assert fetch_iv_history("MSFT", ib, contract=CONTRACT) == [("2026-10-20", 0.33)]


def test_no_bars_raises_rather_than_returning_empty():
    # A backfill that silently does nothing is the worst outcome: the gap it
    # leaves is indistinguishable from the gap it was meant to close.
    with pytest.raises(ValueError, match="no OPTION_IMPLIED_VOLATILITY"):
        fetch_iv_history("MSFT", _IB([]), contract=CONTRACT)


def test_all_bars_unusable_raises_too():
    with pytest.raises(ValueError, match="none with a usable value"):
        fetch_iv_history("MSFT", _IB([_Bar("2026-10-20", 0.0)]), contract=CONTRACT)


def test_dates_are_normalised_to_iso_day():
    # IBKR hands back a datetime for some bar sizes; the store keys on a DATE
    # and a timestamp would create a second row for the same session.
    ib = _IB([_Bar("2026-10-20 00:00:00", 0.33)])
    assert fetch_iv_history("MSFT", ib, contract=CONTRACT) == [("2026-10-20", 0.33)]
