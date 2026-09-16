"""EACH EXPIRY PRICES ITSELF — the defect that mis-sized the funding figure.

`economics()` was documented "per ONE weekly contract" and hardcoded
`legs["weekly"]` and `7/365`. `push_decisions()` computed that ONE block and
stamped it onto EVERY expiry row, so the monthly row carried dte 21 beside a
credit priced at 7 DTE.

Measured live on XSP, 15 Sep 2026 — both rows reported credit_modelled 546:

    leg              strikes      credit_modelled   actually received
    weekly  (7 DTE)  750 / 774          546              245.56
    monthly (21 DTE) 751 / 776          546            1,364.56

The file's own DTE table puts 7-DTE and 21-DTE credits ~2.2x apart, so a single
number for both cannot be right in either place. It mattered because
FUNDING_GATES_V1 S3 grades credit RECEIVED against credit MODELLED, and S5
sizes the account off credit — the funding figure was being derived from the
mislabelled field.

These tests fail on the old code and pass on the new.
"""
import tradepro_strategies.cli.index_strangle_paper as P

# The real XSP row from 15 Sep 2026, strikes and spot as recorded.
ROW = {
    "market": "XSP", "spot": 761.23, "iv_used": 17.1, "lot": 100,
    "legs": {
        "weekly":  {"dte": 7,  "put_strike": 750, "call_strike": 774},
        "monthly": {"dte": 21, "put_strike": 751, "call_strike": 776},
    },
}
EV = {"historical": {"mean_pct": 0.05, "best_pct": 0.4, "worst_pct": -1.05},
      "stress": {"worst_ungated_pct": -3.2}}


def test_a_longer_expiry_earns_a_bigger_credit():
    """The whole point: 21 DTE must not price at 7 DTE."""
    wk = P.economics(ROW, EV, "weekly")
    mo = P.economics(ROW, EV, "monthly")
    assert wk and mo
    assert mo["credit_modelled"] > wk["credit_modelled"], (
        f"monthly {mo['credit_modelled']} must exceed weekly "
        f"{wk['credit_modelled']} — equal means the expiry was ignored")
    # sqrt(21/7) = 1.73x on time value alone. Anything under 1.4x means the
    # tenor is barely reaching the pricer.
    assert mo["credit_modelled"] / wk["credit_modelled"] > 1.4


def test_each_block_says_which_contract_it_describes():
    """An anonymous block is how this went unnoticed for weeks."""
    for kind, dte in (("weekly", 7), ("monthly", 21)):
        e = P.economics(ROW, EV, kind)
        assert e["expiry_kind"] == kind
        assert e["dte"] == dte


def test_collateral_follows_the_legs_own_strike():
    """Collateral came off the weekly strike too — 750 vs 751 is small here
    and is NOT small on SPX."""
    assert P.economics(ROW, EV, "weekly")["collateral"] == 750 * 100
    assert P.economics(ROW, EV, "monthly")["collateral"] == 751 * 100


def test_the_default_is_still_weekly():
    """The email bodies say "one weekly contract" and must keep meaning it."""
    assert P.economics(ROW, EV) == P.economics(ROW, EV, "weekly")


def test_pushed_rows_carry_their_own_expirys_money():
    """End to end: the payload row for each expiry gets ITS credit, not the
    weekly's. This is the assertion that would have caught it live."""
    row = dict(ROW, status="CANDIDATE", as_of="2026-09-15",
               exchange_date="2026-09-15", vol_index=17.1, vol_threshold=13.5)
    row["economics_by_kind"] = {k: P.economics(ROW, EV, k)
                                for k in ("weekly", "monthly")}
    row["economics"] = row["economics_by_kind"]["weekly"]

    sent = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    def _fake_post(url, json=None, **kw):
        sent["payload"] = json
        return _Resp()

    import requests
    import tradepro_strategies.cli.push_to_api as api
    _post, _creds = requests.post, api.load_credentials
    requests.post = _fake_post
    api.load_credentials = lambda: ("http://x", "t")
    try:
        P.push_decisions([row])
    finally:
        requests.post, api.load_credentials = _post, _creds

    rows = sent.get("payload") or []
    by = {r["expiryKind"]: r for r in rows}
    assert set(by) == {"weekly", "monthly"}, by
    assert by["weekly"]["creditModelled"] != by["monthly"]["creditModelled"], (
        "both expiries were pushed with the same modelled credit — the exact "
        "defect measured on XSP 15 Sep")
    assert by["monthly"]["dte"] == 21
    assert by["weekly"]["dte"] == 7
