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

## RESULT — run 4 Oct 2026. FAILS G1 and G4. The scouting number was mostly drift.

    S1 events (>=8% one-day drop, above 200-SMA, >=$25M/63d liquid): n=3,377

    +5d    raw +1.62%   EXCESS +0.82%   median excess +0.23%   worst -73.3%
    +10d   raw +2.53%   EXCESS +0.91%   median excess +0.19%   worst -78.0%
    +20d   raw +3.79%   EXCESS +0.52%   median excess -0.96%   worst -70.8%

    V0 PASS (3,377) · G2 PASS (+0.56 early / +1.25 late) · G3 PASS (+0.19%)
    G1 FAIL (+0.91% vs +1.50%) · G4 FAIL (-78.0% vs -35%)

    VERDICT: FAILS. No alert ships.

### What the control did to the headline

The scouting pass that motivated this study reported +3.68% at 10 days against
a +0.68% "baseline". Against a PROPER matched control — the same symbol's own
forward return on its other above-200-SMA sessions — the excess collapses to
**+0.91%**. Most of the apparent edge was the drift of the kind of stock that
gets an 8% shock while in an uptrend: high-beta names that rise a lot anyway.
The crude baseline pooled every symbol and therefore compared volatile names
against the universe average. That is the whole gap.

Lesson, more useful than the verdict: **the scouting number was not wrong, it
was the wrong comparison.** A matched control is not a formality.

### G4 is the one that forbids the trade the owner actually wanted

Worst +10d outcome: **-78.0%**. The owner's framing was selling PUTS into
these shocks, which is precisely the position that pays a little when the
bounce happens and is destroyed when it does not. 3,377 events contain names
that fell 8% on news and then fell another 78% in ten sessions. Median excess
is +0.19% — the TYPICAL event is nearly a coin flip — while the tail is
catastrophic and on the wrong side for a put seller.

### Q3 — depth helps the mean and NOT the tail, exactly as predicted

    -8 to -12%       n=2,674   excess +0.69%   worst -62.1%
    -12 to -20%      n=  598   excess +1.68%   worst -44.0%
    worse than -20%  n=  105   excess +1.97%   worst -78.0%

Deeper drops do bounce harder (+0.69% → +1.97%), and the deepest band carries
the worst single outcome in the study. Prediction recorded before the run:
*"monotonic but mild... the worst-case deteriorates faster than the mean
improves, which would argue for a depth CAP as well as a floor."* That is
what happened. A -12 to -20% band would clear G1 on its own at +1.68% — but
it is 598 events over twenty years (one per name per thirty-odd years) and
its worst case is still -44%. Not a lane.

### Q2 — not reached, and that is a finding too

G1/G4 failed on the price signal alone, so splitting by catalyst could only
have rescued it by slicing a failing population into a flattering subset —
which is the multiple-testing trap this discipline exists to avoid. The
catalyst question stays open and un-asked rather than answered badly.

### Consequence (pre-stated, applied)

**No alert ships.** Per the frozen consequences for a G1/G4 failure the rule
is recorded and dropped. The news feed in Settings → Catalysts keeps running
as CONTEXT on existing boards; it does not become a signal.

### What this does NOT say

The owner made money on MDB and WDC and this study does not contradict that.
It says the phenomenon is not a mechanical edge at the size we would trade it:
the average is thin once drift is removed, the median is a coin flip, and the
tail is ruinous for a put seller. The owner's trades added something this test
cannot encode — a judgement about WHICH shock was survivable (a CEO leaving,
a partnership headline) versus a business actually breaking. That judgement is
real and is not a filter we can write today.

### Predictions, scored
- Q1 "PASSES, excess +2.0-2.8%" — **WRONG**. +0.91%, failed G1. I over-trusted
  the scouting gap and under-estimated how much of it was drift.
- G4 "the gate I expect to be closest" — **right**, and it failed outright.
- Q3 "monotonic but mild, tail worsens faster than the mean improves" — **right**.
- Q2 "catalyst adds nothing (~70%)" — **not reached**.


---

# AMENDMENT V2 — does a MARKET-REGIME gate rescue it? Frozen 4 Oct 2026, BEFORE the run.

V1 failed G1 (+0.91% excess) and G4 (−78.0% worst). Inspecting the tail
afterwards showed the disasters are NOT weak companies:

    -78.0 GME 2021-02-01   -73.6 GME 2021-01-28   -63.0 CAR 2026-04-22
    -62.1 FCX 2010-01-21   -50.0 RCL 2020-02-24   -44.3 AAL 2008-11-06

GME through the squeeze unwind; RCL and AAL as travel collapsed in COVID and
the GFC. Royal Caribbean in January 2020 was a sound business until the world
stopped — no fundamentals screen excludes it. A size floor does not help
either: at ≥$500M median dollar volume the worst case is still −63.0%.

**THIS AMENDMENT IS WRITTEN AFTER SEEING THAT TABLE, which is exactly when
pre-registration matters most.** The hypothesis it tests is the obvious one
the table suggests, and the obvious one is also the easiest to curve-fit to
two crises. Hence: frozen here, thresholds unchanged from V1, and a
pre-stated kill condition.

## The change (S2)

S1, plus one condition evaluated on the SHOCK DATE:

    SPY close > SPY 200-day SMA        (the market itself is not broken)

Nothing else moves. Same drop threshold, same liquidity floor, same horizons,
same matched control, same gates. No fundamentals — none are needed and none
are available point-in-time.

## Gates — UNCHANGED from V1, deliberately

V0 n≥1,000 · G1 +10d excess ≥ +1.50% · G2 both halves positive ·
G3 median excess > 0 · G4 worst +10d ≥ −35%

Loosening a gate to let a favoured idea through is how a desk lies to itself.
If S2 needs an easier bar than S1 was held to, it has not earned anything.

## The kill condition (pre-stated, and the point of this amendment)

A regime gate is suspicious precisely because it removes 2008 and 2020 — the
periods containing the losses. So S2 is adopted ONLY if it passes the gates
**and** survives this check:

**K1 — the gate must do more than delete two crises.** Excluding 2008-2009 and
2020 entirely from the V1 population must NOT, on its own, already clear G4.
If removing those two windows gets V1 to a worst case ≥ −35% without any SPY
condition, then S2's apparent power is just "the crises are gone", the rule
has learned nothing transferable, and it is REJECTED however good the headline
looks.

## Predictions (recorded before the run)

- **G4 clears, around −40% to −50% worst case — so still FAILS.** The SPY
  filter removes the GFC and COVID clusters but GME 2021 happened with SPY
  comfortably above its 200-SMA, and that is the −78% event. I give S2 passing
  all gates about **25%**.
- **G1 improves to roughly +1.2–1.6%** — borderline on the +1.50% bar.
- **K1 is the real risk and I expect it to BITE.** My honest expectation is
  that crude 2008/2020 exclusion gets most of the way to the same place, which
  would mean the SPY gate is a proxy for "skip the crises" rather than a
  mechanism. If so: rejected.
- Net: I expect this amendment to FAIL, and I am running it because the
  owner's objection deserves a measurement rather than my opinion.

## Consequences, pre-stated

- Passes gates AND survives K1 → a genuinely conditioned rule; proceed to a
  separate sizing/alert study. Still never an auto-placed order.
- Passes gates but fails K1 → REJECTED and recorded as curve-fitting.
- Fails any gate → V1's verdict stands unchanged; the phenomenon stays a
  manual judgement call and the file is closed for good.

## RESULT V2 — run 4 Oct 2026. FAILS, and worse than V1 on the measure that mattered.

                                     n     excess  median    worst   halves
    V1 (all)                       3,377   +0.91%  +0.19%   -78.0%  +0.56/+1.25
    S2: SPY > its own 200-SMA      2,514   +0.85%  +0.06%   -78.0%  +0.76/+0.94
    K1: V1 minus 2008-09 & 2020H1  2,946   +1.21%  +0.24%   -78.0%  +0.83/+1.58

    G1 FAIL (+0.85% vs +1.50%) · G4 FAIL (-78.0% vs -35%) · G3 marginal (+0.06%)

    VERDICT: FAILS. The file is closed.

### The regime gate made it WORSE

I predicted G1 would improve to +1.2-1.6%. It fell: **+0.91% -> +0.85%**, and
the median excess collapsed to **+0.06%** — the typical event became an exact
coin flip. Removing 863 events removed good ones too. The SPY filter is not
selecting for survivable shocks; it is just selecting for calm markets, where
the bounce is smaller because the fear that creates it is absent.

### G4 did not move AT ALL, under either treatment

Worst case stayed **-78.0%** in V1, in S2, and in K1. That is GME on
2021-02-01 — with SPY comfortably above its 200-SMA, in no crisis, in a
liquid mega-volume name. Exactly the event I named in the prediction as the
reason the gate could not work. **No market-regime condition can exclude it,
because the market was fine; the stock was not.**

### K1 — the kill condition, and what it reveals

Crude deletion of 2008-09 and 2020H1 produced a BETTER headline (+1.21%) than
the principled SPY gate (+0.85%). So the only thing that improves this rule is
removing specific historical periods after the fact — which is curve-fitting
by definition, and which is what K1 existed to catch. Per the pre-stated
consequence, that is a rejection regardless of the number.

Note it still did not clear G4 either: -78.0%. Even the curve-fit does not
make the trade safe.

### Predictions, scored
- "G4 clears to -40/-50%, still fails" — **WRONG**: G4 did not move at all.
- "G1 improves to +1.2-1.6%" — **WRONG**: it got worse (+0.85%).
- "~25% chance of passing all gates" — right to be pessimistic, for the wrong
  reason: I expected partial improvement, and got none.
- "K1 will bite" — **RIGHT**, and it bit harder than expected: crude deletion
  beat the principled gate outright.

### Consequence (pre-stated, applied)

**V1's verdict stands. The file is CLOSED for good.** The news-shock bounce is
not a mechanical edge at the size we would trade it, in any market regime, at
any liquidity floor, with or without the crises.

What survives is the owner's original framing, unchanged by three attempts to
systematise it: this needs a human judging whether a particular business is
broken or merely bruised. The desk will not alert on it, and the catalyst feed
remains context on existing boards rather than a signal.

