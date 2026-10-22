"""Deriving the options universe instead of typing it.

WHEEL_SLEEVE was 82 hand-typed symbols while the tradeable universe held 956.
874 names were harvested — bars kept, costs paid — and never looked at for
options. The owner noticed because MDB was absent: not blocked, not illiquid,
just never typed in. Of a 16-name sample from the 874, FIFTEEN clear the
ownership gate.
"""
import pytest

from tradepro_strategies.cli import wheel_universe as wu
from tradepro_strategies.quant_engine.options.bucket import is_ownable


def test_the_gate_is_the_router_s_gate_not_a_second_copy():
    # One implementation, two callers. A second "is this a name we would own"
    # is exactly what was removed from routing; re-introducing it here would
    # put the board and the router back into disagreement.
    import inspect
    src = inspect.getsource(wu)
    assert "is_ownable" in src
    assert "market_cap" in src
    # No local threshold constants — they must come from the router's module.
    assert "MIN_MARKET_CAP_USD = " not in src
    assert "MIN_PRICE_USD = " not in src


def test_etfs_are_kept_by_identity_not_measured():
    # yfinance returns marketCap=None for SPY/QQQ/XLE/SLV, so a naive run
    # dropped EVERY ETF — including the index and sector underlyings the sleeve
    # carries deliberately and the screen already exempts from the earnings
    # blackout. "Would I accept being assigned SPY" is not answered by a
    # company-size test.
    etfs = wu._etf_underlyings()
    assert {"SPY", "QQQ", "SLV", "GDX"} <= set(etfs)
    ok, why = is_ownable(price=600.0, market_cap_usd=None,
                         reporting_currency="USD", symbol="SPY")
    assert not ok, "an ETF fails the company gate — which is why it is exempted"


def test_a_fetch_failure_is_a_stated_no_not_a_silent_drop():
    # A name missing from the board for an unstated reason is the exact thing
    # this tool exists to end.
    ok, why, facts = wu._measure("THIS_IS_NOT_A_TICKER_XYZ")
    assert not ok
    assert why and len(why) > 10


def test_a_partial_scan_cannot_be_written_as_a_universe(monkeypatch, capsys):
    """THE BUG THIS TOOL SHIPPED WITH, CAUGHT ON ITS FIRST RUN.

    --limit reaches only the first N symbols alphabetically, so every name
    after them looks like a REMOVAL. A trial run reported GOOGL, MSFT and NVDA
    as dropped purely because it stopped at the A's. Combined with --write that
    would have emptied the board.
    """
    monkeypatch.setattr("sys.argv", ["tradepro-wheel-universe", "--limit", "5", "--write"])
    assert wu.main() == 2
    err = capsys.readouterr().err
    assert "partial" in err.lower() or "sample" in err.lower()
