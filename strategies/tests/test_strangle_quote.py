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
