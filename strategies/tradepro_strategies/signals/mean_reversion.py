"""THE mean-reversion signal — one definition, imported by everyone.

The screen, the backtest harness and the live paper strategy must agree about
what a signal IS, or gate F1 (signal fidelity) in the forward test is really
just a test of how carefully I copied code between three files. This module
exists so there is nothing to copy.

Owner's standing rule, and the reason: never hand-reimplement a strategy —
port it once and pin it with a parity test.

THE RULE (MEAN_REVERSION_GATES_V1.md, measured in
backtests/studies/mean_reversion_v2.py):

    entry    close < 2.5 sigma below the 20-day mean, while above the 200-SMA
    target   the 20-day mean, recomputed daily
    stop     -8% from the fill
    timeout  10 sessions

MEASURED EXPECTATIONS, on 244 names, 2,251 trades:

    entering at the signal-bar CLOSE   64.9% win   +0.854%/trade   (backtest)
    entering at the NEXT OPEN          64.9% win   +0.769%/trade   (achievable)

The live strategy can only do the second — you cannot place an order at a
close you have not seen yet. **+0.77%/trade is therefore the live baseline,
not +0.85%**, and the 0.085% difference is the cost of the delay, not
slippage. Anything worse than that IS slippage and is what F3 measures.
"""
from __future__ import annotations

import statistics as st

SIGMA = 2.5
BB_WINDOW = 20
TREND_WINDOW = 200
STOP_PCT = 0.08
MAX_HOLD = 10

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
