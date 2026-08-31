"""The screen answers "is this a good trade?", never "can I afford it today?".

Owner ruling, restated 2026-08-26. The wheel desk showed NVDA as

    — no · Notional £15,354 > per-position limit £10,000

for a put that cleared every merit gate. The owner's replies, in order: "the
reason shdnt be that capital allocation is >10000", "i can put in capital if
needed", and — once the message was demoted from a block to a warning — "remove
this ... its more of a noise".

Both halves matter and they are different requirements:

  * ELIGIBILITY must not depend on today's balance. Funding is a decision the
    owner makes after seeing the trade, so a screener that hides the trade has
    pre-empted it.
  * The message must not merely move from `blocks` to `warnings`. The row
    already carries the fact in a better form — the Size fit column renders
    notional as a percentage of NAV — so the sentence is a second, wordier copy
    of a number already on screen.

The AUTONOMOUS paper-wheel is the opposite case and keeps the default: it
commits collateral without asking, so for it these are hard limits.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.quant_engine.options.risk import (
    Regime,
    MarketContext,
    OptionsRiskConfig,
    PortfolioState,
    Structure,
    TradeCandidate,
    evaluate,
)

CAPITAL_PHRASES = ("per-position limit", "assignment buffer breached",
                   "Deployed £", "Open positions")


def _sound_ctx(**kw):
    """Clears every merit gate: liquid, fair premium, no earnings."""
    base = dict(regime=Regime.GREEN, falling_knife=False, pct_off_52w_high=25.0,
                open_interest=5000, bid_ask_spread_usd=0.05,
                premium_mid_usd=2.50, earnings_in_expiry_window=False,
                data_fresh=True, iv_rank=None, iv_hv_ratio=1.25,
                iv_rank_window_days=14)
    base.update(kw)
    return MarketContext(**base)


def _oversized_candidate(notional=22835.0):
    """IWM's real numbers from the 26 Aug run — blocked on capital alone."""
    return TradeCandidate(symbol="IWM", structure=Structure.CASH_SECURED_PUT,
                          abs_delta=0.28, dte=37, strike=200.0,
                          notional_gbp=notional)


def _book():
    return PortfolioState(deployed_gbp=4000.0, open_positions=1)


def test_screen_allows_a_sound_trade_that_exceeds_the_capital_limit():
    """THE ruling. IWM was a good trade the owner could choose to fund."""
    d = evaluate(_oversized_candidate(), _sound_ctx(), _book(),
                 OptionsRiskConfig(), capital_gates=False)
    assert d.allowed, f"a sound trade was rejected on capital: {d.all_blocks}"


def test_screen_emits_no_capital_noise_anywhere():
    """"its more of a noise" — not in blocks, and not relocated to warnings
    either. Size fit already shows notional as a % of NAV."""
    d = evaluate(_oversized_candidate(), _sound_ctx(), _book(),
                 OptionsRiskConfig(), capital_gates=False)
    leaked = [m for m in (list(d.all_blocks) + list(d.warnings))
              if any(p in m for p in CAPITAL_PHRASES)]
    assert not leaked, f"capital text leaked onto the screen: {leaked}"


def test_the_autonomous_wheel_still_hard_blocks_on_capital():
    """The other half of the split. This path spends collateral unattended."""
    d = evaluate(_oversized_candidate(), _sound_ctx(), _book(),
                 OptionsRiskConfig(), capital_gates=True)
    assert not d.allowed
    assert any("per-position limit" in b for b in d.all_blocks), d.all_blocks


def test_capital_gates_default_to_on():
    """Anything that forgets the flag must get the SAFE behaviour."""
    d = evaluate(_oversized_candidate(), _sound_ctx(), _book(),
                 OptionsRiskConfig())
    assert not d.allowed, "capital gating must default to ON"


def test_merit_failures_still_block_the_screen():
    """Suppressing capital must not turn the screen into a rubber stamp."""
    # 0.61 is a WARNING now, not a block (the IV/HV dial, 31 Aug 2026) — so the
    # merit failure this asserts has to be one that genuinely still blocks.
    # Below the floor is exactly that: no strike choice rescues it.
    thin = evaluate(_oversized_candidate(), _sound_ctx(iv_hv_ratio=0.20),
                    _book(), OptionsRiskConfig(), capital_gates=False)
    assert not thin.allowed and any("IV/HV" in b for b in thin.blocks), thin.blocks

    illiquid = evaluate(_oversized_candidate(), _sound_ctx(open_interest=3),
                        _book(), OptionsRiskConfig(), capital_gates=False)
    assert not illiquid.allowed, illiquid.blocks

    earnings = evaluate(_oversized_candidate(),
                        _sound_ctx(earnings_in_expiry_window=True),
                        _book(), OptionsRiskConfig(), capital_gates=False)
    assert not earnings.allowed, earnings.blocks


def test_an_unknown_notional_still_blocks_even_on_the_screen():
    """The one capital-adjacent message that must SURVIVE. A missing notional
    is a data failure, not a funding choice — and Size fit cannot render it
    either, so suppressing it would hide the gap rather than de-duplicate it.
    """
    d = evaluate(
        TradeCandidate(symbol="IWM", structure=Structure.CASH_SECURED_PUT,
                       abs_delta=0.28, dte=37, strike=200.0, notional_gbp=None),
        _sound_ctx(), _book(), OptionsRiskConfig(), capital_gates=False)
    assert not d.allowed
    assert any("notional unavailable" in b.lower() for b in d.all_blocks), d.all_blocks


def test_the_capital_checks_still_appear_in_the_audit_trail():
    """Suppressed output must not become a skipped check — `checked` is the
    record of what was actually evaluated."""
    d = evaluate(_oversized_candidate(), _sound_ctx(), _book(),
                 OptionsRiskConfig(), capital_gates=False)
    for gate in ("per_position_limit", "deployment_limit", "max_positions"):
        assert gate in d.checked, f"{gate} vanished from the audit trail"
