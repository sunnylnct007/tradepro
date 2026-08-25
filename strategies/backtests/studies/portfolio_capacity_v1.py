"""How many positions do these sleeves ASK for, and what does capping cost?

PRE-REGISTERED, 25 Aug 2026. Owner: "what I need is a proper system that can
tell me what to get into and what not to."

This is the question that turns a signal list into a system. Both sleeves passed
their gates on a PER-TRADE basis — +1.10% Swing, +2.25% Momentum — and neither
was ever asked the portfolio question: how much capital is committed at once,
and what happens when the answer exceeds 100%?

The measurement that prompted this (concurrency of open positions, 16 years):

    SWING      2,503 trades   median  7 concurrent   p95  28   MAX  62
    MOMENTUM   5,855 trades   median 55 concurrent   p95 126   MAX 161

At the 5% per trade the forward simulation assumed, Momentum's MEDIAN state is
275% of capital committed and its peak is 805%. Momentum as specified is
UNFUNDABLE. Swing's median is a comfortable 35%, but its p95 is 140% — so even
Swing needs a cap, and nobody has ever set one.

FORWARD_TEST_GATES_V1 modelled ~84 trades over 12 weeks at 5% each and never
asked how many were open simultaneously. This fills that gap.

METHOD. Cap concurrent positions at N. When more signals fire than slots, take
them first-come-first-served — no ranking, because inventing one here would be
curve-fitting and it is a separate question. Size is held at ~100%/N so the
runs are comparable: you cannot fund 20 slots at 10% each.

PRE-REGISTERED PREDICTIONS:
  1. Per-trade mean FALLS as N falls — a cap drops trades. Expected, not the
     metric.
  2. Return on the ACCOUNT peaks at some intermediate N. Too few slots leaves
     capital idle; too many dilutes into marginal signals.
  3. Swing tolerates a lower cap than Momentum, because its median concurrency
     is 7 against 55.
  4. I expect the useful Swing cap between 8 and 15.

  NOT confident about: whether first-come-first-served is much worse than no
  cap. If arbitrary dropping barely hurts, the edge is BROAD and a ranking
  layer is optional. If it hurts a lot, ranking is REQUIRED before either
  sleeve can be sized.
"""
from __future__ import annotations

import statistics as st

from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals import mean_reversion as mr


def sma(x, i, n):
    return sum(x[i - n + 1:i + 1]) / n


def collect():
    swing, mom = [], []
    for sym in universe_symbols():
        d = _load(sym)
        if d is None or len(d) < 300:
            continue
        c = d["close"].tolist(); h = d["high"].tolist()
        l = d["low"].tolist(); o = d["open"].tolist()
        dt = [str(x)[:10] for x in d.index]
        if not poison_check(c, d["volume"].tolist() if "volume" in d else None)[0]:
            continue
        i = 210
        while i < len(c) - 1:
            if not mr.entry_signal(c, i):
                i += 1; continue
            e = c[i]; stop = e * (1 - mr.STOP_PCT); out = None
            for j in range(i + 1, min(len(c), i + mr.MAX_HOLD + 1)):
                t = sma(c, j, mr.BB_WINDOW)
                if l[j] <= stop: out = (100 * (min(stop, o[j]) / e - 1), j); break
                if h[j] >= t: out = (100 * (max(t, o[j]) / e - 1), j); break
            if out is None:
                j = min(len(c) - 1, i + mr.MAX_HOLD); out = (100 * (c[j] / e - 1), j)
            swing.append((dt[i], dt[out[1]], out[0])); i = out[1] + 1
        i = 210
        while i < len(c) - 1:
            if not (c[i] > sma(c, i, 200) and sma(c, i, 20) > sma(c, i, 50)
                    and c[i] > sma(c, i, 20) and c[i] / sma(c, i, 10) - 1 <= 0.005
                    and c[i - 1] / sma(c, i - 1, 10) - 1 > 0.005):
                i += 1; continue
            e = c[i]; peak = e; out = None
            for j in range(i + 1, min(len(c), i + 61)):
                peak = max(peak, c[j])
                if c[j] <= e * 0.92 or c[j] <= peak * 0.92:
                    out = (100 * (c[j] / e - 1), j); break
            if out is None:
                j = min(len(c) - 1, i + 60); out = (100 * (c[j] / e - 1), j)
            mom.append((dt[i], dt[out[1]], out[0])); i = out[1] + 1
    return swing, mom


def simulate(trades, cap, size_pct):
    trades = sorted(trades)
    open_until, taken, skipped, pnl, peak = [], [], 0, 0.0, 0
    for d0, d1, p in trades:
        open_until = [x for x in open_until if x >= d0]
        if len(open_until) >= cap:
            skipped += 1
            continue
        open_until.append(d1)
        peak = max(peak, len(open_until))
        taken.append(p)
        pnl += p * size_pct / 100.0
    return taken, skipped, pnl, peak


def report(name, trades, sizes):
    print(f"\n{'='*76}\n{name}   {len(trades)} signals over the full history")
    print(f"{'cap':>5}{'size%':>7}{'taken':>8}{'skipped':>9}{'mean/trade':>12}"
          f"{'total on acct':>15}{'peak slots':>12}")
    for cap, size in sizes:
        taken, skipped, pnl, peak = simulate(trades, cap, size)
        print(f"{cap:>5}{size:>7.1f}{len(taken):>8}{skipped:>9}"
              f"{st.mean(taken):>11.2f}%{pnl:>14.0f}%{peak:>12}")


if __name__ == "__main__":
    swing, mom = collect()
    report("SWING", swing, [(4, 25), (6, 16.7), (8, 12.5), (10, 10),
                            (12, 8.3), (15, 6.7), (20, 5), (28, 3.6), (62, 1.6)])
    report("MOMENTUM", mom, [(4, 25), (6, 16.7), (8, 12.5), (10, 10),
                             (12, 8.3), (15, 6.7), (20, 5), (55, 1.8), (161, 0.6)])

# ── RESULT, 25 Aug 2026 ────────────────────────────────────────────────────
#
# MOMENTUM IS NOT FUNDABLE. This is the finding.
#
#   cap   4    6    8   10   12   15   20   55  161
#   mean -1.12 -0.59 -0.32 -0.06 +0.32 +0.50 +0.70 +1.62 +2.25
#
# At every cap you could actually finance, Momentum's mean trade is NEGATIVE.
# It only turns positive at 12 concurrent positions and only reaches its
# headline +2.25% at 161 — which is 0.6% per position, i.e. not a portfolio,
# an index.
#
# The reason is the shape we already knew and had not connected: Momentum's
# median trade LOSES 0.33% and its mean is carried entirely by a minority of
# large winners. Breadth is not a nice-to-have for that shape, it IS the
# strategy. Cap the slots and first-come-first-served hands you an arbitrary
# subset — and an arbitrary subset of a tail-driven distribution is, on
# average, the losing part of it.
#
# **This settles the question the owner actually asked.** "Do I get into XLY
# today" has a definite answer and it is no: taking one Momentum signal, or
# seven, is precisely the losing subset. The screen was never a list of trades.
#
# SWING IS FUNDABLE, AND IT IS NOT A KNIFE EDGE.
#
#   cap        6    8   10   12   15   20   28   62
#   account  81%  87%  86%  86%  85%  91%  85%  44%
#
# Account return is flat across a 5x range of caps, which is the property you
# want — the answer does not depend on picking the number correctly. It falls
# off only at 62, where per-position size drops to 1.6% and most of the capital
# sits idle.
#
# BUT MY UNCERTAINTY RESOLVED THE EXPENSIVE WAY. I wrote that if arbitrary
# dropping barely hurt, the edge was broad and ranking was optional; if it hurt
# a lot, ranking was REQUIRED. Swing's per-trade mean falls from +1.10%
# (take everything) to +0.52% (cap 8) — it loses more than half its quality to
# the choice of WHICH signals to skip. **Ranking is required, not optional.**
# Right now nothing ranks: the live strategy takes signals in whatever order
# the symbol loop reaches them, which is alphabetical, which is arbitrary.
#
# CAVEAT ON THE ACCOUNT COLUMN: it is a simple sum of sized returns over 16
# years, not compounded, and it does not price risk. Cap 4 shows the highest
# total (116%) at 25% per position — one -17.7% trade costs 4.4% of the
# account. Read the column for SHAPE, not as a return forecast, and do not
# read cap 4 as the best answer.
#
# WHAT THIS CHANGES:
#   * Momentum does NOT go into a forward test as a fundable sleeve. It failed
#     the portfolio question before it got there. Keep the screen, label it as
#     market context, stop implying it is a trade list.
#   * Swing needs a concurrency cap. Anywhere in 8-20 is defensible on this
#     evidence; the cap is not the sensitive choice.
#   * The sensitive choice is RANKING, and it is now the priority.
