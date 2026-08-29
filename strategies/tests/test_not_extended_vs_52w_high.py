"""Don't sell puts into a name sitting at its own high.

Owner rule, 2026-08-26: "normally I would sell put on a symbol that is not
close to its 52 week high", and then plainly: "there is no point selling a put
in stock which is high already as it will do mean reversion."

The reasoning is sound and specific to short premium. A cash-secured put is
short downside. Sold on a name at its high, the position collects a small
credit precisely where there is most room to give back, and assignment lands
the shares at a price nobody would have chosen deliberately.

WHY IT WAS MISSING, and why the omission was systematic rather than random:
the regime gate leans the OTHER WAY. GREEN means in or above the Ichimoku
cloud, which selects for strength — so the screen preferentially surfaced
extended names. Measured on the 26 Aug run, of only four eligible candidates:

    SLV    41.8% off its 52-week high     <- the one that matched the rule
    XLF     0.2% off                      <- at the high
    IWM     2.2% off                      <- at the high
    SPY     1.8% off                      <- at the high

Cross-checked against the API the same day (raw closes throughout, no
adjusted/raw seam): identical to the parquet store to one decimal, so these
are real prices, not artefacts.

This gate is the bookend to falling-knife. That one rejects names that have
fallen too far; this one rejects names that have not fallen at all. Together
they define the band a wheel entry should occupy.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli.options_screen import _pct_off_52w_high
from tradepro_strategies.quant_engine.options.risk import (
    MarketContext,
    OptionsRiskConfig,
    PortfolioState,
    Regime,
    Structure,
    TradeCandidate,
    evaluate,
)

EXTENDED = "off its 52-week high"


def _ctx(pct_off, **kw):
    base = dict(regime=Regime.GREEN, falling_knife=False, open_interest=5000,
                bid_ask_spread_usd=0.05, premium_mid_usd=2.50,
                earnings_in_expiry_window=False, data_fresh=True,
                iv_rank=None, iv_hv_ratio=1.25, iv_rank_window_days=14,
                pct_off_52w_high=pct_off)
    base.update(kw)
    return MarketContext(**base)


def _put(symbol="XLF"):
    return TradeCandidate(symbol=symbol, structure=Structure.CASH_SECURED_PUT,
                          abs_delta=0.28, dte=37, strike=57.0,
                          notional_gbp=4500.0)


def _decide(pct_off, cfg=None, **kw):
    return evaluate(_put(), _ctx(pct_off, **kw), PortfolioState(),
                    cfg or OptionsRiskConfig(), capital_gates=False)


# ── the rule ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pct_off,symbol", [(0.2, "XLF"), (1.8, "SPY"), (2.2, "IWM")])
def test_names_at_their_high_are_blocked(pct_off, symbol):
    """The three real cases the owner objected to."""
    d = evaluate(_put(symbol), _ctx(pct_off), PortfolioState(),
                 OptionsRiskConfig(), capital_gates=False)
    assert not d.allowed
    assert any(EXTENDED in b for b in d.all_blocks), d.all_blocks


def test_a_name_well_off_its_high_still_passes():
    """SLV at 41.8% off — the one that matched the rule, and must survive."""
    assert _decide(41.8).allowed


def test_the_threshold_boundary_is_inclusive():
    """At exactly the floor the name is acceptable; a hair under is not."""
    assert _decide(5.0).allowed
    assert not _decide(4.9).allowed


# ── the guards around it ────────────────────────────────────────────────────

def test_unknown_extension_blocks_rather_than_passing():
    """"I cannot tell how extended this is" must never read as "not extended"
    — the module's no-false-positives rule."""
    d = _decide(None)
    assert not d.allowed
    assert any("52-week high unavailable" in b for b in d.all_blocks), d.all_blocks


def test_the_gate_can_be_disabled_entirely():
    """The threshold is a judgement, so it is a knob. 0 disables it, and then
    an unknown value must not block either."""
    off = OptionsRiskConfig(min_pct_off_52w_high=0.0)
    assert _decide(0.2, off).allowed
    assert _decide(None, off).allowed


def test_it_does_not_touch_structures_that_are_not_wheel_entries():
    """The rule is about being SHORT DOWNSIDE. A protective put buys it."""
    d = evaluate(
        TradeCandidate(symbol="XLF", structure=Structure.PROTECTIVE_PUT,
                       abs_delta=0.28, dte=37, strike=57.0, notional_gbp=4500.0),
        _ctx(0.2), PortfolioState(), OptionsRiskConfig(), capital_gates=False)
    assert not [b for b in d.all_blocks if EXTENDED in b], d.all_blocks


def test_it_complements_falling_knife_rather_than_duplicating_it():
    """The two gates are bookends: too extended, and fallen too far. A name can
    fail either independently."""
    extended = _decide(0.2, falling_knife=False)
    assert not extended.allowed and any(EXTENDED in b for b in extended.blocks)

    knife = _decide(45.0, falling_knife=True)
    assert not knife.allowed and any("FALLING-KNIFE" in b for b in knife.blocks)
    assert not [b for b in knife.blocks if EXTENDED in b], (
        "a name 45% off its high must not ALSO be called extended")


# ── the measurement itself ──────────────────────────────────────────────────

def test_measure_reports_zero_at_the_high_and_scales_down():
    assert _pct_off_52w_high([100.0] * 200) == 0.0
    assert round(_pct_off_52w_high([100.0] * 199 + [80.0]), 1) == 20.0


def test_measure_refuses_to_answer_on_thin_history():
    """A "52-week high" from two months of bars means nothing, and would pass
    a distance test trivially. None, so the gate blocks."""
    assert _pct_off_52w_high([100.0] * 50) is None
    assert _pct_off_52w_high([]) is None
    assert _pct_off_52w_high(None or []) is None


def test_measure_ignores_non_positive_closes():
    """A zero or negative close is a bad bar, not a 100% drawdown."""
    got = _pct_off_52w_high([0.0] * 5 + [100.0] * 199 + [90.0])
    assert got is not None and round(got, 1) == 10.0
