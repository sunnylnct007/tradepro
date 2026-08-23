"""Does the Swing edge still exist RECENTLY — and how sure can we be?

Owner: "can u not backtest thoroughly with last 2-3 months of data".

The honest answer has two parts. Yes, and the harness already does it. But a
short window is a small SAMPLE, and a win rate measured on 30 trades is not
the same kind of fact as one measured on 1,270. So every window here is
reported with a 95% confidence interval, computed with the Wilson method
(correct for small n, unlike the normal approximation which produces intervals
running past 100%).

READ THE INTERVAL, NOT THE POINT ESTIMATE. If a window's interval contains
50%, that window cannot tell you the strategy beats a coin flip — however good
its headline number looks.

NOT A CLEAN OUT-OF-SAMPLE TEST, and this must not be claimed. The rule's
parameters (2.5 sigma, 20-day mean target, -8% stop) were chosen from a
24-combination sweep over ALL history, so recent data was in the selection
set. This measures REGIME PERSISTENCE — does it still work lately — which is
a different and weaker question than out-of-sample validation.
"""
from __future__ import annotations
import math, sys
import numpy as np
sys.argv = [sys.argv[0]]
exec(open(__file__.replace("_recency", "_v2")).read().split("# ── grading")[0])


def wilson(k, n, z=1.96):
    """95% interval for a proportion. Wilson, not normal approximation —
    at n=30 the normal method happily returns bounds above 100%."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * max(0, c - m), 100 * min(1, c + m))


r = results[("moving", "high")]
a, dates = r["res"], r["dates"]
di = np.array([int(x[:4]) * 10000 + int(x[5:7]) * 100 + int(x[8:10]) for x in dates])

print(f"\n{'='*86}\nDOES IT STILL WORK LATELY? (convention moving/high)\n")
print(f"{'window':<22}{'trades':>8}{'win%':>8}{'95% interval':>18}{'mean%':>9}{'median%':>9}  verdict")
WINDOWS = [("last 1 month", 20260723), ("last 2 months", 20260623),
           ("last 3 months", 20260523), ("last 6 months", 20260223),
           ("last 12 months", 20250823), ("last 24 months", 20240823),
           ("ALL HISTORY", 0)]
for label, cutoff in WINDOWS:
    m = di >= cutoff
    v = a[m]
    if len(v) == 0:
        print(f"{label:<22}{0:>8}   no trades")
        continue
    k = int((v > 0).sum())
    lo, hi = wilson(k, len(v))
    beats_coin = lo > 50
    verdict = ("edge visible" if beats_coin else
               "TOO FEW to tell" if lo <= 50 <= hi else "below a coin flip")
    print(f"{label:<22}{len(v):>8}{100*k/len(v):>7.1f}%{f'{lo:.0f}–{hi:.0f}%':>18}"
          f"{v.mean():>8.2f}%{np.median(v):>8.2f}%  {verdict}")

print(f"\n{'quarter':<12}{'trades':>8}{'win%':>8}{'mean%':>9}")
qs = {}
for pct, dd in zip(a, dates):
    q = f"{dd[:4]}Q{(int(dd[5:7])-1)//3+1}"
    qs.setdefault(q, []).append(pct)
for q in sorted(qs)[-8:]:
    v = np.array(qs[q])
    print(f"{q:<12}{len(v):>8}{100*(v>0).sum()/len(v):>7.1f}%{v.mean():>8.2f}%")

print("\nWhat this is NOT: a clean out-of-sample test. The parameters were chosen")
print("from a 24-combination sweep over all history, so recent data was in the")
print("selection set. This is REGIME PERSISTENCE, a weaker claim.")
