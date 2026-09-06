"""QUOTED, NOT TRADED — pricing the strangles we cannot place.

Owner, 6 Sep 2026: for the markets we cannot fund, "we can atleast see the
potential gain and loss if we would have placed".

NDX resolves perfectly and simply cannot be funded (~$2.85M collateral against
a ~$151k account). It has produced a decision a day and nothing measurable
since it was configured. A real bid/ask mid is not a fill, but it IS a price
someone was offering — unlike credit_modelled, which is Black-Scholes with no
skew, no spread and no counterparty.

The same quote on markets we DO place measures SLIPPAGE against credit_actual,
which no backtest can supply.
"""
from unittest.mock import patch

import tradepro_strategies.cli.index_strangle_paper as P


def _row(market="NDX"):
    return {"market": market, "as_of": "2026-09-07", "exchange_date": "2026-09-07",
            "legs": {"monthly": {"dte": 17, "put_strike": 29000, "call_strike": 30100}}}


class _R:
    content = b"{}"
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


def test_a_quote_is_money_not_per_share():
    # mid 12.50/share on a lot of 100 is $1,250 — recording 12.50 would repeat
    # the 100x mistake the close job made on 2 Sep.
    payload = {"ok": True, "midPerShare": 12.50, "widestSpread": 0.40}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            q = P.quote_strangle(_row())
    assert q["ok"] is True
    assert q["credit"] == 1250.0
    assert q["spread"] == 0.40


def test_a_one_sided_book_is_reported_not_invented():
    payload = {"ok": False, "error": "a leg had no two-sided quote — no honest mid exists"}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            q = P.quote_strangle(_row())
    assert q["ok"] is False
    assert "two-sided" in q["error"]


def test_india_is_skipped_because_no_price_source_exists():
    # Yahoo does not serve NSE chains and IBKR does not cover Indian options.
    # A quote here would have to be INVENTED, which is the one thing this
    # feature exists to avoid.
    for m in ("NIFTY", "BANKNIFTY"):
        assert P.quote_strangle(_row(m)) is None


def test_the_scheduled_run_actually_asks_for_quotes():
    # lambda_handler.py sits at the repo's strategies/ root, not inside the
    # package, so it is read as a FILE. A flag that exists but is never passed
    # by the scheduler records nothing, which is indistinguishable from not
    # having built it.
    import pathlib
    here = pathlib.Path(__file__).resolve()
    handler = next(p for p in here.parents if (p / "lambda_handler.py").exists()) / "lambda_handler.py"
    src = handler.read_text()
    i = src.index('"index_strangle_paper"')
    assert '"--quote"' in src[i:i + 400], "the daily job must quote, or nothing is recorded"


# ---------------------------------------------------------------------------
# IS THE PAIR ACTUALLY BALANCED?
#
# Strikes sit at +/-1.5x the expected move — EQUIDISTANT IN PERCENT. Index
# options carry skew, so an equidistant put is fatter than the call on premium
# AND delta, leaving the position NET LONG the market. A short strangle is not
# supposed to be directional, and it is why a falling session hurts more than
# the design intends: SPX on 1 Sep 2026 lost 1,625 on the put while the call
# gained only 686.
#
# The owner sells the call 200-500 points closer than the system on every one
# of four sessions — delta-matching by feel — and beat it three times in four.
#
# These columns MEASURE that. Nothing here changes strike selection: the
# published 82.9% win rate describes the equidistant rule, and swapping the
# selection would invalidate it exactly as the condor substitution would have.
# ---------------------------------------------------------------------------

def test_the_quote_carries_both_deltas_and_the_net():
    payload = {"ok": True, "midPerShare": 12.50, "widestSpread": 0.40,
               "putDelta": -0.25, "callDelta": 0.10, "netDelta": 0.15}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            q = P.quote_strangle(_row())
    assert q["put_delta"] == -0.25
    assert q["call_delta"] == 0.10
    # Short a put is +delta, short a call is -delta: -(-0.25) - 0.10 = +0.15.
    assert q["net_delta"] == 0.15


def test_a_missing_delta_is_none_not_zero():
    # Zero would read as PERFECTLY BALANCED — the most misleading possible
    # substitute for "we do not know".
    payload = {"ok": True, "midPerShare": 12.50, "widestSpread": 0.40}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            q = P.quote_strangle(_row())
    assert q["put_delta"] is None and q["net_delta"] is None


def test_strike_selection_is_UNCHANGED_by_this():
    # The measurement must not quietly become the rule. strike_pair still
    # places on distance; changing it needs a backtest, not an observation.
    import inspect
    src = inspect.getsource(P.strike_pair)
    assert "delta" not in src.lower(), \
        "strike_pair must stay distance-based until a backtest says otherwise"
