# Momentum's stop — is it inside the noise of what it trades?

**Pre-registered 2026-10-14, BEFORE the study ran.** Thresholds, gates, kill
condition and the prediction were committed first. Results appended verbatim,
wrong predictions included.

## The question

Momentum's published signals, replayed: **16 closed, 1 win, −$2,861**. Needs a
47% win rate to break even, delivering 6%.

That looks fatal and probably is not, because the trades are not independent:

    2026-10-08   7 exits
    2026-10-12   5 exits
    other         4 exits

**Twelve of sixteen exits landed on two days.** The sleeve holds correlated
high-beta names, so one session stops out the book. That is four or five
events, not sixteen — nowhere near enough to condemn a rule. (An earlier
"0 for 11, a 1-in-700 event" claim assumed independence that is not there.)

What makes it worth investigating rather than shrugging off: **the S&P rose
+0.80% over the window**, worst drawdown −2.14%. Momentum lost 15 of 16 into a
mildly RISING market. That is not the market taking everything down.

So the suspicion is the exit, not the entry. `STOP_PCT = 0.08` and
`TRAIL_PCT = 0.08` are FIXED percentages (`cli/momentum_candidates.py:51-52`),
applied to a universe of semis and high-beta growth. A fixed 8% means something
very different on a 1.5%-ATR name than on a 4%-ATR name. If 8% sits inside
these instruments' ordinary daily range, a routine −2% index day stops the
whole sleeve on noise.

**This study does not propose a new stop.** It measures whether the current one
is inside the noise, and whether the stopped positions were right to be closed.

## Method

The 16 closed momentum trades from the signal archive (first appearance only),
each with its entry date, entry price and exit, from `signal_replay`. Bars from
the golden store via `daily_bars.load_daily` with `check_poison=True`.

Per trade:
- **ATR%** — ATR(14) at the entry bar, as a percentage of the entry close.
  True range, not close-to-close, so gaps count.
- **stop in ATR** — `8.0 / ATR%`. How many ordinary days' range the stop sits
  away from entry.
- for STOPPED trades, **recovery** — did the close get back above the EXIT
  price within 10 sessions after the exit?

## Gates

- **G1 — is the stop inside the noise?** Median `stop in ATR` must be **≥ 2.0**.
  Below 2.0, an 8% stop is inside two ordinary days of range for these names
  and will be hit by noise alone. FAIL ⇒ the stop is mis-scaled for the
  universe.
- **G2 — did the stops fire on noise?** Of the stopped trades, **< 60%** may
  close back above their EXIT price within 10 sessions. At 60% or more, the
  exits were mostly premature: the thesis was not broken, the stop was just in
  the way. FAIL ⇒ the stop is firing on noise.
- **G3 — would scaling it actually help?** Re-run the SAME signals through the
  SAME rule with the hard stop and trail set to **3 × ATR%** (floor 8%, cap
  20%) instead of a flat 8%. To pass, BOTH:
  (a) total realised return must improve, and
  (b) the worst single trade must not be worse than **−20%**.
  A change that only buys return by widening the tail is not an improvement.

G1 and G2 diagnose. G3 is the one that decides whether anything should change —
a stop can be "inside the noise" and still be the best available choice.

## Kill condition (K1)

If G3 passes but **more than 50% of the improvement comes from a single
symbol**, the result is REJECTED. One name recovering hard is not a rule. This
desk has curve-fit on exactly that before, which is why it is written down
before the numbers exist.

## Prediction — written before running

1. **G1 PASSES, narrowly.** Semis and high-beta growth run roughly 2.5–3.5%
   daily ATR, so 8% is about 2.3–3.2 ATR. My own "the stop is inside the noise"
   hypothesis from this morning is probably WRONG on the plain reading, and I
   am recording that before I can quietly drop it.
2. **G2 FAILS** — I expect ≥60% of stopped names to recover above their exit
   within 10 sessions. The clustering on two days, into a rising market, looks
   like a correlated shake-out rather than sixteen broken theses.
3. **G3 PASSES on (a) and FAILS on (b)** — a 3×ATR stop should improve the
   total, but on these names it reaches 10–12%, and a gap through it produces a
   worse than −20% trade. If so the honest answer is NOT "widen the stop" but
   "this universe needs smaller positions", which is a sizing decision for the
   owner, not a tuning knob for me.

If 1 and 2 both hold, they are in tension — a stop that is NOT inside the
noise, yet firing on noise — and the resolution would be that the problem is
CORRELATION (one event hitting every position) rather than stop width. That
would point at position-count limits per sector, not at the stop. Stated now so
that conclusion cannot be invented afterwards.

## Results

*(appended after the run)*
