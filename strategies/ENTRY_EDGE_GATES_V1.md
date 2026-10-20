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

## Results — run 2026-10-20

                SWING                              MOMENTUM
      h    n  signal%  ctrlA%   vs A  vs SPY    n  signal%  ctrlA%   vs A  vs SPY
      1   48   -0.61    0.14   -0.75   -0.58  103   -0.17    0.11   -0.29   -0.19
      3   26   -2.45    0.31   -2.76   -2.18   94   -0.95    0.33   -1.29   -0.90
      5   18   -0.90    0.44   -1.34   -1.09   71   -1.22    0.58   -1.80   -1.78
     10   13   -0.43    0.70   -1.13   -1.02   50   -1.60    1.22   -2.83   -2.34

     hit rate at h=5:   SWING 33%        MOMENTUM 44%

### G1 — FAILED, both, at every horizon.

Signal forward returns are NEGATIVE while the same symbols' unconditional
forward returns are POSITIVE at every horizon (+0.11% to +1.22%). **These names
drift up in general and the rules buy the moments they do not.** At h = 5 swing
gives up 1.34 points against simply holding the same name at a random time;
momentum gives up 1.80, widening to 2.83 by h = 10.

### G2 — FAILED, both, at every horizon.

Against SPY over identical dates: swing −1.09, momentum −1.78 at h = 5. Not
"fails to beat the index" — materially worse than it.

### G3 — FAILED, both. 33% and 44% positive at h = 5.

Momentum's hit rate RISES to 56% at h = 10 while its mean falls to −1.60%:
more winners, much bigger losers.

### K1 — not triggered, which makes it worse.

Dropping the largest contributor leaves the sign unchanged in both
(swing −0.90 → −1.32 without UNM; momentum −1.22 → −0.91 without SMTC). The
result is not one name. It is the whole population.

## Prediction scorecard

| # | Prediction | Outcome |
|---|---|---|
| 1 | Momentum G1 passes at h=10 | **WRONG** — its worst horizon, −2.83 vs control |
| 2 | Swing G1 passes at h=3/5 | **WRONG** — −2.76 at h=3, its worst |
| 3 | Both fail G2 | **RIGHT** |
| 4 | G3 passes for swing | **WRONG** — 33%, the lowest number in the study |

One of four, and the one I got right was the pessimistic one. Every optimistic
prediction was wrong, and wrong in the same direction.

## Conclusion

**The exits are not the problem, and fixing the stop would not have saved
these rules.** That was the working hypothesis after MOMENTUM_STOP_GATES_V1 —
stop at 1.43 ATR, 86% of stopped names recovering — and it is now refuted. The
recoveries were real, but the entries were already negative before any stop
touched them.

What the numbers say, stated plainly: **both rules select worse-than-random
moments to buy names that otherwise drift up.** A coin flip on the same
universe would have done better, and so would buying SPY.

THE PRE-REGISTERED LIMIT STILL APPLIES, and now cuts the other way. 48 and 110
signals inside one 3½-week regime, overlapping heavily in time, cannot prove an
edge — but the registration said a clear FAIL would be the more informative
outcome, and this is clear: **8 of 8 strategy × horizon combinations negative
against BOTH controls**, with no single name carrying it.

What this does NOT establish: that mean reversion and momentum are bad ideas.
It establishes that THESE implementations, on THIS universe, in THIS regime,
buy at the wrong moment. A −2.25σ entry and a pullback-to-the-10-day entry are
reasonable premises; the measurement says this parameterisation inverts them.

### Recommended

1. **Stop trading both sleeves on paper as configured.** They are not
   generating data worth collecting — every additional trade is measuring a
   known-negative rule.
2. **Keep the archive running.** Signal capture cost nothing, never broke, and
   is what made this answerable in a day.
3. Do NOT retune the stop, the trail or the position cap on this evidence.
   Nothing downstream of the entry can fix an entry that is negative before any
   exit applies.
