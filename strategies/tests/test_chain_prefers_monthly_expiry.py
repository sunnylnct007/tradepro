"""The screen must not select a weekly and then reject it for being thin.

THE BUG (30 Aug 2026). The screen targets 35 DTE. The MONTH was chosen
correctly — October, whose 3rd Friday at 47 days is nearest 35 — but passing
`targetDte` let the server re-pick the closest listed expiry INSIDE that month,
which is a weekly (Oct 2, 33d) rather than the monthly (Oct 16, 47d).

Weeklies carry a fraction of a monthly's open interest, and the liquidity gate
(OI >= 250) is calibrated for monthlies. Measured the same minute:

    SPY  weekly Sep 30   median OI 284
    SPY  monthly Oct 16  median OI 654      <- 2.3x deeper
    XOM  weekly Sep 25   median OI  29      <- fails the gate outright

So the screen picked the thin contract and then blocked it for thinness. An
external review saw the same OI=57 on XOM and concluded a yfinance fallback was
fabricating data. It was not: the number was real, and it was the WEEKLY's.
"""
from __future__ import annotations

from unittest.mock import patch

from tradepro_strategies.quant_engine.options import chains_g3


def _resp(payload, status=200):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload
    return R()


def _run(chain_payload, months=("OCT26", "NOV26"), **kw):
    """Drive fetch_chain_g3 against a stubbed API, capturing request params."""
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append({"url": url, "params": params or {}})
        if url.endswith("/months"):
            return _resp({"months": list(months)})
        return _resp(chain_payload() if callable(chain_payload) else chain_payload)

    with patch.object(chains_g3.requests, "get", side_effect=fake_get), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "tok")):
        out = chains_g3.fetch_chain_g3("XOM", target_dte=35, right="P", **kw)
    return out, seen


def _legs(n, oi):
    return [{"strike": 100 + i, "right": "P", "bid": 1.0, "ask": 1.2,
             "openInterest": oi, "maturityDate": "20261016"} for i in range(n)]


def test_monthly_expiry_is_requested_by_date_not_by_target_dte():
    """The whole fix. Asking for `targetDte` is what let a weekly be chosen."""
    _out, seen = _run({"spot": 157.0, "legs": _legs(20, 500)})
    chain_call = [s for s in seen if not s["url"].endswith("/months")][0]
    assert "expiry" in chain_call["params"], (
        "chain requested without an explicit expiry — the server will re-pick "
        "the nearest listed expiry, which is a weekly")
    assert chain_call["params"]["expiry"] == "20261016", chain_call["params"]
    assert "targetDte" not in chain_call["params"]


def test_a_monthly_with_no_open_interest_falls_back_to_the_weekly():
    """Preferring monthlies UNCONDITIONALLY would have made things worse.

    Measured live: XOM's Oct-16 monthly returned 10 legs all carrying OI 0
    while its weekly returned 20 with real OI. Blindly switching would have
    made the liquidity gate fire harder on exactly the names it was already
    wrongly rejecting.
    """
    calls = {"n": 0}

    def payload():
        calls["n"] += 1
        # first call = the monthly (unusable), second = the weekly fallback
        return ({"spot": 157.0, "legs": _legs(10, 0)} if calls["n"] == 1
                else {"spot": 157.0, "legs": _legs(20, 300)})

    out, seen = _run(payload)
    chain_calls = [s for s in seen if not s["url"].endswith("/months")]
    assert len(chain_calls) == 2, "no fallback was attempted"
    assert "targetDte" in chain_calls[1]["params"], chain_calls[1]["params"]
    assert out is not None and len(out.puts) == 20


def test_a_monthly_with_too_few_strikes_falls_back():
    """Counted on the PARSED legs. QQQ returned a raw list that passed a naive
    length check and parsed down to a single put — the 0.27-delta selection
    would then take whatever it could reach and label it correctly-delta'd."""
    calls = {"n": 0}

    def payload():
        calls["n"] += 1
        return ({"spot": 157.0, "legs": _legs(2, 900)} if calls["n"] == 1
                else {"spot": 157.0, "legs": _legs(20, 300)})

    out, seen = _run(payload)
    assert len([s for s in seen if not s["url"].endswith("/months")]) == 2
    assert out is not None and len(out.puts) == 20


def test_an_explicit_expiry_is_never_overridden():
    """The short-dated tier asks for one exact weekly on purpose."""
    _out, seen = _run({"spot": 157.0, "legs": _legs(20, 500)},
                      expiry="2026-10-02")
    chain_call = [s for s in seen if not s["url"].endswith("/months")][0]
    assert chain_call["params"]["expiry"] == "20261002"


def test_prefer_monthly_can_be_switched_off():
    _out, seen = _run({"spot": 157.0, "legs": _legs(20, 500)},
                      prefer_monthly=False)
    chain_call = [s for s in seen if not s["url"].endswith("/months")][0]
    assert chain_call["params"].get("targetDte") == 35
    assert "expiry" not in chain_call["params"]
