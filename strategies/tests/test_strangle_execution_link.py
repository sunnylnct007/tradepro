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
