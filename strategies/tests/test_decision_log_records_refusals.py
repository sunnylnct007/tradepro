"""Every evaluation is persisted — the refusals most of all.

Owner, 31 Aug 2026: "i need the stuff to be logged for analysis later on ... so
we can evaluate what we did and why we did it and check if it was right or
not", and "the whole purpose of running this on a daily basis is to gather as
much data we can for developing, backtesting new strategy".

THE LEDGER THIS REPLACES WAS EPHEMERAL. It writes under $HOME and the Lambda
sets HOME=/tmp, wiped between invocations. Every scheduled decision since the
Lambda migration was thrown away — the forward test ran daily and recorded
nothing.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from tradepro_strategies.cli import index_strangle_paper as P


def _rows():
    return [
        {"market": "NIFTY", "status": "CANDIDATE", "as_of": "2026-08-31",
         "exchange_date": "2026-08-31", "reason": "India VIX 10.68 <= 12.5",
         "vol_index": 10.68, "vol_threshold": 12.5, "iv_used": 10.68,
         "spot": 24117.55, "spot_basis": "session_open", "provisional": False,
         "session_state": "open", "lot": 75,
         "legs": {"weekly": {"dte": 7, "put_strike": 23900.0,
                             "call_strike": 24400.0, "forward": 24096.0},
                  "monthly": {"dte": 21, "put_strike": 23900.0,
                              "call_strike": 24400.0, "forward": 24156.0}}},
        {"market": "SPY", "status": "stand aside", "as_of": "2026-08-28",
         "exchange_date": "2026-08-31", "reason": "^VIX 14.43 ABOVE 13.5",
         "vol_index": 14.43, "vol_threshold": 13.5, "spot": 769.35,
         "spot_basis": "prior_close", "provisional": True,
         "session_state": "pre_open", "lot": 100, "legs": {}},
        {"market": "X", "status": "no_data", "reason": "nope"},
    ]


def _capture():
    sent = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        sent["url"], sent["body"] = url, json

        class R:
            def raise_for_status(self): pass
            def json(self): return {"ok": True, "rows": len(json or [])}
        return R()
    return sent, fake_post


def test_stand_asides_are_persisted_not_just_trades():
    """The gate is the strategy. A log of only the trades cannot show whether
    the threshold is set right — the refusals are the evidence."""
    sent, fake = _capture()
    with patch("requests.post", fake), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "t")):
        out = P.push_decisions(_rows())
    assert out["pushed"] > 0
    decisions = {r["market"]: r["decision"] for r in sent["body"]}
    assert decisions["SPY"] == "STAND_ASIDE"
    assert decisions["NIFTY"] == "CANDIDATE"


def test_no_data_rows_are_skipped():
    """A row with no reading is not a decision — recording it as one would
    inflate the denominator when the gate is later judged."""
    sent, fake = _capture()
    with patch("requests.post", fake), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "t")):
        P.push_decisions(_rows())
    assert all(r["market"] != "X" for r in sent["body"])


def test_each_expiry_is_its_own_row():
    """Weekly and monthly price off different forwards and carry different
    strikes. Collapsing them would record a trade that was never described."""
    sent, fake = _capture()
    with patch("requests.post", fake), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "t")):
        P.push_decisions(_rows())
    kinds = {r["expiryKind"] for r in sent["body"] if r["market"] == "NIFTY"}
    assert kinds == {"weekly", "monthly"}


def test_the_WHY_is_recorded_not_just_the_what():
    """"check if it was right or not" needs the inputs the decision turned on,
    or it can never be re-judged without re-deriving them."""
    sent, fake = _capture()
    with patch("requests.post", fake), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "t")):
        P.push_decisions(_rows())
    spy = next(r for r in sent["body"] if r["market"] == "SPY")
    for k in ("volIndex", "volThreshold", "reason", "spotBasis",
              "provisional", "sessionState"):
        assert spy[k] is not None, k
    assert spy["provisional"] is True and spy["spotBasis"] == "prior_close"


def test_a_push_failure_never_kills_the_job():
    """load_credentials EXITS rather than raising. `except Exception` here is
    how the Lambda died on 31 Aug; this must survive a SystemExit."""
    def boom(*a, **kw):
        raise SystemExit(2)
    with patch("tradepro_strategies.cli.push_to_api.load_credentials", boom):
        out = P.push_decisions(_rows())
    assert out["pushed"] == 0 and "error" in out
