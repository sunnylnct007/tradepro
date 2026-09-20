"""SWING_PUT_OVERLAY_GATES_V1 — sell a weekly put on a swing signal?

Gates frozen in SWING_PUT_OVERLAY_GATES_V1.md before this ran.

ASSIGNMENT IS MEASURED, PREMIUM IS MODELLED, and the two must not be confused.
Whether a put finished in the money is pure price data — no option price is
assumed anywhere. The premium comes from Black-Scholes on trailing realised
vol, because we hold ~25 days of option history and this study spans twenty
years. Every yield here is therefore a CEILING: Saturday's theta study
measured the median put spread at 8.9% of mid, and none of that is deducted.
"""
import math
import statistics as st
import sys

import numpy as np

sys.path.insert(0, ".")
from backtests.studies.swing_out_of_sample_v1 import _load_many, _sma  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as W, MAX_HOLD, SIGMA, STOP_PCT, TREND_WINDOW)
from tradepro_strategies.universe import universe_symbols  # noqa: E402

MAX_DAY_MOVE = 0.35
DTE_CAL = 7                     # a weekly
DTE_SESSIONS = 5                # ~5 trading days in 7 calendar days
STRIKES = (0.00, -0.03, -0.05, -0.08)   # ATM, -3%, -5%, -8% (the swing stop)
RF = 0.045                      # risk-free, roughly the front-end rate

G0_MIN_N, G1_MAX_ASSIGN, G2_MIN_YIELD = 1000, 20.0, 8.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _put_premium(spot, strike, sigma_ann, dte_years):
    """Black-Scholes put. A MODEL, not a quote — see the module docstring."""
    if sigma_ann <= 0 or dte_years <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = ((math.log(spot / strike) + (RF + 0.5 * sigma_ann ** 2) * dte_years)
          / (sigma_ann * math.sqrt(dte_years)))
    d2 = d1 - sigma_ann * math.sqrt(dte_years)
    return strike * math.exp(-RF * dte_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def main() -> int:
    syms = sorted({s.upper() for s in universe_symbols(strict=False)})
    data = _load_many(syms, "traded")

    # results[strike] = list of {assigned, yield_ann, date, stock_ret}
    results = {k: [] for k in STRIKES}
    stock_rets = []

    for sym, (c, h, l, o, d) in sorted(data.items()):
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, W)
            sd = st.pstdev(c[i - W + 1:i + 1])
            if not (sd > 0 and c[i] > _sma(c, i, TREND_WINDOW)):
                i += 1
                continue
            if (c[i] - m) / sd > -SIGMA:
                i += 1
                continue
            spot = c[i]
            exp_i = i + DTE_SESSIONS
            if exp_i >= n:
                break

            # trailing realised vol, annualised — the model's only input
            rets = [math.log(c[k] / c[k - 1]) for k in range(i - 60, i + 1)
                    if c[k - 1] > 0 and abs(c[k] / c[k - 1] - 1) < MAX_DAY_MOVE]
            if len(rets) < 40:
                i += 1
                continue
            vol = st.pstdev(rets) * math.sqrt(252)
            if vol <= 0:
                i += 1
                continue

            for pct in STRIKES:
                strike = spot * (1 + pct)
                prem = _put_premium(spot, strike, vol, DTE_CAL / 365.0)
                if prem is None or prem <= 0:
                    continue
                assigned = c[exp_i] < strike
                # Yield on the COLLATERAL a cash-secured put ties up.
                yld = 100 * (prem / strike) * (365.0 / DTE_CAL)
                results[pct].append({
                    "assigned": assigned, "yield": yld, "date": d[i],
                    # what the stock did over the same week, for G3
                    "fwd": 100 * (c[exp_i] / spot - 1)})

            # the baseline the desk already trades: BUY on this signal
            stop = spot * (1 - STOP_PCT)
            out = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    out = "corrupt"
                    break
                tgt = _sma(c, j, W)
                if l[j] <= stop:
                    out = (100 * (min(stop, o[j]) / spot - 1), j - i)
                    break
                if h[j] >= tgt:
                    out = (100 * (max(tgt, o[j]) / spot - 1), j - i)
                    break
            if out == "corrupt":
                i = j + 1
                continue
            if out is None:
                j = min(n - 1, i + MAX_HOLD)
                out = (100 * (c[j] / spot - 1), j - i)
            stock_rets.append(out[0])
            i += max(1, out[1]) + 1

    print(f"\nbaseline — BUY the stock on the signal: n={len(stock_rets)}  "
          f"mean {np.mean(stock_rets):+.2f}%/trade")
    print(f"\n{'strike':>8}{'n':>7}{'assigned':>11}{'gross ann yield':>17}"
          f"{'mean stock move':>18}")
    for pct in STRIKES:
        rows = results[pct]
        if not rows:
            continue
        a = 100 * sum(1 for r in rows if r["assigned"]) / len(rows)
        y = st.fmean(r["yield"] for r in rows)
        f = st.fmean(r["fwd"] for r in rows)
        print(f"{pct:>+7.0%}{len(rows):>7}{a:>10.1f}%{y:>16.1f}%{f:>17.2f}%")

    key = -0.05
    rows = results[key]
    assigned = [r for r in rows if r["assigned"]]
    a_rate = 100 * len(assigned) / len(rows)
    y_mean = st.fmean(r["yield"] for r in rows)

    # G3: when assignment happens, is it worse than having bought the stock?
    assign_move = st.fmean(r["fwd"] for r in assigned) if assigned else 0.0
    stock_mean = float(np.mean(stock_rets))

    # G4: does the assignment rate hold in both halves?
    dates = sorted(r["date"] for r in rows)
    mid = dates[len(dates) // 2]
    early = [r for r in rows if r["date"] < mid]
    late = [r for r in rows if r["date"] >= mid]
    ea = 100 * sum(1 for r in early if r["assigned"]) / max(len(early), 1)
    la = 100 * sum(1 for r in late if r["assigned"]) / max(len(late), 1)

    print("\n— gates (frozen in SWING_PUT_OVERLAY_GATES_V1.md), judged at −5% —")
    checks = (
        (f"G0 n>={G0_MIN_N}", len(rows) >= G0_MIN_N, len(rows)),
        (f"G1 assigned<={G1_MAX_ASSIGN}%", a_rate <= G1_MAX_ASSIGN, f"{a_rate:.1f}%"),
        (f"G2 gross yield>={G2_MIN_YIELD}%", y_mean >= G2_MIN_YIELD, f"{y_mean:.1f}%"),
        ("G3 assignment no worse than buying", assign_move >= stock_mean,
         f"assigned week {assign_move:+.2f}% vs stock {stock_mean:+.2f}%"),
        ("G4 both halves similar", abs(ea - la) <= 10.0, f"{ea:.1f}% / {la:.1f}%"),
    )
    for label, ok, got in checks:
        print(f"  {label:36s} {'PASS' if ok else 'FAIL'}  ({got})")
    print(f"\nVERDICT: {'BUILD IT' if all(c[1] for c in checks) else 'DO NOT BUILD'}")
    print("\nNOTE: every yield above is GROSS of the bid-ask. The measured "
          "median put spread is 8.9% of mid (THETA_EARLY_CLOSE_GATES_V1), "
          "and crossing it was the largest single cost in that study.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
