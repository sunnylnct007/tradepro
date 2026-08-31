"""Open interest may only REJECT a candidate when its source can be trusted.

THE DEFECT, 31 Aug 2026. The live wheel board showed 1 eligible name out of 82,
and the single largest rejection reason — 53 of 82 rows — was:

    "Open interest N < 250 AND the spread is wide"

The open-interest values driving that were min 1, median 40, max 222 across 82
large-caps, which is not credible. Measured against IBKR on the same contract at
the same moment:

    XOM 155P exp 2026-09-04    ours: 58      IBKR: 868
    (an earlier review of a different XOM strike: ours 57, live 7,570)

IBKR does serve open interest — `option_open_interest` returns
{callInterest: 0, putInterest: 868} — but only on the LIVE account. It is not
served on the paper cpapi session the screen authenticates with: probing fields
7085-7089, 7607, 7638, 7639 and 7697-7698 against that exact contract returned
none of them. So every OI we hold today comes from our own capture, and it is
wrong by more than an order of magnitude.

Blocking a genuinely liquid name on a number we know is false is worse than not
checking at all, because it presents as a judgement about the MARKET when it is
a judgement about our DATA.

Owner's instruction, same day: "OI data doesnt need to be a hard gate".

The spread remains a real gate. It is verified against live IBKR quotes and it
measures the thing that actually decides whether you get filled.

An UNSPECIFIED source still blocks. A caller that says nothing about provenance
keeps the strict behaviour, so this cannot silently weaken a future feed.
"""
from __future__ import annotations

from tradepro_strategies.quant_engine.options.risk import (
    MarketContext, OptionsRiskConfig, PortfolioState, Regime, Structure,
    TradeCandidate, evaluate,
)

CAPTURE = "option_quote_daily"


def _cand():
    return TradeCandidate(symbol="XOM", structure=Structure.CASH_SECURED_PUT,
                          abs_delta=0.28, dte=35, strike=155.0, notional_gbp=8000.0)


def _decide(oi, source, *, spread=0.05, mid=1.40):
    ctx = MarketContext(
        regime=Regime.GREEN, falling_knife=False, pct_off_52w_high=25.0,
        open_interest=oi, open_interest_source=source,
        bid_ask_spread_usd=spread, premium_mid_usd=mid,
        earnings_in_expiry_window=False, data_fresh=True, iv_rank=45.0)
    return evaluate(_cand(), ctx, PortfolioState(), OptionsRiskConfig(),
                    capital_gates=False)


def _oi_blocks(d):
    return [b for b in d.all_blocks if "interest" in b.lower()]


def test_untrusted_oi_never_blocks(d_source=CAPTURE):
    """THE regression. XOM's real 868 read as 58 from our capture."""
    d = _decide(58, CAPTURE)
    assert not _oi_blocks(d), _oi_blocks(d)
    assert d.allowed, d.all_blocks


def test_untrusted_oi_says_so_rather_than_going_quiet():
    """Not blocking is not the same as hiding it. The row must state where the
    number came from and that it was not used to reject."""
    d = _decide(58, CAPTURE)
    warned = [w for w in d.warnings if "interest" in w.lower()]
    assert warned, d.warnings
    assert CAPTURE in warned[0]
    assert "not from IBKR" in warned[0]
    assert "NOT used to reject" in warned[0]


def test_even_a_zero_from_an_untrusted_source_does_not_block():
    """A feed wrong by 15x is equally capable of reporting a false zero, and a
    false zero is the most damaging direction — it reads as 'untradeable'."""
    d = _decide(0, CAPTURE)
    assert not _oi_blocks(d), _oi_blocks(d)


def test_an_unspecified_source_keeps_the_strict_behaviour():
    """A caller that says nothing must not silently get the weaker rule."""
    d = _decide(3, None)
    assert _oi_blocks(d), "unspecified provenance must still block a near-zero"


def test_a_trusted_near_zero_still_blocks():
    """When IBKR does serve it, an empty contract is a real rejection again."""
    d = _decide(3, "ibkr")
    assert _oi_blocks(d), d.all_blocks
    assert "illiquid" in _oi_blocks(d)[0]


def test_a_trusted_healthy_oi_passes():
    d = _decide(868, "ibkr")
    assert not _oi_blocks(d), _oi_blocks(d)
    assert d.allowed, d.all_blocks


def test_the_spread_is_still_a_real_gate_regardless_of_oi_source():
    """Removing OI must not remove liquidity checking altogether. The spread is
    verified against live IBKR quotes and stays authoritative."""
    d = _decide(868, CAPTURE, spread=0.60, mid=1.40)
    assert not d.allowed
    assert any("spread" in b.lower() for b in d.all_blocks), d.all_blocks


def test_chain_sourced_oi_is_allowed_to_block():
    """`resolve_open_interest` labels chain values "g3", and a chain value IS
    IBKR's own figure — verified 31 Aug: conid 904441116 returned 7638="868",
    matching the live account's option_open_interest digit for digit.

    The first version of oi_blocking_sources listed "g3_ibkr", a string that
    exists nowhere in the codebase, so real IBKR open interest would never have
    been trusted. Guessing an identifier instead of reading it is the same
    mistake that put "7638 is WRONG" into the parser for a week.
    """
    d = _decide(3, "g3")
    assert _oi_blocks(d), "IBKR chain OI must be allowed to reject an empty contract"


def test_chain_sourced_healthy_oi_passes():
    d = _decide(868, "g3")
    assert not _oi_blocks(d), _oi_blocks(d)
    assert d.allowed, d.all_blocks
