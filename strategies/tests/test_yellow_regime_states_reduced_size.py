"""YELLOW permits a short put at REDUCED size — and nothing reduces it.

Owner, seeing GREEN and YELLOW rows both marked "✓ tradeable":
"how come diff regiome and all tradeble".

The regime matrix permits CASH_SECURED_PUT in YELLOW with the inline comment
"reduced size (brake/size handles)". It does not:

  * the brakes are DRAWDOWN brakes keyed to cumulative realised loss in GBP
    (brake1 500 / brake2 1000 / brake3 1500) — they never read the regime;
  * `size_fit_pct` is notional / NAV, a consequence of the contract price;
  * there is no regime-based sizing anywhere in the screen.

So a YELLOW name presented identically to a GREEN one. On the 31 Aug board the
worst case was TSLA — YELLOW, and the LARGEST position on the screen at 22.5%
of NAV, the exact opposite of the stated intent.

This does NOT invent a size rule; choosing one is the owner's call. It makes the
screen state what the matrix already intends, so the reader is not told two
names are equivalent when the engine believes one needs half the size.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.quant_engine.options.risk import (
    MarketContext, OptionsRiskConfig, PortfolioState, Regime, Structure,
    TradeCandidate, evaluate,
)


def _decide(regime, structure=Structure.CASH_SECURED_PUT):
    cand = TradeCandidate(symbol="TSLA", structure=structure,
                          abs_delta=0.26, dte=32, strike=340.0, notional_gbp=8000.0)
    ctx = MarketContext(
        regime=regime, falling_knife=False, pct_off_52w_high=25.0,
        open_interest=433, open_interest_source="g3",
        bid_ask_spread_usd=0.15, premium_mid_usd=7.58,
        earnings_in_expiry_window=False, data_fresh=True, iv_rank=45.0)
    return evaluate(cand, ctx, PortfolioState(), OptionsRiskConfig(),
                    capital_gates=False)


def _size_warning(d):
    return [w for w in d.warnings if "REDUCED" in w]


def test_yellow_is_allowed_but_says_reduced_size():
    """THE regression. Allowed, yes — silently equivalent to GREEN, no."""
    d = _decide(Regime.YELLOW)
    assert d.allowed, d.all_blocks
    w = _size_warning(d)
    assert w, f"YELLOW passed with no size guidance: {d.warnings}"
    assert "HALF-SIZE" in w[0]


def test_the_warning_names_the_missing_behaviour():
    """House rule: a warning standing in for a block must say what is NOT being
    done for you, or the reader assumes it is handled."""
    w = _size_warning(_decide(Regime.YELLOW))[0]
    assert "does NOT size it for you" in w, w
    assert "size_fit" in w and "realised loss" in w, w


def test_green_gets_no_size_warning():
    """The signal has to discriminate — that is the whole complaint."""
    assert not _size_warning(_decide(Regime.GREEN))


def test_orange_still_blocks_outright():
    """Reduced size is for YELLOW. ORANGE is a refusal, not a smaller trade."""
    d = _decide(Regime.ORANGE)
    assert not d.allowed
    assert any("not permitted in ORANGE" in b for b in d.all_blocks), d.all_blocks


def test_a_defined_risk_structure_in_yellow_is_not_warned():
    """The reduction is about OPENING short-premium exposure. A bull put spread
    is defined-risk and is not a wheel-entry structure."""
    d = _decide(Regime.YELLOW, Structure.BULL_PUT_SPREAD)
    assert not _size_warning(d), d.warnings
