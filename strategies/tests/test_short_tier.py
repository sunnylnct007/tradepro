"""TIER_SHORT (SPEC_OPTIONS_SHORTDATED_AND_EARNINGS_2026_08_12 §1) — the
earnings-avoidance short-dated CSP tier. Calibration anchor: MRVL Aug21'26
200P @ $2.90 (0.28Δ, 58.6%/yr, 9 DTE, clears the 27-Aug print by 6 sessions)
must be admissible; marginal trades must not be."""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.cli.options_screen import _short_tier_cfg
from tradepro_strategies.quant_engine.options.risk import (
    MarketContext, OptionsRiskConfig, PortfolioState, Regime, Structure,
    TradeCandidate, evaluate)


def _short_ctx(**kw):
    base = dict(
        regime=Regime("GREEN"), falling_knife=False, pct_off_52w_high=25.0,
        iv_rank=None, iv_hv_ratio=1.2, iv_rank_window_days=5,
        open_interest=800, bid_ask_spread_usd=0.20, premium_mid_usd=2.90,
        earnings_in_expiry_window=False, data_fresh=True, quotes_delayed=False)
    base.update(kw)
    return MarketContext(**base)


def _cfg():
    # Wide capital so the notional gate never interferes with tier gates.
    return _short_tier_cfg(OptionsRiskConfig(
        pot_gbp=30000, max_deploy_gbp=25000, per_position_gbp=25000))


def test_short_tier_abuts_the_standard_band_no_dead_zone():
    """The ORCL Sep04 case (13 Aug 2026): 22 DTE cleared its print by 3 days
    but sat in a 22-24 DTE hole between the spec's 7-21 short tier and the
    25-50 standard band — while a 9-DTE trade (MORE gamma) was admissible.
    The tiers must ABUT: short.dte_max == standard.dte_min - 1."""
    from tradepro_strategies.quant_engine.options.risk import OptionsRiskConfig
    base = OptionsRiskConfig()
    s = _cfg()
    assert s.dte_max == base.dte_min - 1 == 24
    assert s.dte_min == 7
    # no DTE between 7 and 50 is unreachable by both tiers
    for d in range(7, 51):
        assert (s.dte_min <= d <= s.dte_max) or (base.dte_min <= d <= base.dte_max), d


def test_short_tier_gate_values_match_spec():
    s = _cfg()
    assert s.dte_min == 7
    assert s.delta_max == 0.30
    assert s.min_ann_yield_pct == 25.0
    assert s.min_premium_usd == 0.50
    assert s.oi_min == 500
    assert s.spread_max_pct_of_mid == 0.12


def test_mrvl_worked_example_is_admissible():
    """SPEC §1.2: the tier is tuned so THIS trade clears every gate."""
    cand = TradeCandidate(symbol="MRVL", structure=Structure.CASH_SECURED_PUT,
                          abs_delta=0.28, dte=9, strike=200.0,
                          notional_gbp=round(200 * 100 / 1.27))
    d = evaluate(cand, _short_ctx(), PortfolioState(), _cfg())
    assert d.allowed, d.blocks
    # yield sanity: 2.90/200 * 365/9 = 58.8%/yr — comfortably over the 25 floor
    assert (2.90 / 200) * (365 / 9) * 100 > 25


def test_marginal_trades_are_not_admissible():
    cfg = _cfg()
    # thin premium: $0.40 < $0.50 floor
    d = evaluate(TradeCandidate("XX", Structure.CASH_SECURED_PUT, 0.25, 10, 50.0, 3937),
                 _short_ctx(premium_mid_usd=0.40), PortfolioState(), cfg)
    assert not d.allowed
    # hot delta: 0.33 > 0.30 cap
    d = evaluate(TradeCandidate("XX", Structure.CASH_SECURED_PUT, 0.33, 10, 200.0, 15748),
                 _short_ctx(), PortfolioState(), cfg)
    assert not d.allowed
    # thin book: OI 300 < 500
    d = evaluate(TradeCandidate("XX", Structure.CASH_SECURED_PUT, 0.25, 10, 200.0, 15748),
                 _short_ctx(open_interest=300), PortfolioState(), cfg)
    assert not d.allowed
    # too long for the tier: 23 DTE > 21
    d = evaluate(TradeCandidate("XX", Structure.CASH_SECURED_PUT, 0.25, 23, 200.0, 15748),
                 _short_ctx(), PortfolioState(), cfg)
    assert not d.allowed
    # yield floor: 2.90 on a 200 strike at 21 DTE = 25.2%/yr passes, but a
    # $1.40 premium (12.2%/yr) fails the 25 floor
    d = evaluate(TradeCandidate("XX", Structure.CASH_SECURED_PUT, 0.25, 21, 200.0, 15748),
                 _short_ctx(premium_mid_usd=1.40), PortfolioState(), cfg)
    assert not d.allowed


def test_clearing_sessions_rule():
    """Expiry must precede earnings by ≥ 3 XNYS sessions — the admissibility
    core. Aug21'26 (Fri) → 27-Aug print = 3 clear sessions (24,25,26)."""
    from tradepro_strategies.gates.earnings_proximity import sessions_between
    assert sessions_between(dt.date(2026, 8, 21), dt.date(2026, 8, 27)) >= 3
    # Aug26 expiry the day before the print — 1 session, NOT admissible
    assert sessions_between(dt.date(2026, 8, 26), dt.date(2026, 8, 27)) < 3
