"""Paper placement must refuse rather than guess.

Owner: "ok start with the us paper execution". This turns modelled
Black-Scholes credits into REAL fills — the one input no backtest can
manufacture. But a strangle placed on the wrong basis is worse than no data at
all, because it contaminates the very record being built to settle this
strategy.
"""
from __future__ import annotations

import datetime as dt

import os
from unittest.mock import patch

from tradepro_strategies.cli import index_strangle_paper as P


def _desk_restrictions_lifted():
    """These tests are about PLACEMENT MECHANICS, not about which unit the desk
    happens to be trading today.

    TWO desk-scope restrictions fire BEFORE the provisional / shut-session /
    stand-aside logic these tests exist to check, so both must be lifted to
    reach it:

      * `placement_parked` — SPY/QQQ/GOLD, parked 12 Sep 2026 while the IBKR
        market-data session is dark.
      * PLACE_UNITS — from 23 Sep 2026 the desk places XSP monthly ALONE until
        that unit is reliable, so a SPY row is refused before any mechanics run.

    Lifted, not deleted: both are real behaviour with their own tests
    (test_index_strangle_markets_one_definition.py,
    test_place_one_unit_until_reliable.py).
    """
    import copy
    import contextlib
    m = copy.deepcopy(P.MARKETS)
    for c in m.values():
        c.pop("placement_parked", None)

    @contextlib.contextmanager
    def _both():
        with patch.object(P, "MARKETS", m), \
             patch.dict(os.environ, {"TRADEPRO_STRANGLE_PLACE_UNITS": "all"}):
            yield
    return _both()


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
        with _desk_restrictions_lifted():
            res = P.place_paper(_row(market=m))
        assert res["placed"] is False
        assert "not paper-tradeable" in res["reason"]


def test_provisional_strikes_are_never_placed():
    """Placing off a stale close is the lopsided trade that was just fixed —
    on 31 Aug it left the put 116 points away and the call 384."""
    with _desk_restrictions_lifted():
        res = P.place_paper(_row(provisional=True))
    assert res["placed"] is False and "PROVISIONAL" in res["reason"]


def test_a_shut_session_is_never_placed():
    for state in ("pre_open", "closed"):
        with _desk_restrictions_lifted():
            res = P.place_paper(_row(session_state=state))
        assert res["placed"] is False and state in res["reason"]


def test_a_stand_aside_row_is_never_placed():
    """The gate is the whole strategy. If it says stand aside, nothing goes."""
    with _desk_restrictions_lifted():
        res = P.place_paper(_row(status="stand aside"))
    assert res["placed"] is False and "not a candidate" in res["reason"]


def test_index_products_are_placed_as_INDICES_never_as_etfs():
    """SPX/XSP/NDX became placeable in 2349cd6 (1 Sep, owner: "ok spx/ndx").

    These asserted `paper_trade is False` for weeks, behind a note saying they
    needed "their own IBKR symbol mapping, not the ETF". That was right about
    the DANGER and wrong about the cost: two small bugs were blocking them —
    place_paper sent the YAHOO symbol (^GSPC is not an IBKR symbol), and
    resolution was hardcoded to STK when a cash index is IND.

    The danger the old assertion protected against is real and still guarded,
    just not here: test_broker_symbol_mapping.py proves no placeable market
    sends a caret-prefixed Yahoo symbol to the broker and that a cash index
    declares IND. What this test now pins is that the INDEX products are never
    quietly re-typed as equities — trading SPX as if it were an ETF is the
    wrong instrument at the wrong size.
    """
    # NDX was re-disabled in f6a6368 for a DIFFERENT reason than before: not a
    # mapping gap but funding — one contract is ~$2.5M collateral the paper
    # account cannot carry. The instrument-safety asserts below still apply to
    # its config: if it is ever re-enabled, it must come back as an index.
    for m in ("SPX", "XSP", "NDX"):
        cfg = P.MARKETS[m]
        assert cfg["paper_trade"] is (m != "NDX"), (
            f"{m}: SPX/XSP placeable; NDX stays off until it can be funded")
        assert cfg.get("broker_sec_type") == "IND", (
            f"{m} is a cash index and must resolve as IND, not as an equity")
        assert not str(cfg.get("broker_symbol", "")).startswith("^"), (
            f"{m} must not send a Yahoo symbol to the broker")


def test_the_placeable_set_is_deliberate():
    """A market becoming placeable must be a DECISION, not a drift.

    Pinned as a set so adding one is a visible edit here, with the size review
    that goes with it: one NDX contract is ~33x the SPY position (collateral
    ~$2.5M vs ~$77k), so "one more market" is not a small change.
    """
    enabled = {m for m, c in P.MARKETS.items() if c.get("paper_trade")}
    assert enabled == {"SPY", "QQQ", "GOLD", "SPX", "XSP"}
    # India has no paper account — the owner places those by hand.
    assert not any(P.MARKETS[m]["paper_trade"] for m in ("NIFTY", "BANKNIFTY"))


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


def test_a_transient_chain_error_is_retried_but_a_real_refusal_is_not():
    """16 Sep 2026: the run fired at 14:12:21 and ALL SIX markets returned
    "IBKR returned NO strikes for conid ... month OCT26" — SPX and XSP included,
    which had placed every session that week. Forty-five minutes later the
    identical call returned 40 legs with a live spot. One transient blackout on
    the placement minute, and the desk took a zero for the day.

    We place once. Retrying the pass is the difference between a blank day and
    a traded one — but only for causes that can change. A park, a margin
    rejection, a shut session or PROVISIONAL strikes are ANSWERS; repeating
    them burns the window and buries the real reason.
    """
    import inspect
    from tradepro_strategies.cli import index_strangle_paper as P

    src = inspect.getsource(P.main)
    assert "TRANSIENT = (" in src
    for phrase in ("could not resolve", "no strikes", "no two-sided quote"):
        assert phrase in src, f"{phrase} should be retried"
    # These must NOT be in the retry set.
    i = src.index("TRANSIENT = (")
    block = src[i:i + 400]
    for never in ("PARKED", "not paper-tradeable", "PROVISIONAL", "insufficient"):
        assert never.lower() not in block.lower(), f"{never} must not be retried"


def test_the_retry_is_bounded_well_inside_the_lambda_ceiling():
    """Lambda stops at 900s and the job spends ~120s before placing. A run
    killed mid-placement is worse than one that gave up honestly."""
    import inspect
    from tradepro_strategies.cli import index_strangle_paper as P

    src = inspect.getsource(P.main)
    assert "RETRY_BUDGET_S" in src and "RETRY_WAIT_S" in src
    assert "retry budget spent" in src, "exhausting the budget must be SAID"


def test_a_transient_failure_is_not_recorded_as_a_refusal_first():
    """Recording it would put a place_error on the row that the next attempt
    then has to clear — and on 8 Sep exactly that left a row reading
    placed=true WITH a stale refusal still attached."""
    import inspect
    from tradepro_strategies.cli import index_strangle_paper as P

    src = inspect.getsource(P.main)
    i = src.index("if _is_transient(res):")
    # The branch body ends at its `continue`. Slicing past that swept in the
    # _finish call that correctly sits OUTSIDE it — the first version of this
    # test failed on the code being right.
    branch = src[i:src.index("continue", i)]
    assert "_finish" not in branch, "a transient failure must not be recorded yet"
    assert "still_pending.append" in branch, "it must be queued for another go"
