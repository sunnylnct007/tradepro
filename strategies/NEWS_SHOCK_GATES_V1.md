# NEWS SHOCK GATES V1 — is the capitulation bounce real, and does the CATALYST add anything?

3 Oct 2026. Owner, describing trades made with his own money:

> *"manually i have been booking options and making money. e.g. yesterday MDB
> went down significantly due to news, I sold the put and MDB normalized
> giving me a decent uplift"* · *"same with WDC which went more than 10% due
> to news and then recovered slightly"* · *"we are missing these kind of
> things alerting"*

Both were NEWS shocks, not earnings: **MDB's CEO left for Meta**; **a Toshiba
collaboration hit WDC**. The existing gated rule (`POST_EARNINGS_PUT_GATES_V1`,
V2, all eight gates passed) would have caught NEITHER — it requires an
earnings event. So this is a genuinely new question, not a re-run.

Frozen BEFORE the run, per house discipline.

## What is already known (not a gate, context)

A scouting pass on 3 Oct over 969 names, 2006–2026, measured the RAW price
phenomenon with no news filter at all:

    one-day drop >= 8% while still above the 200-SMA     n = 4,631
      +5d   mean +2.42%   win 55.0%
      +10d  mean +3.68%   win 55.6%
      +20d  mean +5.54%   win 54.8%
    BASELINE (any bar above 200-SMA, +10d)  mean +0.68%  win 55.1%

The bounce is ~5x baseline drift at 10 days and the win rate is IDENTICAL to
baseline — the edge is in the SIZE of the move, not its frequency. That is
the shape that makes short puts pay. This scouting number is why the study is
worth running; it is not itself a gated result, and it is not re-used as one.

Frequency: ~4,631 events / 969 names / 20 years ≈ **one per name every four
years**. Too rare to watch by eye — which is the argument for an alert.

## The rule under test (S1)

On any session, flag a symbol when:

    close <= 0.92 x previous close        (a one-day drop of >= 8%)
    AND close > 200-SMA                   (the trend is still intact)
    AND 63-day median dollar volume >= $25M   (point-in-time, from bars)

Horizon measured: +5, +10, +20 sessions, close-to-close, no options modelled.

## The questions

**Q1 — does the raw shock beat baseline on a GATED footing?** Same 4,631-event
population, but graded properly against a matched control: for every event,
the same symbol's average forward return over the same horizon across all
other sessions above its 200-SMA. Reported as EXCESS over that control, so
"stocks drift up" cannot masquerade as an edge.

**Q2 — does the CATALYST add anything over the price shock?** This is the
question the owner's framing assumes and nobody has tested. Split events by
whether a catalyst row (SEC 8-K / news extractor / GDELT — the feed already
running in Settings → Catalysts) exists for that symbol within ±1 session.
If catalyst-matched events do not beat unmatched ones, the alert should fire
on PRICE ALONE and the news feed is decoration.

**Q3 — depth bands.** Does a −8% drop behave like a −15% drop? Bands named
now: −8 to −12%, −12 to −20%, worse than −20%. A monotonic "deeper = bigger
bounce" would be a different rule from "any shock bounces".

## Gates — the alert ships only if ALL hold

- **V0** n >= 1,000 events after the liquidity floor
- **G1** +10d EXCESS over the matched control >= **+1.50%**
- **G2** the excess holds in BOTH halves of the period (no 2008/2020 artifact)
- **G3** median +10d excess > 0 — the TYPICAL event bounces, not just the mean
      dragged by a few monsters
- **G4** worst +10d outcome >= **−35%**. A short put against a name that keeps
      falling is the loss that matters, and the owner would be selling puts
      into exactly this.
- **G5** (Q2) catalyst-matched excess beats unmatched by >= 0.50%, OR the
      verdict is explicitly "price alone is the signal"

## Predictions (recorded before the run)

- **Q1 PASSES.** The scouting gap (+3.68% vs +0.68%) is far too large to be
  entirely control drift. I expect +10d excess around +2.0–2.8%.
- **Q2: the catalyst adds NOTHING measurable, and I give that ~70%.** An 8%
  one-day drop in a liquid large cap is ALWAYS caused by news; the price move
  IS the news detector. The catalyst feed will mostly confirm what the price
  already said, and its coverage (GDELT/8-K) will be patchy enough that
  "unmatched" mostly means "we missed the article", not "no news".
  **If so, the alert fires on price and the feed becomes context on the row.**
- **Q3: monotonic but mild.** Deeper drops bounce more in percentage terms
  and also have fatter left tails; I expect the worst-case to deteriorate
  faster than the mean improves, which would argue for a depth CAP as well
  as a floor.
- **G4 is the gate I expect to be closest.** Capitulation names that keep
  falling are the whole risk of this trade.

## Consequences, pre-stated

- **All gates pass** → build the alert: daily scan, fires on S1, email names
  the symbol, depth, trend state, any matched catalyst, and the measured
  base rate. It is an ALERT (a candidate for manual judgement), never an
  auto-placed order — consistent with the desk's product definition.
- **G1 or G3 fails** → the bounce is drift or tail-driven; no alert, record it.
- **G4 fails** → the alert may still ship but must carry the measured worst
  case on every row, and must NOT be described as a put-selling signal.
- **G5 "price alone"** → the alert does not depend on the news feed, and we
  stop treating catalyst coverage as a blocker for shipping it.
- Nothing here changes swing, momentum, wheel or strangle.

## RESULT — (to be filled by the run, verbatim)
