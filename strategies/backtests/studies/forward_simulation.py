"""Forward simulation of the 12-week paper window, from the real trade record.

Owner: "can we not run forward simulation on this". Yes, and it converts
"expect it to look bad at some point" from a caveat into a probability.

TWO METHODS, because the difference between them IS the finding:

  IID bootstrap    resample individual trades independently. Assumes losses do
                   not cluster — which is FALSE. 2022 lost across the whole
                   year because bad conditions persist for months.
  BLOCK bootstrap  resample contiguous runs of trades in date order, so a bad
                   patch is drawn whole. Wider, more honest tails.

Quoting the IID number would understate the chance of a losing quarter by a
factor of four (5% against 21%). That gap is the entire reason both are shown.

Sizing matches the live daemon: 5% of capital per position, $100k start.
Run: strategies/.venv/bin/python backtests/studies/forward_simulation.py
"""
import numpy as np
from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals.mean_reversion import (
    entry_signal, MAX_HOLD, BB_WINDOW, STOP_PCT)

PER_WEEK, WEEKS, SIZE, SIMS = 7.0, 12, 0.05, 20000


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def trade_sequence():
    """Every historical trade, IN DATE ORDER — the order matters, because the
    block bootstrap resamples contiguous runs to preserve clustering."""
    seq = []
    for s in universe_symbols():
        df = _load(s)
        if df is None or "open" not in df.columns:
            continue
        c = df["close"].tolist()
        v = df["volume"].tolist() if "volume" in df.columns else None
        if not poison_check(c, v)[0]:
            continue
        o = df["open"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
        d = [str(x)[:10] for x in df.index]
        n = len(c); i = 210
        while i < n - 2:
            if not entry_signal(c, i):
                i += 1; continue
            e = o[i + 1]; stop = e * (1 - STOP_PCT); r = None
            for j in range(i + 1, min(n - 1, i + MAX_HOLD) + 1):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > 0.35:
                    break
                tgt = sma(c, j, BB_WINDOW)
                if l[j] <= stop:
                    r = 100 * (min(stop, o[j]) / e - 1); break
                if h[j] >= tgt:
                    r = 100 * (max(tgt, o[j]) / e - 1); break
            if r is None:
                j = min(n - 1, i + MAX_HOLD); r = 100 * (c[j] / e - 1)
            seq.append((d[i], r)); i = j + 1
    seq.sort()
    return np.array([x[1] for x in seq])


def simulate(R, block, n_trades, rng):
    outs, dds = [], []
    for _ in range(SIMS):
        if block == 1:
            picks = rng.choice(R, n_trades, replace=True)
        else:
            picks = []
            while len(picks) < n_trades:
                st = rng.integers(0, len(R) - block)
                picks.extend(R[st:st + block])
            picks = np.array(picks[:n_trades])
        eq = np.cumprod(1 + picks * SIZE / 100)
        outs.append(100 * (eq[-1] - 1))
        peak = np.maximum.accumulate(eq)
        dds.append(100 * ((eq - peak) / peak).min())
    return np.array(outs), np.array(dds)


def main() -> int:
    R = trade_sequence()
    n = int(PER_WEEK * WEEKS)
    rng = np.random.default_rng(20260824)
    print(f"{len(R):,} historical trades · mean {R.mean():+.2f}% · win {100*(R>0).mean():.1f}%")
    print(f"{WEEKS} weeks = ~{n} trades at {SIZE:.0%} each\n")
    for label, blk in (("IID (losses independent — OPTIMISTIC)", 1),
                       ("BLOCK of 20 (losses cluster — realistic)", 20)):
        o, dd = simulate(R, blk, n, rng)
        print(label)
        print(f"   return   5th {np.percentile(o,5):+6.1f}%  median {np.median(o):+6.1f}%"
              f"  95th {np.percentile(o,95):+6.1f}%")
        print(f"   drawdown median {np.median(dd):.1f}%  5th-worst {np.percentile(dd,5):.1f}%")
        print(f"   P(loses money) {100*(o<0).mean():.0f}%   P(down >5%) {100*(o<-5).mean():.0f}%\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
