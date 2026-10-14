"""A resolve failure must say WHICH leg, and what was asked for.

THE COST OF NOT DOING THIS. From 5 to 14 Oct 2026 the strangle recorded, every
session:

    could not resolve one or both contracts — NOTHING was placed

Eight sessions, and the line named neither the leg, the expiry, the strikes nor
the symbol. Diagnosing it needed the US market open and a hand-driven probe
against a read-only endpoint. The answer — both legs failing because
`_monthly_expiry(21)` had rolled the unit onto a November chain XSP cannot
resolve — was in the API's own response the whole time, as putResolved and
callResolved.

Those two fields sit at the TOP LEVEL of the resolve-stage response, not inside
out["put"]/out["call"], which is why the existing `legs_said` assembly (built
for broker rejections, where they ARE nested) never saw them.
"""
from __future__ import annotations

import tradepro_strategies.cli.index_strangle_paper as P


class _Resp:
    status_code = 502
    content = b"{}"
    def __init__(self, payload): self._p = payload
    def json(self): return self._p


def _place(monkeypatch, payload):
    monkeypatch.setattr(P, "_entry_min_minutes", lambda: 0)
    monkeypatch.setattr(P, "_minutes_since_open", lambda cfg: 999)
    monkeypatch.setattr(P, "_place_units", lambda: None)   # allow any unit
    monkeypatch.setattr(P, "_monthly_expiry", lambda dte, today=None: "2026-11-20")
    import tradepro_strategies.cli.push_to_api as api
    monkeypatch.setattr(api, "load_credentials", lambda: ("http://x", "t"))
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(payload))
    row = {"market": "XSP", "status": "CANDIDATE", "provisional": False,
           "session_state": "open",
           "legs": {"monthly": {"dte": 21, "put_strike": 768, "call_strike": 791}}}
    return P.place_paper(row, kind="monthly")


def test_resolve_failure_records_which_leg_and_what_was_asked(monkeypatch):
    res = _place(monkeypatch, {
        "ok": False, "stage": "resolve",
        "error": "could not resolve one or both contracts — NOTHING was placed",
        "putResolved": False, "callResolved": False,
    })
    r = res["reason"]
    assert "could not resolve" in r              # the API's own words stay first
    assert "put=False" in r and "call=False" in r, r
    assert "2026-11-20" in r, "the EXPIRY is the thing that actually changed"
    assert "768/791" in r, r
    assert "XSP" in r and "IND" in r, r


def test_a_one_legged_failure_says_WHICH_one(monkeypatch):
    """put OK / call missing is a different bug from both missing, and the
    record must tell them apart — one is a strike that does not exist, the
    other an expiry or symbol that does not."""
    res = _place(monkeypatch, {
        "ok": False, "stage": "resolve", "error": "could not resolve",
        "putResolved": True, "callResolved": False,
    })
    assert "put=True" in res["reason"] and "call=False" in res["reason"]


def test_a_BROKER_rejection_is_untouched(monkeypatch):
    """The detail is appended only at the resolve stage. A broker rejection
    already carries IBKR's words nested per leg, and must not be clipped or
    decorated — 'We are unable to acc' cost a run once already."""
    res = _place(monkeypatch, {
        "ok": False, "stage": "place",
        "put": {"status": "REJECTED", "reason": "We are unable to accept this order"},
        "call": {"status": "FILLED"},
    })
    assert "put=False" not in res["reason"]
    assert "unable to accept this order" in res["reason"]
