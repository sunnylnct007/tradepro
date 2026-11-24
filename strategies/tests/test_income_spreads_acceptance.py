"""INCOME_PUT_SPREADS_V1 §9 acceptance tests, replayed from §10 without a
broker. Golden example: ABBV Jan15'27 270/260 on the 23 Nov 2026 quotes."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradepro_strategies import income_spreads as I

TODAY = date(2026, 11, 23)
S = dict(I.DEFAULTS)
FX = 1 / 1.31  # GBP per USD

# §10 — ABBV, 23 Nov 2026 quotes
ABBV_TECH = {"close": 289.33, "as_of": "2026-11-20", "sma200": 242.44, "above_200dma": True,
             "cloud_top": 260.0, "above_cloud": True, "rsi14": 49.2, "bb_pct_b": 0.23,
             "ret_10d": -0.04, "swing_low_30d": 274.39, "hv_30d": 0.231}
ABBV_PUTS = [  # strike, bid, ask, |delta|, iv, OI  (270/260 from §10; neighbours plausible)
    {"strike": 280, "bid": 6.30, "ask": 6.80, "delta": -0.36, "iv": 0.27, "open_interest": 900},
    {"strike": 275, "bid": 5.10, "ask": 5.60, "delta": -0.31, "iv": 0.275, "open_interest": 700},
    {"strike": 270, "bid": 4.10, "ask": 4.55, "delta": -0.26, "iv": 0.278, "open_interest": 1431},
    {"strike": 265, "bid": 3.10, "ask": 3.55, "delta": -0.21, "iv": 0.28, "open_interest": 600},
    {"strike": 260, "bid": 2.20, "ask": 2.75, "delta": -0.17, "iv": 0.285, "open_interest": 1290},
    {"strike": 255, "bid": 1.60, "ask": 2.05, "delta": -0.13, "iv": 0.29, "open_interest": 300},
]
# NOTE for the owner: on 23 Nov 2026 the DEC monthly (18 Dec) is EXACTLY 25 DTE,
# inside §2's [25, 60], and §4.2 prefers the nearer — so the engine as specified
# would propose Dec 18, not the Jan 15 of the golden example. The golden replay
# below lists the months as the chain did WITHOUT Dec 18 so it tests the strike,
# pricing, probability and sizing stages; the boundary itself is pinned in
# test_monthly_expiry_window_as_specified. Resolve by setting dte_min > 25.
EXPIRIES = ["2027-01-15", "2027-02-19", "2027-03-19"]


def _abbv(**over):
    kw = dict(bucket="single", sector="Health Care", spot=289.33, tech=ABBV_TECH, expiries=EXPIRIES,
              puts_for=lambda e: ABBV_PUTS if e == "2027-01-15" else [], next_earnings="2027-02-03",
              earnings_basis="scheduled", held=set(), sector_open_gbp=0.0, total_open_gbp=0.0,
              fx_gbp_per_usd=FX, s=S, today=TODAY)
    kw.update(over)
    return I.evaluate("ABBV", **kw)


def test_golden_replay_abbv_jan_270_260_four_contracts():
    r = _abbv()
    assert r["rejected"] == [], r["rejected"]
    assert r["id"] == "ABBV-20270115-270-260"
    assert r["expiry"] == "2027-01-15" and r["dte"] == 53
    assert (r["short_strike"], r["long_strike"], r["width"]) == (270.0, 260.0, 10.0)
    assert r["credit_mid"] == pytest.approx(1.85, abs=0.005)
    assert r["credit_natural"] == pytest.approx(1.35, abs=0.005)
    assert r["contracts"] == 4
    assert r["max_loss_usd"] == pytest.approx(3260, abs=1)
    assert r["income_usd"] == pytest.approx(740, abs=1)
    assert r["p_full_profit_iv"] == pytest.approx(0.73, abs=0.02)
    assert r["p_max_loss_iv"] == pytest.approx(0.17, abs=0.03)
    assert r["p_full_profit_hv"] == pytest.approx(0.77, abs=0.02)
    assert r["exit_rule"].endswith("< 274.39")
    assert r["take_profit_at"] == pytest.approx(0.93, abs=0.01)
    assert r["breakeven"] == pytest.approx(268.15, abs=0.01)
    assert r["iv_hv_ratio"] == pytest.approx(1.20, abs=0.01)
    assert 0 < r["score"] <= 1


def test_earnings_inside_expiry_rejects_acn():
    r = I.evaluate("ACN", bucket="single", sector="Information Technology", spot=300.0, tech=ABBV_TECH,
                   expiries=EXPIRIES, puts_for=lambda e: ABBV_PUTS, next_earnings="2026-12-17",
                   earnings_basis="scheduled", held=set(), sector_open_gbp=0.0, total_open_gbp=0.0,
                   fx_gbp_per_usd=FX, s=S, today=TODAY)
    assert any(x.startswith("earnings_inside_expiry") for x in r["rejected"])


def test_unknown_earnings_is_a_rejection_not_a_pass():
    r = _abbv(next_earnings=None, earnings_basis="no report date held")
    assert any(x.startswith("earnings_unknown") for x in r["rejected"])


def test_sector_cap_sizes_jnj_within_the_healthcare_room_or_rejects():
    # one Health Care spread open at £2,489 → sector room is
    # 6,000 − 2,489 = 3,511 > 3,000, so the per-trade cap binds; with £5,000 open the sector binds.
    r = _abbv(sector_open_gbp=2489.0)
    assert r["contracts"] == 4 and r["binding_cap"] == "per_trade"
    r2 = _abbv(sector_open_gbp=5000.0)
    assert r2["binding_cap"] == "sector" and r2["max_loss_gbp"] <= 1000.0 + 1
    r3 = _abbv(sector_open_gbp=6000.0)
    assert any(x.startswith("sector_cap") for x in r3["rejected"])


def test_total_phase1_cap_rejects_when_full():
    r = _abbv(total_open_gbp=6000.0)
    assert any(x.startswith("total_cap") for x in r["rejected"])


def test_falling_knife_rejected():
    tech = dict(ABBV_TECH, close=200.0, sma200=242.44, above_200dma=False, ret_10d=-0.17)
    r = _abbv(tech=tech, spot=200.0)
    assert any(x.startswith("falling_knife") for x in r["rejected"])
    assert any(x.startswith("below_200dma") for x in r["rejected"])


def test_already_held_googl_rejected():
    r = I.evaluate("GOOGL", bucket="single", sector="Communication Services", spot=283.0, tech=ABBV_TECH,
                   expiries=EXPIRIES, puts_for=lambda e: ABBV_PUTS, next_earnings="2027-02-03",
                   earnings_basis="scheduled", held={"GOOGL"}, sector_open_gbp=0.0, total_open_gbp=0.0,
                   fx_gbp_per_usd=FX, s=S, today=TODAY)
    assert "already_held" in r["rejected"]


def test_stale_quote_rejects_and_no_instruction_hint():
    stale = [dict(p, bid=None, ask=None) for p in ABBV_PUTS]
    r = _abbv(puts_for=lambda e: stale)
    assert any(x.startswith("stale_quote") or x.startswith("no_strike") for x in r["rejected"])
    assert "instruction_hint" not in r
    # one leg stale (the long)
    half = [dict(p, bid=None) if p["strike"] == 260 else p for p in ABBV_PUTS]
    r2 = _abbv(puts_for=lambda e: half)
    assert any("stale_quote" in x for x in r2["rejected"])


def test_illiquid_leg_and_low_oi_reject():
    wide = [dict(p, ask=p["bid"] * 1.4) if p["strike"] == 270 else p for p in ABBV_PUTS]
    assert any(x.startswith("illiquid_leg") for x in _abbv(puts_for=lambda e: wide)["rejected"])
    thin = [dict(p, open_interest=50) if p["strike"] == 270 else p for p in ABBV_PUTS]
    assert any(x.startswith("low_oi") for x in _abbv(puts_for=lambda e: thin)["rejected"])
    unknown = [dict(p, open_interest=None) if p["strike"] == 270 else p for p in ABBV_PUTS]
    assert any(x.startswith("oi_unavailable") for x in _abbv(puts_for=lambda e: unknown)["rejected"])


def test_monthly_expiry_window_as_specified():
    # Dec 24 is a weekly (ignored); Dec 18 is 25 DTE (inside the window as written) and nearer than Jan 15.
    e, _ = I.pick_expiry(["2026-12-24", "2026-12-18", "2027-01-15", "2027-02-19"], TODAY, S)
    assert e == "2026-12-18"
    e2, _ = I.pick_expiry(["2026-12-24", "2027-01-15"], TODAY, S)
    assert e2 == "2027-01-15"
    e3, why3 = I.pick_expiry(["2027-03-19"], TODAY, S)
    assert e3 is None and "no monthly expiry" in why3
    tight = dict(S, dte_min=26)
    e4, _ = I.pick_expiry(["2026-12-18", "2027-01-15"], TODAY, tight)
    assert e4 == "2027-01-15"


def test_short_strike_must_sit_below_the_closing_low_with_delta_in_range():
    legs, why = I.pick_strikes(ABBV_PUTS, 274.39, S)
    assert legs["short_strike"] == 270.0 and legs["long_strike"] == 260.0, why
    legs2, why2 = I.pick_strikes(ABBV_PUTS, 259.0, S)  # only 255 is below; Δ 0.13 out of range
    assert legs2 is None and "delta" in why2


def test_instruction_direction_is_sell_the_combo():
    r = _abbv()
    assert r["instruction_hint"].startswith("SELL 4 × ABBV 2027-01-15 270P / 260P combo")


def test_monitor_exit_alert_has_a_stable_key_so_it_is_sent_once():
    t = {"id": "ABBV-20270115-270-260", "symbol": "ABBV", "expiry": "2027-01-15", "short_strike": 270, "long_strike": 260,
         "contracts": 4, "credit_per_spread": 1.85, "swing_low_at_entry": 274.39, "status": "open"}
    a = I.monitor_trade(t, 273.10, "2026-12-01", 2.40, 2.70, S, date(2026, 12, 1))
    b = I.monitor_trade(t, 273.10, "2026-12-01", 2.40, 2.70, S, date(2026, 12, 1))
    kinds = [x["kind"] for x in a["alerts"]]
    assert kinds == ["EXIT_NEXT_SESSION"]
    assert a["alerts"][0]["key"] == b["alerts"][0]["key"]
    ok = I.monitor_trade(t, 280.0, "2026-12-01", 0.90, 1.00, S, date(2026, 12, 1))
    assert [x["kind"] for x in ok["alerts"]] == ["TAKE_PROFIT"]
    gamma = I.monitor_trade(t, 271.0, "2027-01-11", 3.0, 3.2, S, date(2027, 1, 11))
    assert "GAMMA_RISK" in [x["kind"] for x in gamma["alerts"]]


def test_record_summary_counts_income_only_and_reports_vs_target():
    rec = {"trades": [{"symbol": "ABBV", "role": "income", "status": "open", "credit_usd": 740, "max_loss_usd": 3260, "opened": "2026-11-23"},
                      {"symbol": "QQQ", "role": "hedge", "status": "open", "max_loss_usd": 1921}]}
    r = I.record_summary(rec, S, FX)
    assert r["n_open"] == 1 and r["open_max_loss_usd"] == 3260 and r["target_gbp_per_month"] == 833
    assert r["verdict"].startswith("too few")


def test_technicals_from_bars_use_the_closing_low():
    idx = pd.bdate_range("2025-10-01", periods=260)
    close = pd.Series([200 + i * 0.4 for i in range(260)], index=idx)
    df = pd.DataFrame({"open": close, "high": close + 2, "low": close - 5, "close": close})
    t = I.technicals(df)
    assert t["above_200dma"] and t["above_cloud"]
    assert t["swing_low_30d"] == pytest.approx(float(close.tail(30).min()), abs=0.01)  # closes, not lows
    assert t["hv_30d"] is not None


def test_cash_secured_alternative_is_priced_beside_the_spread_and_sized_by_cash():
    r = _abbv()
    k = r["csp"]
    assert k["structure"] == "cash_secured_put" and k["short_strike"] == 270.0
    assert k["credit_mid"] == pytest.approx(4.325, abs=0.01)       # the whole short-leg mid, no long leg
    assert k["notional_per_contract_usd"] == 27000
    # £25k per name at 1.31 → 32,750 $ → 1 contract of 27,000 $ notional
    assert k["contracts"] == 1 and k["cash_required_usd"] == 27000
    assert k["income_usd"] == pytest.approx(432.5, abs=1) and k["max_loss_usd"] == pytest.approx(26567.5, abs=1)
    assert k["eligible"] is False and k["why"].startswith("low_pop")   # 73% < the 75% CSP floor on IV
    relaxed = dict(S, csp_min_pop_full_credit=0.70)
    r2 = _abbv(s=relaxed)
    assert r2["csp"]["eligible"] is True and r2["csp"]["instruction_hint"].startswith("SELL 1 × ABBV 270P cash-secured")


def test_cash_secured_respects_the_total_notional_cap_and_can_be_switched_off():
    r = _abbv(csp_open_notional_gbp=40_000.0)                    # £10k room left → 13,100 $ → 0 contracts at 27,000 $
    assert r["csp"]["eligible"] is False and "total_notional_cap" in r["csp"]["why"]
    off = dict(S, csp_enabled=False)
    assert "csp" not in _abbv(s=off)
