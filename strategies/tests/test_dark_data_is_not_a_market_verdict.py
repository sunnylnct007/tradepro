"""A feed outage must not read as a market verdict.

THE DEFECT, measured on the live wheel board 28 Aug 2026:

    82 screened, 0 eligible
    32 rows  "Pricing carried from the last priced screen (Nh old)"
    12 rows  "Bid-ask spread unavailable."
    20 rows  "CASH_SECURED_PUT not permitted in ORANGE regime"

Forty-four of eighty-two rows — 54% of the board — were blocked because the
option chain was DARK, and they rendered identically to a name rejected on its
merits. The owner reading that board sees "nothing qualifies today" when the
truth is "we could not price half of it".

The two facts answer different questions and only one of them is about the
market:

    blocks       this is not a good trade        -> a market verdict
    data_blocks  we cannot verify this trade     -> an outage

`data_blocks` STILL counts against `allowed`. A trade whose inputs cannot be
checked must never be offered — that is the no-false-positives rule and this
change does not touch it. What it adds is the ability to say WHICH kind of
"no" a row is, so a dark feed can be reported as a dark feed.

`merit_ok` is the useful state that had no name before: the setup is sound and
only its pricing is missing. Those rows are the ones worth re-checking the
moment the chain comes back, and the ones a board should show differently from
a name that simply failed.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.quant_engine.options.risk import (
    MarketContext,
    OptionsRiskConfig,
    PortfolioState,
    Regime,
    Structure,
    TradeCandidate,
    evaluate,
)


def _ctx(**kw):
    base = dict(regime=Regime.GREEN, falling_knife=False, pct_off_52w_high=25.0,
                open_interest=5000, bid_ask_spread_usd=0.05, premium_mid_usd=2.50,
                earnings_in_expiry_window=False, data_fresh=True,
                iv_rank=None, iv_hv_ratio=1.25, iv_rank_window_days=14)
    base.update(kw)
    return MarketContext(**base)


def _put():
    return TradeCandidate(symbol="XLF", structure=Structure.CASH_SECURED_PUT,
                          abs_delta=0.28, dte=37, strike=57.0, notional_gbp=4500.0)


def _decide(**kw):
    return evaluate(_put(), _ctx(**kw), PortfolioState(), OptionsRiskConfig(),
                    capital_gates=False)


def test_a_clean_setup_with_a_dark_chain_is_merit_ok():
    """THE case. Nothing is wrong with the trade; we just cannot price it."""
    d = _decide(bid_ask_spread_usd=None)
    assert not d.allowed, "an unverifiable trade must still not be offered"
    assert d.merit_ok, "the SETUP is sound — only the pricing is missing"
    assert not d.blocks, f"nothing about the trade should fail: {d.blocks}"
    assert any("spread unavailable" in b for b in d.data_blocks), d.data_blocks


def test_a_real_rejection_is_not_merit_ok():
    """ORANGE regime is a market verdict, not an outage, and must read as one."""
    d = _decide(regime=Regime.ORANGE)
    assert not d.allowed and not d.merit_ok
    assert any("ORANGE" in b for b in d.blocks)
    assert not d.data_blocks


def test_a_bad_trade_with_a_dark_chain_is_still_a_bad_trade():
    """Both kinds at once must not be flattered into merit_ok — the trade
    fails on its own terms regardless of the feed."""
    d = _decide(regime=Regime.ORANGE, bid_ask_spread_usd=None)
    assert not d.allowed and not d.merit_ok
    assert d.blocks and d.data_blocks


def test_a_fully_clean_row_has_neither():
    d = _decide()
    assert d.allowed and not d.blocks and not d.data_blocks
    assert not d.merit_ok, "merit_ok means 'sound but unpriceable', not 'fine'"


@pytest.mark.parametrize("kw,needle", [
    ({"data_fresh": False}, "stale or invalid"),
    ({"iv_rank": None, "iv_hv_ratio": None}, "IV-Rank unavailable"),
    ({"open_interest": None}, "Open interest unavailable"),
    ({"earnings_in_expiry_window": None}, "Earnings calendar unavailable"),
    ({"falling_knife": None}, "Falling-knife status unavailable"),
    ({"regime": None}, "Regime could not be determined"),
])
def test_every_could_not_verify_condition_is_classed_as_data(kw, needle):
    """Each of these says "we do not know", not "this is bad"."""
    d = _decide(**kw)
    assert not d.allowed
    assert any(needle in b for b in d.data_blocks), (
        f"{needle!r} should be a DATA condition, found blocks={d.blocks} "
        f"data_blocks={d.data_blocks}")


def test_all_blocks_still_shows_everything():
    """Callers that only care THAT it is blocked keep one place to look."""
    d = _decide(regime=Regime.ORANGE, bid_ask_spread_usd=None)
    assert set(d.all_blocks) == set(d.blocks) | set(d.data_blocks)
    assert len(d.all_blocks) == len(d.blocks) + len(d.data_blocks)
