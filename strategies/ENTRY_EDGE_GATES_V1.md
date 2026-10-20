# Do the ENTRIES have edge, independent of the exits?

**Pre-registered 2026-10-20, BEFORE the study ran.** Gates, kill condition and
the prediction were committed first. Results appended verbatim, wrong
predictions included.

## The question

Replayed from the signal archive, both sleeves lose — and both lose the same
way:

    SWING     14 closed   win 57%   avg win +3.57%   avg loss -9.47%   needs 73%
    MOMENTUM  40 closed   win  5%   avg win +4.06%   avg loss -7.29%   needs 64%

**Losses are 2-3x the wins.** That is geometry, not luck: swing targets the
20-day mean (~+4%) against an -8% stop, so by construction it needs ~73% of
trades right. Momentum is the same shape.

So the measured failure is of the EXIT, and the entry has never been tested on
its own. Two pieces of evidence say that distinction matters:

  * MOMENTUM_STOP_GATES_V1 measured the stop at **1.43 ATR** — inside the daily
    noise of the names it picks — and found **86% of stopped positions traded
    back above their exit within days**. Those were not broken theses.
  * Owner, 20 Oct: *"mean reversion and momentum were some good price action"*.

If the entries have edge, the exits are the problem and are fixable. If they
do not, the rules are finished and no amount of execution work matters.

## Method

Every FIRST-APPEARANCE signal in the archive (swing 48, momentum 110; signal
bars 25 Sep – 19 Oct 2026), entered at the signal-bar close. **No stop, no
target, no trail.** Forward return measured at h ∈ {1, 3, 5, 10} trading bars.
Bars from the golden store via `daily_bars.load_daily(check_poison=True)`.

h = 20 is deliberately NOT a gate: the archive is 3½ weeks old, so almost no
signal has 20 bars of forward data. `n` is reported per horizon and any horizon
with n < 20 is reported but not gated.

Two controls, because a raw positive number proves nothing in a rising market:

- **Control A — the same symbol, unconditionally.** Mean forward return at the
  same h across that symbol's whole available history. Strips out "we picked
  names that drift up".
- **Control B — SPY over the identical dates.** Strips out the regime. The S&P
  rose over this window; a long-only rule that merely tracks it has found
  nothing.

## Gates

Judged at **h = 5** (the horizon with usable n for both sleeves).

- **G1 — beats the symbol's own baseline.** Mean signal forward return must
  exceed Control A. FAIL ⇒ the entry rule adds nothing over holding the same
  names at random times.
- **G2 — beats the market.** Mean signal forward return must exceed Control B
  (excess > 0). FAIL ⇒ the rule does not beat being long the index, and is not
  worth running as a stock-selection rule.
- **G3 — more right than wrong.** Fraction of signals with positive forward
  return at h = 5 must exceed 50%.

G1 and G2 are the ones that matter. G3 is reported because a rule can have a
positive mean on a few large winners, which is a different animal.

## Kill condition (K1)

If a gate passes but **removing the single largest-contributing symbol flips
the sign** of the h = 5 mean, the result is REJECTED. 48 and 110 signals are
small enough for one name to carry the whole number, and this desk has
curve-fit on exactly that before.

## What this study CANNOT do

Every signal was entered inside one ~3½-week regime, in which the S&P rose
0.80% with a worst drawdown of 2.14%. Signals also overlap heavily in time —
12 of 16 momentum exits once landed on two days — so these are NOT independent
observations. A pass here is **indicative, not evidence of edge**; a clear fail
is more informative than a marginal pass. Stated now so a pass cannot later be
quoted as proof.

## Prediction — written before running

1. **Momentum G1 PASSES at h = 10, marginal at h = 5.** The 86% recovery
   finding says the entries are not the problem; a pullback-in-uptrend rule
   should need more than five bars to work.
2. **Swing G1 PASSES at h = 3 and h = 5, fading by h = 10.** Mean reversion is
   a short-horizon effect and should decay.
3. **BOTH FAIL G2.** I expect neither to beat SPY over the same dates. If that
   is right, the honest conclusion is that these are not stock-selection edges
   — they are long exposure with extra steps — and the answer is not a better
   stop.
4. **G3 passes for swing (>50%), fails for momentum.**

If 1 and 2 pass while 3 fails, the rules are salvageable as *timing* overlays
but not as *selection* rules, and the exits are worth fixing. If G1 fails for
both, the sleeves should be stopped regardless of the exit geometry.

## Results

*(appended after the run)*
