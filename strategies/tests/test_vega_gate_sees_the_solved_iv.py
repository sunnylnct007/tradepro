"""If the screen can PRINT a vega edge, the gate may not call it unavailable.

THE DEFECT, 2026-08-26. Every row on the wheel desk showed a vega edge — NVDA
1.21, SLV 1.21, XLF 1.46, TLT 1.07 — and was simultaneously blocked with
"IV-Rank unavailable — cannot confirm the vega edge for selling premium."
Both statements cannot be true of the same row.

The cause was ordering, not missing data:

    line 1384   ctx = MarketContext(iv_hv_ratio=ivr.iv_hv_ratio if ivr.available ...)
    line 1405   decision = evaluate(cand, ctx, ...)      <-- gate runs HERE
    line 1426   iv_solved = solve_iv_and_crosscheck(...) <-- solve runs HERE
    line 1436   ivr = replace(ivr, available=True, iv_hv_ratio=...)  --> the DISPLAY

`evaluate()` saw `iv_hv_ratio=None` and took its both-are-None branch. Forty
lines later the solve succeeded and fed the rendered row. So the screen
computed the number, displayed it, and declared it unknowable — 67 of 82 rows,
for as long as the solve had existed.

Note what did NOT catch it: 752 passing tests. Every one checked a piece in
isolation. The solve was correct. The gate was correct. `MarketContext` was
correct. Only their ORDER was wrong, and nothing asserted a relationship
between two things that were individually fine.

Two guards, because the bug has two faces:
  * the SEMANTIC one — a populated ratio must never yield "unavailable";
  * the ORDERING one — the solve must run before the context that reads it,
    which is the specific thing that regressed and the thing a future edit
    could silently undo again.
"""
from __future__ import annotations

import os
import re

import pytest

from tradepro_strategies.quant_engine.options.risk import (
    MarketContext,
    PortfolioState,
    Structure,
    TradeCandidate,
    OptionsRiskConfig,
    evaluate,
)

SCREEN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tradepro_strategies", "cli", "options_screen.py")

UNAVAILABLE = "IV-Rank unavailable"


def _decide(**ctx_kw):
    """A candidate that clears every gate except the one under test."""
    ctx = MarketContext(
        open_interest=5000,
        bid_ask_spread_usd=0.05,
        premium_mid_usd=2.50,
        earnings_in_expiry_window=False,
        data_fresh=True,
        **ctx_kw,
    )
    cand = TradeCandidate(
        symbol="NVDA", structure=Structure.CASH_SECURED_PUT,
        abs_delta=0.28, dte=37, strike=200.0, notional_gbp=1000.0)
    return evaluate(cand, ctx, PortfolioState(), OptionsRiskConfig())


def _blocks(decision):
    return list(decision.all_blocks or [])


def test_a_populated_bridge_ratio_is_never_reported_unavailable():
    """THE regression, stated semantically. NVDA's row carried 1.21."""
    d = _decide(iv_rank=None, iv_hv_ratio=1.21, iv_rank_window_days=12)
    assert not [b for b in _blocks(d) if UNAVAILABLE in b], (
        f"a row with a real IV/HV of 1.21 was told its vega edge is unknowable: {_blocks(d)}")


def test_a_thin_bridge_ratio_still_blocks_but_says_why():
    """The gate must keep doing its job — AMZN at 0.61 is a REAL rejection,
    and it must not be silently converted into a pass by this fix."""
    d = _decide(iv_rank=None, iv_hv_ratio=0.61, iv_rank_window_days=12)
    blocked = _blocks(d)
    assert any("IV/HV" in b for b in blocked), blocked
    assert not [b for b in blocked if UNAVAILABLE in b], blocked


def test_genuinely_absent_vega_data_still_blocks():
    """The no-false-positives floor. When nothing is known, block."""
    d = _decide(iv_rank=None, iv_hv_ratio=None)
    assert [b for b in _blocks(d) if UNAVAILABLE in b], (
        "with neither rank nor bridge the gate must still block")


def test_a_real_rank_takes_precedence_over_the_bridge():
    d = _decide(iv_rank=75.0, iv_hv_ratio=0.61, iv_rank_window_days=300)
    assert not [b for b in _blocks(d) if UNAVAILABLE in b or "IV/HV" in b], _blocks(d)


def test_the_solve_runs_before_the_context_that_reads_it():
    """THE ordering guard — the specific thing that regressed.

    A semantic test on evaluate() cannot see this: the gate was always
    correct. What was wrong was that options_screen.py called it with a value
    it had not computed yet.
    """
    with open(SCREEN) as fh:
        src = fh.read()

    solve = [m.start() for m in re.finditer(r"iv_solved\s*=\s*solve_iv_and_crosscheck", src)]
    ctx = [m.start() for m in re.finditer(r"^\s{4}ctx\s*=\s*MarketContext\(", src, re.M)]
    assert solve, "the IV solve disappeared from options_screen.py"
    assert ctx, "MarketContext construction not found in options_screen.py"

    first_solve, last_ctx = min(solve), max(ctx)
    assert first_solve < last_ctx, (
        "solve_iv_and_crosscheck() runs AFTER MarketContext is built, so the "
        "vega gate reads iv_hv_ratio=None while the rendered row shows the "
        "solved value — the 2026-08-26 'IV-Rank unavailable on every row' bug")


def test_there_is_exactly_one_solve_site():
    """Two solves would be two definitions of the vega edge, which is how the
    displayed number and the gated number diverged in the first place."""
    with open(SCREEN) as fh:
        src = fh.read()
    n = len(re.findall(r"iv_solved\s*=\s*solve_iv_and_crosscheck", src))
    assert n == 1, f"expected exactly one IV solve site, found {n}"
