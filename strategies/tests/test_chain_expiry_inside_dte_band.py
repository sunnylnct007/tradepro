"""The screen must not pick a contract it is about to reject on DTE.

THE BUG (24 Sep 2026). The board showed 82 wheel candidates and ZERO eligible.
75 of the 82 died on one gate: "DTE band 23 vs 25-50". All 75 carried the same
contract — the 17 Oct monthly — because the month was chosen as "nearest to a
35-day target" with no reference to the band the row would then be judged by.

The arithmetic underneath is the real defect, and it is not a matter of taste:

    consecutive monthlies are 28-35 calendar days apart
    the band was 50 - 25 = 25 days wide

A window narrower than the gap between monthlies CANNOT always contain one. On
24 Sep the listed monthlies were 17 Oct (23 DTE) and 20 Nov (57 DTE) and
neither was admissible, so the screen went blind on the calendar rather than on
the market. That recurs for roughly eight days of every cycle.

Two changes, and neither works alone: the band widened to 25-60 (dte_min + 35,
the smallest max that always reaches a monthly), and selection made band-aware
so there is something admissible to choose. Widening alone changes nothing,
because the month is picked before the band is consulted.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from tradepro_strategies.quant_engine.options import chains_g3
from tradepro_strategies.quant_engine.options.risk import OptionsRiskConfig


def _resp(payload):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload
    return R()


def _run(months, legs, *, today, **kw):
    """Drive fetch_chain_g3 at a FIXED date — the bug is a calendar bug."""
    seen = []

    class _FixedDate(dt.date):
        @classmethod
        def today(cls):
            return today

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append({"url": url, "params": params or {}})
        if url.endswith("/months"):
            return _resp({"months": list(months)})
        return _resp({"spot": 100.0, "legs": legs})

    with patch.object(chains_g3.requests, "get", side_effect=fake_get), \
         patch.object(chains_g3._dt, "date", _FixedDate), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "tok")):
        out = chains_g3.fetch_chain_g3("XOM", target_dte=35, right="P", **kw)
    return out, seen


def _legs(maturity, n=20, oi=500):
    return [{"strike": 90 + i, "right": "P", "bid": 1.0, "ask": 1.2,
             "openInterest": oi, "maturityDate": maturity} for i in range(n)]


# 24 Sep 2026: OCT monthly = 17 Oct (23 DTE), NOV monthly = 20 Nov (57 DTE).
_THE_DAY = dt.date(2026, 9, 24)


def test_the_band_is_wider_than_the_gap_between_monthlies():
    """The property that makes a monthly always reachable. 28-35 days apart."""
    cfg = OptionsRiskConfig()
    assert cfg.dte_max - cfg.dte_min >= 35, (
        f"band {cfg.dte_min}-{cfg.dte_max} is "
        f"{cfg.dte_max - cfg.dte_min} days wide; monthlies are up to 35 days "
        "apart, so this band cannot always contain one")


def test_picks_the_in_band_month_not_the_nearest_to_target():
    """24 Sep, the live case. Oct is nearer 35 DTE; only Nov is admissible."""
    _out, seen = _run(("OCT26", "NOV26"), _legs("20261120"),
                      today=_THE_DAY, dte_min=25, dte_max=60)
    asked = [s["params"] for s in seen if not s["url"].endswith("/months")]
    assert asked, "no chain request was made"
    # NOV26's monthly is 20 Nov — the only listed month inside 25-60 DTE.
    assert asked[0].get("expiry") == "20261120", (
        f"selected {asked[0].get('expiry')}; expected the 20 Nov monthly, the "
        "only listed month inside the 25-60 DTE band")


def test_without_a_band_the_old_behaviour_is_unchanged():
    """No band supplied -> nearest to target, exactly as before. No surprises
    for the strangle and short-tier callers that pass their own expiry."""
    _out, seen = _run(("OCT26", "NOV26"), _legs("20261016"), today=_THE_DAY)
    asked = [s["params"] for s in seen if not s["url"].endswith("/months")]
    assert asked[0].get("expiry") == "20261016", (
        "with no band the nearest-to-target month (Oct, 23 DTE) must still win")


def test_falls_back_to_nearest_when_no_month_is_admissible():
    """A gate may still fail — but on the CALENDAR, not on our selection order.
    With only October listed, nothing sits in 25-60 and we must still return a
    chain so the row can state its real reason."""
    out, seen = _run(("OCT26",), _legs("20261016"),
                     today=_THE_DAY, dte_min=25, dte_max=60)
    asked = [s["params"] for s in seen if not s["url"].endswith("/months")]
    assert asked[0].get("expiry") == "20261016"
    assert out is not None, "must return the chain so the gate can explain itself"


def test_an_unusable_in_band_month_falls_back_rather_than_returning_nothing():
    """THE REGRESSION MY OWN FIX INTRODUCED, caught against the live feed.

    Band-aware selection correctly steered XOM off the 22-DTE October monthly
    and onto the 57-DTE November one — which had ZERO open interest on every
    leg and every quote null. The screen went from "a usable chain the DTE gate
    rejects" to "no chain at all", which is strictly worse: a blocked row at
    least states a reason. An in-band contract that does not trade is not a
    better answer than an out-of-band one that does.
    """
    calls = []

    class _FixedDate(dt.date):
        @classmethod
        def today(cls):
            return _THE_DAY

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/months"):
            return _resp({"months": ["OCT26", "NOV26"]})
        exp = (params or {}).get("expiry")
        calls.append(exp)
        # November (in band) is listed but dead — no legs at all.
        if exp == "20261120":
            return _resp({"spot": 100.0, "legs": []})
        return _resp({"spot": 100.0, "legs": _legs("20261016")})

    with patch.object(chains_g3.requests, "get", side_effect=fake_get), \
         patch.object(chains_g3._dt, "date", _FixedDate), \
         patch("tradepro_strategies.cli.push_to_api.load_credentials",
               return_value=("http://api", "tok")):
        out = chains_g3.fetch_chain_g3("XOM", target_dte=35, right="P",
                                       dte_min=25, dte_max=60)

    assert "20261120" in calls, "the in-band month must be TRIED first"
    assert out is not None, (
        "band preference must degrade to a tradeable contract, not to None")
    assert out.expiry == "2026-10-16", (
        f"fell back to {out.expiry}; expected the liquid October monthly")


def test_leg_level_expiry_also_respects_the_band():
    """A month holds several weeklies; the same rule applies one level down."""
    legs = _legs("20261016", n=10) + _legs("20261030", n=10)
    _out, _seen = _run(("OCT26",), legs, today=_THE_DAY,
                       dte_min=25, dte_max=60, prefer_monthly=False)
    # 17 Oct = 23 DTE (outside), 30 Oct = 36 DTE (inside and nearest 35).
    assert _out is not None and _out.dte == 36, (
        f"picked {_out.dte if _out else None} DTE; the 30 Oct legs at 36 DTE "
        "are the only ones inside the band")
