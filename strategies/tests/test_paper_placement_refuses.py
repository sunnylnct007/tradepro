"""Paper placement must refuse rather than guess.

Owner: "ok start with the us paper execution". This turns modelled
Black-Scholes credits into REAL fills — the one input no backtest can
manufacture. But a strangle placed on the wrong basis is worse than no data at
all, because it contaminates the very record being built to settle this
strategy.
"""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.cli import index_strangle_paper as P


def _row(**kw):
    base = {"market": "SPY", "status": "CANDIDATE", "provisional": False,
            "session_state": "open",
            "legs": {"monthly": {"dte": 21, "put_strike": 740.0,
                                 "call_strike": 800.0, "forward": 770.0}}}
    base.update(kw)
    return base


def test_india_is_never_placed():
    """No paper account exists for India — the owner places those by hand."""
    for m in ("NIFTY", "BANKNIFTY"):
        res = P.place_paper(_row(market=m))
        assert res["placed"] is False
        assert "not paper-tradeable" in res["reason"]


def test_provisional_strikes_are_never_placed():
    """Placing off a stale close is the lopsided trade that was just fixed —
    on 31 Aug it left the put 116 points away and the call 384."""
    res = P.place_paper(_row(provisional=True))
    assert res["placed"] is False and "PROVISIONAL" in res["reason"]


def test_a_shut_session_is_never_placed():
    for state in ("pre_open", "closed"):
        res = P.place_paper(_row(session_state=state))
        assert res["placed"] is False and state in res["reason"]


def test_a_stand_aside_row_is_never_placed():
    """The gate is the whole strategy. If it says stand aside, nothing goes."""
    res = P.place_paper(_row(status="stand aside"))
    assert res["placed"] is False and "not a candidate" in res["reason"]


def test_index_products_are_not_placed_as_etfs():
    """SPX/XSP/NDX are modelled here off ^GSPC and ^NDX — XSP in particular is
    ^GSPC/10, which is not an IBKR symbol. Placing those as if they were the
    ETF would trade the wrong instrument at 10x the size."""
    for m in ("SPX", "XSP", "NDX"):
        assert P.MARKETS[m]["paper_trade"] is False
        res = P.place_paper(_row(market=m))
        assert res["placed"] is False


def test_only_liquid_etfs_are_enabled():
    enabled = {m for m, c in P.MARKETS.items() if c.get("paper_trade")}
    assert enabled == {"SPY", "QQQ", "GOLD"}


def test_monthly_expiry_is_a_third_friday_in_the_future():
    today = dt.date(2026, 8, 31)
    for dte in (7, 21, 45):
        e = dt.date.fromisoformat(P._monthly_expiry(dte, today=today))
        assert e.weekday() == 4, f"{e} is not a Friday"
        assert 15 <= e.day <= 21, f"{e} is not the third Friday"
        assert e > today


def test_monthly_expiry_never_returns_today_or_the_past():
    """A same-day expiry would be a 0-DTE trade — explicitly excluded by the
    strategy ("we will rarely sell with 1 DTE"), and its worst day costs 8.8x
    the credit collected."""
    third_friday = dt.date(2026, 9, 18)
    assert dt.date.fromisoformat(P._monthly_expiry(21, today=third_friday)) > third_friday
