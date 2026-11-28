"""Options review item 12 (28 Nov 2026): two expiries, the index leg, the
shadow low-volatility rule, the owner's credit rule beside the engine's,
held-list freshness. Synthetic data only."""
from datetime import date

from tradepro_strategies import income_spreads as I

TODAY = date(2026, 11, 28)
EXPS = ["2026-12-18", "2027-01-15", "2027-02-19", "2027-03-19"]


def _s(**over):
    s = dict(I.DEFAULTS); s.update(over); return s


def test_two_monthlies_nearest_first():
    s = _s(dte_min=25, dte_max=50, expiries_to_try=2)
    got = I.pick_expiries(EXPS, TODAY, s)
    assert [e for e, _ in got] == ["2027-01-15", "2027-02-19"]
    assert "tried second" in got[1][1]
    assert [e for e, _ in I.pick_expiries(EXPS, TODAY, _s(dte_min=25, dte_max=50, expiries_to_try=1))] == ["2027-01-15"]


def _legs(credit_short, credit_long, delta=-0.2, oi=5000):
    return [{"strike": 95.0, "bid": credit_short - 0.05, "ask": credit_short + 0.05, "delta": delta, "iv": 0.3, "open_interest": oi},
            {"strike": 85.0, "bid": credit_long - 0.05, "ask": credit_long + 0.05, "delta": -0.08, "iv": 0.32, "open_interest": oi}]


def _tech():
    return {"close": 100.0, "above_200dma": True, "sma200": 90.0, "above_cloud": True, "cloud_top": 92.0,
            "swing_low_30d": 96.0, "swing_low_20d": 97.0, "rsi14": 55, "bb_pct_b": 0.5, "ret_10d": 0.01,
            "hv_30d": 0.25, "as_of": "2026-11-27"}


def test_the_second_expiry_is_used_when_the_first_has_thin_credit():
    s = _s(dte_min=25, dte_max=50, expiries_to_try=2, min_credit_to_width=0.15, min_pop_full_credit=0.0,
           min_open_interest_short_leg=1, max_leg_bid_ask_pct=0.5, spread_width_preference=[10])
    quotes = {"2027-01-15": _legs(1.0, 0.1), "2027-02-19": _legs(2.5, 0.5)}     # 9% then 20% of a 10 width
    row = I.evaluate("AAA", bucket="single", sector="x", spot=100.0, tech=_tech(), expiries=EXPS,
                     puts_for=lambda e: quotes[e], next_earnings="2027-06-01", earnings_basis="scheduled", held=set(),
                     sector_open_gbp=0.0, total_open_gbp=0.0, fx_gbp_per_usd=0.79, s=s, today=TODAY)
    assert row["expiry"] == "2027-02-19" and not row["rejected"]
    assert [t["expiry"] for t in row["expiries_tried"]] == ["2027-01-15", "2027-02-19"]
    assert any(x.startswith("thin_credit") for x in row["expiries_tried"][0]["rejected"])


def test_when_no_expiry_passes_the_specs_expiry_row_is_shown_with_both_attempts():
    s = _s(dte_min=25, dte_max=50, expiries_to_try=2, min_credit_to_width=0.15, min_open_interest_short_leg=1,
           max_leg_bid_ask_pct=0.5, spread_width_preference=[10])
    row = I.evaluate("AAA", bucket="single", sector="x", spot=100.0, tech=_tech(), expiries=EXPS,
                     puts_for=lambda e: _legs(1.0, 0.1), next_earnings="2027-06-01", earnings_basis="scheduled", held=set(),
                     sector_open_gbp=0.0, total_open_gbp=0.0, fx_gbp_per_usd=0.79, s=s, today=TODAY)
    assert row["expiry"] == "2027-01-15" and row["rejected"] and len(row["expiries_tried"]) == 2


def test_index_rows_carry_the_must_close_warning():
    s = _s(dte_min=25, dte_max=50, expiries_to_try=1, min_credit_to_width=0.15, min_pop_full_credit=0.0,
           min_open_interest_short_leg=1, max_leg_bid_ask_pct=0.5, spread_width_preference=[10])
    row = I.evaluate("IWM", bucket="index", sector="Index", spot=100.0, tech=_tech(), expiries=EXPS,
                     puts_for=lambda e: _legs(2.5, 0.5), next_earnings=None, earnings_basis="index", held=set(),
                     sector_open_gbp=0.0, total_open_gbp=0.0, fx_gbp_per_usd=0.79, s=s, today=TODAY)
    assert any(w.startswith("etf_option_must_close_before_expiry") for w in row["warnings"])


def test_low_vol_shadow_annotates_but_does_not_pass_and_on_uses_it():
    # 30-day low 96 but every strike at/below it has |Δ| < 0.15 → no_strike under the spec
    legs = [{"strike": 95.0, "bid": 0.9, "ask": 1.0, "delta": -0.12, "iv": 0.2, "open_interest": 5000},
            {"strike": 85.0, "bid": 0.2, "ask": 0.3, "delta": -0.05, "iv": 0.22, "open_interest": 5000}]
    base = dict(dte_min=25, dte_max=50, expiries_to_try=1, min_credit_to_width=0.05, min_pop_full_credit=0.0,
                min_open_interest_short_leg=1, max_leg_bid_ask_pct=0.5, spread_width_preference=[10],
                short_put_delta_range=[0.15, 0.30], low_vol_hv_threshold=0.30, low_vol_min_delta=0.10)
    tech = dict(_tech(), hv_30d=0.18)
    shadow = I.evaluate("JNJ", bucket="single", sector="x", spot=100.0, tech=tech, expiries=EXPS, puts_for=lambda e: legs,
                        next_earnings="2027-06-01", earnings_basis="scheduled", held=set(), sector_open_gbp=0.0,
                        total_open_gbp=0.0, fx_gbp_per_usd=0.79, s=_s(**base, low_vol_rule="shadow"), today=TODAY)
    assert any(x.startswith("no_strike") for x in shadow["rejected"])
    assert shadow["low_vol_rule"]["would_pick"] == {"short_strike": 95.0, "long_strike": 85.0}
    assert any(w.startswith("low_vol_rule_would_pass") for w in shadow["warnings"])
    on = I.evaluate("JNJ", bucket="single", sector="x", spot=100.0, tech=tech, expiries=EXPS, puts_for=lambda e: legs,
                    next_earnings="2027-06-01", earnings_basis="scheduled", held=set(), sector_open_gbp=0.0,
                    total_open_gbp=0.0, fx_gbp_per_usd=0.79, s=_s(**base, low_vol_rule="on"), today=TODAY)
    assert not on["rejected"] and on["short_strike"] == 95.0 and "low-vol rule" in on["strike_note"]


def test_owner_credit_rule_by_volatility_class():
    kv = {"credit_to_width_min": {"index_or_low_vol": 0.20, "volatile": 0.3333, "hv30_volatile_threshold_pct": 40}}
    assert I.owner_credit_rule(0.25, kv)[0] == 0.20 and I.owner_credit_rule(0.55, kv)[0] == 0.3333
    assert I.owner_credit_rule(0.55, kv, bucket="index")[0] == 0.20
    assert I.owner_credit_rule(0.25, {})[0] is None
    s = _s(dte_min=25, dte_max=50, expiries_to_try=1, min_credit_to_width=0.15, min_pop_full_credit=0.0,
           min_open_interest_short_leg=1, max_leg_bid_ask_pct=0.5, spread_width_preference=[10], _risk_guardrails=kv)
    row = I.evaluate("AAA", bucket="single", sector="x", spot=100.0, tech=dict(_tech(), hv_30d=0.55), expiries=EXPS,
                     puts_for=lambda e: _legs(2.5, 0.5), next_earnings="2027-06-01", earnings_basis="scheduled", held=set(),
                     sector_open_gbp=0.0, total_open_gbp=0.0, fx_gbp_per_usd=0.79, s=s, today=TODAY)
    assert row["credit_rule_owner"]["min_credit_to_width"] == 0.3333 and row["credit_rule_owner"]["pass"] is False
    assert any(w.startswith("below_owner_credit_rule") for w in row["warnings"]) and not row["rejected"]   # engine gate 15% still passes


def test_held_live_as_of_reads_the_statement_date(monkeypatch):
    import requests
    class _R:
        status_code = 200
        def json(self): return {"artifact": {"report_date": "2026-11-27", "as_of_utc": "2026-11-28T07:20:00+00:00"}}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    out = I.held_live_as_of("http://x", "t")
    assert out["report_date"] == "2026-11-27" and "end-of-day" in out["note"]
