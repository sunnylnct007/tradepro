"""THE mean-reversion signal — one definition, imported by everyone.

The screen, the backtest harness and the live paper strategy must agree about
what a signal IS, or gate F1 (signal fidelity) in the forward test is really
just a test of how carefully I copied code between three files. This module
exists so there is nothing to copy.

Owner's standing rule, and the reason: never hand-reimplement a strategy —
port it once and pin it with a parity test.

THE RULE (MEAN_REVERSION_GATES_V1.md, amended by MEAN_REVERSION_HOLD_V3.md,
measured in backtests/studies/mean_reversion_v2.py):

    entry    close < 2.5 sigma below the 20-day mean, while above the 200-SMA
    target   the 20-day mean, recomputed daily
    stop     -8% from the fill
    timeout  20 sessions            <- raised from 10 on 23 Aug 2026

MEASURED EXPECTATIONS, on 244 names, 2,310 trades:

    entering at the signal-bar CLOSE   72.8% win   +1.06%/trade   (backtest)
    entering at the NEXT OPEN          ~72% win    +0.97%/trade   (achievable)

The live strategy can only do the second — you cannot place an order at a
close you have not seen yet. **+0.97%/trade is therefore the live baseline,
not +1.06%**, and the 0.09% difference IS the cost of the delay, already
deducted. Anything worse than +0.97% is slippage, which is what F3 measures.

Do NOT deduct the delay twice. An earlier draft of this block read "~+0.88%
is the live baseline" — that was 0.09% taken off a figure it had already been
taken off. +0.88% was the correct live baseline when the backtest stood at
+0.97% (the 10-session hold); after the hold moved to 20 the backtest is
+1.06% and the live figure is +0.97%. Carrying the old subtrahend forward
under-states the strategy by a tenth of a percent per trade, which is roughly
a tenth of the whole edge.

That one-session delay is the DESIGN, not a lag to be engineered away: the
signal is computed on a SETTLED close and the order goes in at the next open.
Evaluating the rule against a partial intraday bar would be a different rule
from the one that was measured, and the forward test would no longer be
testing anything. The Scanner's "include today's session" toggle previews
exactly that and is deliberately not what trades.

WHY THIS BLOCK IS WORTH KEEPING HONEST: it said "timeout 10 sessions" and
quoted the 2,251-trade numbers for a day after the hold changed to 20, in the
one file that is supposed to BE the rule. The constants below were right the
whole time — only the prose was stale, which is the harder half of the
duplicate-knowledge problem: a wrong number in code fails a test, a wrong
number in a docstring just quietly misinforms whoever reads it next.
"""
from __future__ import annotations

import statistics as st

SIGMA = 2.5
BB_WINDOW = 20
TREND_WINDOW = 200
STOP_PCT = 0.08
MAX_HOLD = 20
"""Raised from 10 on 2026-08-23, BEFORE the forward test started. See
MEAN_REVERSION_HOLD_V3.md.

The 10-session cap was closing 31.4% of trades before either the target or the
stop was reached — the one exit that ends a trade which has not actually
failed. Extending to 20 lifts the win rate 64.6% -> 72.5% and per-trade return
+0.71% -> +0.97% on the same 2,308 trades, and the gain holds in all four
cells of the two-split test (+0.25% to +0.29%, which is unusually even).

NOT true GTC, deliberately: no timeout scores +0.99% against 20 sessions'
+0.97%, and gives up the bound on how long capital is committed for two basis
points. Median hold is 7 sessions either way — the cap only ever truncated the
tail."""

MIN_BARS = TREND_WINDOW + 10
"""History needed before a signal can be computed at all. A strategy with
fewer bars must decline to trade rather than compute on a short window —
that is how a 200-SMA silently becomes a 60-SMA."""


def sma(closes: list[float], i: int, n: int) -> float:
    return sum(closes[i - n + 1:i + 1]) / n


def entry_signal(closes: list[float], i: int) -> bool:
    """Does the rule fire on bar i? The single source of truth.

    Deliberately takes an index rather than "the latest bar" so the backtest
    can walk history and the live strategy can ask about its most recent
    settled bar, using identical code on both paths.
    """
    if i < MIN_BARS or i >= len(closes):
        return False
    window = closes[i - BB_WINDOW + 1:i + 1]
    if len(window) < BB_WINDOW:
        return False
    sd = st.pstdev(window)
    if sd <= 0:
        return False
    mean20 = sum(window) / BB_WINDOW
    return bool(closes[i] < mean20 - SIGMA * sd
                and closes[i] > sma(closes, i, TREND_WINDOW))


def target_price(closes: list[float], i: int) -> float:
    """Where to take profit, as of bar i — the 20-day mean, which MOVES.

    It rises to meet a recovering price, so the target comes to you. Both the
    graded harness convention and the live strategy recompute it daily; a
    target fixed at entry tested slightly worse (+0.91% vs +0.85% mean but a
    longer hold and a fatter tail) and is not what shipped.
    """
    return sma(closes, i, BB_WINDOW)


def stop_price(fill_price: float) -> float:
    return fill_price * (1 - STOP_PCT)


def exit_decision(closes: list[float], i: int, *, fill_price: float,
                  bars_held: int) -> tuple[bool, str] | tuple[bool, None]:
    """Should an open position close on bar i? Returns (exit, reason).

    Checked on the CLOSE, which is what the backtest did. A stop checked on
    the close does not survive a gap — the worst historical trade is -17.7%
    against an -8% stop for exactly that reason, and the live strategy must
    not pretend otherwise.
    """
    if closes[i] <= stop_price(fill_price):
        return True, "stop"
    if closes[i] >= target_price(closes, i):
        return True, "target"
    if bars_held >= MAX_HOLD:
        return True, "timeout"
    return False, None
