"""Risk-engine contract tests — the options desk's non-negotiable spine.

Covers the happy path, every hard block, the no-false-positives (missing input →
block) rule, and the drawdown-brake tiers.
"""
from tradepro_strategies.quant_engine.options.risk import (
    MarketContext,
    OptionsRiskConfig,
    PortfolioState,
    Regime,
    Structure,
    TradeCandidate,
    brake_tier_for,
    evaluate,
)


def _good_csp() -> TradeCandidate:
    return TradeCandidate(
        symbol="KO", structure=Structure.CASH_SECURED_PUT,
        abs_delta=0.27, dte=35, strike=55.0, contracts=1, notional_gbp=4300.0,
    )


def _good_ctx() -> MarketContext:
    return MarketContext(
        regime=Regime.GREEN, falling_knife=False, iv_rank=45.0,
        open_interest=5000, bid_ask_spread_usd=0.05,
        earnings_in_expiry_window=False, ex_div_in_expiry_window=False, data_fresh=True,
    )


def _flat() -> PortfolioState:
    return PortfolioState(deployed_gbp=0.0, open_positions=0, cumulative_realised_loss_gbp=0.0)


def test_happy_path_allows():
    d = evaluate(_good_csp(), _good_ctx(), _flat())
    assert d.allowed, d.blocks
    assert d.blocks == []
    assert d.brake_tier == 0 and d.size_factor == 1.0


def test_regime_blocks_csp_in_red():
    ctx = MarketContext(**{**_good_ctx().__dict__, "regime": Regime.RED})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("not permitted in RED" in b for b in d.blocks)


def test_falling_knife_blocks_wheel_entry():
    ctx = MarketContext(**{**_good_ctx().__dict__, "falling_knife": True})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("FALLING-KNIFE" in b for b in d.blocks)


def test_low_iv_rank_blocks_short_premium():
    ctx = MarketContext(**{**_good_ctx().__dict__, "iv_rank": 20.0})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("IV-Rank" in b for b in d.blocks)


def test_delta_out_of_band_blocks():
    cand = TradeCandidate(**{**_good_csp().__dict__, "abs_delta": 0.45})
    d = evaluate(cand, _good_ctx(), _flat())
    assert not d.allowed
    assert any("delta" in b.lower() for b in d.blocks)


def test_dte_out_of_band_blocks():
    cand = TradeCandidate(**{**_good_csp().__dict__, "dte": 7})
    d = evaluate(cand, _good_ctx(), _flat())
    assert not d.allowed
    assert any("DTE" in b for b in d.blocks)


def test_illiquid_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "open_interest": 100})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("Open interest" in b for b in d.blocks)


def test_wide_spread_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "bid_ask_spread_usd": 0.40})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("Bid-ask" in b for b in d.blocks)


def test_earnings_in_window_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "earnings_in_expiry_window": True})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("Earnings" in b for b in d.blocks)


def test_per_position_limit_blocks():
    cand = TradeCandidate(**{**_good_csp().__dict__, "notional_gbp": 6000.0})
    d = evaluate(cand, _good_ctx(), _flat())
    assert not d.allowed
    assert any("per-position" in b for b in d.blocks)


def test_deployment_limit_blocks():
    pf = PortfolioState(deployed_gbp=8000.0, open_positions=1, cumulative_realised_loss_gbp=0.0)
    d = evaluate(_good_csp(), _good_ctx(), pf)
    assert not d.allowed
    assert any("max £9000" in b or "max £9,000" in b or "> max" in b for b in d.blocks)


def test_max_positions_blocks():
    pf = PortfolioState(deployed_gbp=0.0, open_positions=2, cumulative_realised_loss_gbp=0.0)
    d = evaluate(_good_csp(), _good_ctx(), pf)
    assert not d.allowed
    assert any("Open positions" in b for b in d.blocks)


# ── NO FALSE POSITIVES: missing required input must BLOCK ────────────────
def test_missing_regime_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "regime": None})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("Regime could not be determined" in b for b in d.blocks)


def test_missing_falling_knife_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "falling_knife": None})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("Falling-knife status unavailable" in b for b in d.blocks)


def test_missing_iv_rank_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "iv_rank": None})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("IV-Rank unavailable" in b for b in d.blocks)


def test_stale_data_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "data_fresh": False})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("stale or invalid" in b for b in d.blocks)


def test_missing_earnings_calendar_blocks():
    ctx = MarketContext(**{**_good_ctx().__dict__, "earnings_in_expiry_window": None})
    d = evaluate(_good_csp(), ctx, _flat())
    assert not d.allowed
    assert any("Earnings calendar unavailable" in b for b in d.blocks)


# ── Drawdown brakes ─────────────────────────────────────────────────────
def test_brake_tiers():
    cfg = OptionsRiskConfig()
    assert brake_tier_for(0, cfg) == (0, 1.0)
    assert brake_tier_for(600, cfg) == (1, 1.0)
    assert brake_tier_for(1200, cfg) == (2, 0.5)
    assert brake_tier_for(1600, cfg) == (3, 0.0)
    assert brake_tier_for(2600, cfg) == (4, 0.0)


def test_circuit_breaker_blocks_everything():
    pf = PortfolioState(deployed_gbp=0.0, open_positions=0, cumulative_realised_loss_gbp=3000.0)
    d = evaluate(_good_csp(), _good_ctx(), pf)
    assert not d.allowed
    assert d.brake_tier == 4
    assert any("CIRCUIT BREAKER" in b for b in d.blocks)


def test_brake3_no_new_positions():
    pf = PortfolioState(deployed_gbp=0.0, open_positions=0, cumulative_realised_loss_gbp=1600.0)
    d = evaluate(_good_csp(), _good_ctx(), pf)
    assert not d.allowed
    assert d.brake_tier == 3
    assert any("no new positions" in b for b in d.blocks)


def test_brake2_halves_per_position_limit():
    # brake 2 → size_factor 0.5 → per-position limit halved to £2500; a £4300
    # notional that passed at full size now blocks.
    pf = PortfolioState(deployed_gbp=0.0, open_positions=0, cumulative_realised_loss_gbp=1100.0)
    d = evaluate(_good_csp(), _good_ctx(), pf)
    assert d.brake_tier == 2 and d.size_factor == 0.5
    assert not d.allowed
    assert any("halved by drawdown brake 2" in b for b in d.blocks)


def test_no_trade_is_clean_outcome_not_exception():
    # A blocked candidate returns a normal RiskDecision (no raise).
    ctx = MarketContext(**{**_good_ctx().__dict__, "iv_rank": 5.0})
    d = evaluate(_good_csp(), ctx, _flat())
    assert d.allowed is False
    assert isinstance(d.blocks, list) and len(d.blocks) >= 1
    assert "iv_rank" in d.checked
