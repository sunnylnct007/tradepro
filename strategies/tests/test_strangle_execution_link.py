"""The fill must attach to the expiry that was ACTUALLY traded.

1 Sep 2026, the first real two-leg strangle: SPY went on at 758/780 (the
MONTHLY leg) and the execution was recorded against the 757/779 WEEKLY row,
which was never traded. place_paper() takes legs["monthly"]; record_execution()
took next(iter(legs)), and that dict is weekly-first.

Consequence had it stood: the grader would score strikes nobody traded, and the
row that actually filled would read as never placed. Both halves wrong, and
neither would have raised.

This is the repo's dominant bug shape — two components quietly disagreeing
about the same value. The fix is ONE definition, not two kept in step.
"""
from unittest.mock import patch

import tradepro_strategies.cli.index_strangle_paper as P


def _row():
    return {
        "market": "SPY", "as_of": "2026-09-01", "spot": 767.33,
        # A genuinely placeable row: paper-tradeable market, real session open,
        # not provisional. The guards refuse otherwise, and a refusal must ALSO
        # carry the expiry so it links to the row a fill would have used.
        "status": "CANDIDATE", "session_state": "open", "provisional": False,
        # Weekly FIRST, deliberately: this ordering is what the old code read.
        "legs": {"weekly":  {"dte": 7,  "put_strike": 757, "call_strike": 779},
                 "monthly": {"dte": 21, "put_strike": 758, "call_strike": 780}},
    }


def test_the_placement_reports_the_expiry_it_traded():
    sent = {}

    class R:
        status_code = 200
        content = b"{}"
        def json(self): return {"ok": True, "put": {"orderId": "1"},
                                "call": {"orderId": "2"}}

    def fake_post(url, json=None, **kw):
        sent["body"] = json
        return R()

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", fake_post):
            res = P.place_paper(_row(), contracts=1, shadow=True)

    assert res["expiry_kind"] == "monthly"
    # And the strikes sent are the MONTHLY ones, not the weekly.
    assert sent["body"]["putStrike"] == 758
    assert sent["body"]["callStrike"] == 780


def test_the_execution_links_to_the_traded_expiry_not_the_first_leg():
    res = {"placed": True, "partial": False, "shadow": True,
           "expiry_kind": "monthly",
           "response": {"put": {"orderId": "875165505"},
                        "call": {"orderId": "875165506"}}}
    sent = {}

    class R:
        status_code = 200
        ok = True
        text = ""
        def raise_for_status(self): pass

    def fake_post(url, json=None, **kw):
        sent["body"] = json
        return R()

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", fake_post):
            P.record_execution(_row(), res)

    # NOT "weekly", which is what next(iter(legs)) yields.
    assert sent["body"]["expiryKind"] == "monthly"
    assert sent["body"]["brokerOrderIds"] == "875165505,875165506"
    assert sent["body"]["placed"] is True
    assert sent["body"]["shadow"] is True


def test_a_result_without_the_kind_falls_back_to_the_named_constant():
    # Never back to next(iter(legs)) — the failure mode being fixed.
    res = {"placed": False, "partial": False, "shadow": False, "response": {}}
    sent = {}

    class R:
        status_code = 200
        ok = True
        text = ""
        def raise_for_status(self): pass

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post",
                          lambda url, json=None, **kw: (sent.update(body=json), R())[1]):
            P.record_execution(_row(), res)

    assert sent["body"]["expiryKind"] == P.PLACE_EXPIRY_KIND == "monthly"


def test_place_paper_and_record_execution_read_the_SAME_definition():
    # The structural guarantee: one constant, not two values kept in step.
    import inspect
    src_place = inspect.getsource(P.place_paper)
    src_rec = inspect.getsource(P.record_execution)
    assert "PLACE_EXPIRY_KIND" in src_place
    assert "PLACE_EXPIRY_KIND" in src_rec
    assert "next(iter(legs)" not in src_rec


# ---------------------------------------------------------------------------
# NO PLACEMENT OUTCOME MAY BE SILENT. This site has failed twice.
#
# 31 Aug: the report branch read `elif r["status"] == "CANDIDATE"`, so a failed
# SHADOW placement matched nothing — three markets attempted, all three failed,
# run printed nothing.
#
# 1 Sep: "fixed" to `elif res.get("reason")` — but the API-rejection path
# returned no `reason` key, so SPY, QQQ and GOLD failed silently AGAIN in the
# SCHEDULED run while the log read clean.
#
# Twice is a pattern: any condition can be missed by a return shape nobody
# anticipated. The guarantee has to be structural.
# ---------------------------------------------------------------------------

def test_an_api_rejection_carries_a_reason():
    class R:
        status_code = 502
        content = b"{}"
        def json(self):
            # Exactly the shape that printed nothing: ok false, partial false,
            # and historically no reason.
            return {"ok": False, "partial": False,
                    "put": {"status": "REJECTED", "reason": "no permission"},
                    "call": {"status": "REJECTED"}}

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: R()):
            res = P.place_paper(_row(), contracts=1, shadow=True)

    assert res["placed"] is False
    assert res["partial"] is False
    assert res["reason"], "a rejection with no reason is how this failed twice"
    assert "REJECTED" in res["reason"] or "permission" in res["reason"]


def test_the_report_branch_is_an_else_not_a_condition():
    # The structural guarantee. A condition here can always be missed by a
    # return shape nobody thought about; an else cannot.
    # Checks CODE, not prose. The comments below deliberately quote the old
    # broken line, and an earlier version of this very test matched its own
    # explanation — the same way StrangleOrderGuardTest spent a day asserting
    # against a comment. Strip comments first.
    import inspect, re
    src = inspect.getsource(P.main)
    block = src[src.index("if args.place:"):]
    code = "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("#"))

    assert re.search(r"^\s+else:\s*$", code, re.M), \
        "the final placement branch must be an unconditional else"
    assert not re.search(r'^\s*elif res\.get\("reason"\)', code, re.M), \
        "a condition here can be missed by an unanticipated return shape"
    assert "placement returned nothing" in code, "a None result must report too"


# ---------------------------------------------------------------------------
# THE FILL PRICE. Owner, 4 Sep 2026: "i cant see what price".
#
# credit_actual — the column built to hold what the broker ACTUALLY gave us —
# was never written. record_execution recorded that an order was placed and its
# ids, and nothing about the price. IBKR's Web API returns avgPrice NULL on the
# order, so the position's averagePricePaid is the ONLY place a fill price
# exists, and it disappears the moment the position closes.
#
# Every published figure for this strategy is Black-Scholes off a volatility
# index. The traded credit is the one input no backtest can manufacture, and it
# was being thrown away daily.
# ---------------------------------------------------------------------------

def test_the_placement_records_the_credit_it_was_filled_at():
    import inspect
    src = inspect.getsource(P.record_execution)
    assert "creditActual" in src
    assert "_credit_from_broker" in src


def test_the_credit_is_money_not_per_share():
    # The close job stored 0.32 for a $32 trade by summing per-share prices.
    # This must not repeat: the multiplier is READ, never assumed.
    import inspect
    src = inspect.getsource(P._credit_from_broker)
    assert 'p.get("multiplier")' in src
    assert "* mult" in src


def test_only_SHORT_option_legs_count_toward_the_credit():
    # A long leg, or a stock position, is not part of the credit. Counting one
    # would inflate the very number this exists to keep honest.
    import inspect
    src = inspect.getsource(P._credit_from_broker)
    assert 'p.get("isOption")' in src
    assert 'float(p.get("quantity") or 0) >= 0' in src


def test_strikes_are_matched_via_the_occ_symbol():
    assert P._occ_strike("SPX    SEP2026 7545 P [SPXW  260918P07545000 100]") == 7545.0
    assert P._occ_strike("SPY    SEP2026 758 P [SPY   260918P00758000 100]") == 758.0
    assert P._occ_strike("nothing here") is None


def test_the_placement_keys_on_the_TRADED_session():
    # It sent as_of (the settled session the GATE read) while the endpoint keys
    # on COALESCE(exchange_date, as_of). Every placement 404'd while exits
    # linked fine — placed=None beside a perfectly good realised_pnl.
    import inspect
    src = inspect.getsource(P.record_execution)
    assert 'row.get("exchange_date") or row.get("as_of")' in src


def test_a_failed_price_read_never_costs_the_link():
    # A missing price is a worse row; a lost link is a lost row.
    import inspect
    src = inspect.getsource(P.record_execution)
    assert "could not read the filled credit" in src


# ---------------------------------------------------------------------------
# WHY IT DID NOT PLACE, on the row.
#
# Owner, 5 Sep 2026: "yes but placeemnt fails then we need to see failure
# reason". It existed only in a Lambda log he cannot read, so on screen a
# REFUSED placement was indistinguishable from one never attempted.
#
# In the first week of live running the failures were the MAJORITY of the
# record — resolution on SPY/QQQ/GOLD, margin on NDX, a cancelled SPX — and
# none of it reached the person deciding whether to trust the desk.
# ---------------------------------------------------------------------------

def test_a_refusal_records_its_reason():
    res = {"placed": False, "partial": False, "shadow": True,
           "expiry_kind": "monthly", "response": {},
           "reason": 'put=REJECTED/"SELL 1 NDX (NDXP) SEP 18 26 28500 Put"'}
    sent = {}

    class R:
        status_code = 200; ok = True; text = ""
        def raise_for_status(self): pass

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post",
                          lambda url, json=None, **kw: (sent.update(body=json), R())[1]):
            P.record_execution(_row(), res)

    assert sent["body"]["placeError"].startswith("put=REJECTED")
    assert sent["body"]["placed"] is False


def test_a_SUCCESS_carries_no_error():
    res = {"placed": True, "partial": False, "shadow": False,
           "expiry_kind": "monthly", "reason": None,
           "response": {"put": {"orderId": "1"}, "call": {"orderId": "2"}}}
    sent = {}

    class R:
        status_code = 200; ok = True; text = ""
        def raise_for_status(self): pass

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post",
                          lambda url, json=None, **kw: (sent.update(body=json), R())[1]):
            P.record_execution(_row(), res)

    assert sent["body"]["placeError"] is None


def test_a_very_long_reason_is_truncated_not_dropped():
    res = {"placed": False, "partial": False, "shadow": False,
           "expiry_kind": "monthly", "response": {}, "reason": "x" * 5000}
    sent = {}

    class R:
        status_code = 200; ok = True; text = ""
        def raise_for_status(self): pass

    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post",
                          lambda url, json=None, **kw: (sent.update(body=json), R())[1]):
            P.record_execution(_row(), res)

    # A reason too long to store is still worth storing the FRONT of.
    assert len(sent["body"]["placeError"]) == 400
