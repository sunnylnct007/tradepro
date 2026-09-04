# Earnings strategies — PRE-REGISTERED gates, v2

**Committed BEFORE any run**, 4 Sep 2026. Supersedes `EARNINGS_BOUNCE_GATES_V1.md`
(28 Aug), which was pre-registered and **never run** — no results were seen, so
carrying its questions forward with bars UNCHANGED is legitimate.

## Why there is a v2

Two things happened since v1.

**The owner traded the missing shape, twice, profitably, with real money.**
DELL: bought ~420 into the pre-print sell-off, sold ~465 on the post-print move
(~+10.7%). SNOW: same shape around its 2 Sep print. Neither was flagged by any
screen — the desk's rules treat "earnings in window" as a reason to REFUSE, so
the one setup the owner actually trades is the one thing every strategy here
excludes. v1 studied the post-print bounce; it never asked about entering
BEFORE the print. That question is now Q3.

**The coverage limit that crippled v1 is gone.** v1 had 5,062 events, 7 of them
pre-2020 — a time split inside one regime. The 2 Sep backfill added 15,996
historical report dates (yfinance, `source=yfinance_hist`, median 98/name,
91-day median gap, back to ~2015). The two-split test can now put a genuinely
different regime in each half.

## Data contract for the run

* Report dates: `earnings_calendar`, BOTH sources, deduped per (symbol, date).
  Every result table states the event count and per-source split it ran on.
* Bars: the settled daily bar store only — the same bars every other study used.
* Universe: the committed tradeable list MINUS the 39 account-barred ETFs
  (they have no earnings; they must also never appear as placebo names).
* Costs: 10 bps per side on every arm, including placebo arms.

### The alignment hazard, named before it can flatter anything

`yfinance_hist` rows carry `session=None` — we do NOT know if a print was
before the open (BMO) or after the close (AMC). Get that wrong and the
post-print move leaks into the "pre-print" window: look-ahead that manufactures
edge. Therefore:

* **Conservative alignment is the graded run**: assume AMC — the print lands
  between the close of day T and the open of T+1. Entry windows end at the
  close of T; exit windows start at the open of T+1.
* **A3 (below) requires sign-stability under ±1-session shift.** An edge that
  appears only under one alignment guess is an artefact, not an edge.

## Q1 + Q2 — carried from v1, bars unchanged

Q1: split the live swing rule's trades into earnings-driven vs not, compare.
Q2: looser entry that fires ONLY on an earnings drop (≥5% on an earnings
session, above the 200-SMA), same target/stop/cap as swing.

| # | Test | Threshold |
|---|------|-----------|
| V0 | Trades | ≥ 300 |
| E1 | Win rate | ≥ 55% |
| E2 | Mean net return per trade | > 0 |
| E3 | Beats the comparison arm on mean/trade | by ≥ 0.20pt |
| E4 | Two-split (time × symbol) | positive in ALL FOUR cells |
| E5 | Worst single trade | ≥ −25% |

## Q3 — the owner's shape: buy weakness INTO the print, exit on the move

**Entry (all conditions, no discretion):** at the close of session T−1, where
T is the first session whose OPEN follows the print under the conservative
alignment:
1. close(T−1) ≤ close(T−6) × 0.96 — fell ≥ 4% over the five sessions into the print;
2. close(T−1) > its own 200-day SMA — the only quality proxy this repo has,
   and a crude one: this tests "in an uptrend", NOT "fundamentally sound".
   v1 said the same and it stays said;
3. long only — the trader spec is long/flat, never short.

**Exit:** the close of T+1. No stop is modelled through the print — an earnings
gap blows through a stop and fills at the open, so pretending a stop limits the
tail would flatter the record. The tail is graded raw, where it can fail a gate.

**The comparison arm (this is the whole question):** the identical entry
(≥4%/5-session fall, above the 200-SMA) on dates at least 10 sessions from any
known print for that symbol, exit two sessions later. The desk ALREADY owns a
dip-buyer (swing). Q3 ships only if the PRINT adds edge beyond generic
dip-buying — otherwise it is swing with extra tail risk.

| # | Test | Threshold |
|---|------|-----------|
| P0 | Qualifying events | ≥ 400, across ≥ 80 symbols |
| P1 | Mean net return per trade | ≥ +0.75% |
| P2 | Beats the no-print comparison arm on mean/trade | by ≥ 0.50pt |
| P3 | Win rate | ≥ 55% |
| P4 | 5th-percentile trade | ≥ −12% |
| P5 | Worst single trade | ≥ −35% |
| P6 | Two-split (time × symbol) | positive in ALL FOUR cells |
| A3 | Alignment: mean/trade stays positive under ±1-session shift | both shifts |

P2 and P6 are the gates that matter. P2 is why this is not just swing again;
P6 is the gate that has killed six candidates. A3 exists because the data
cannot tell BMO from AMC and look-ahead is the failure mode of every earnings
study ever quietly shipped.

**What this strategy would be if it passed:** the deliberate taking of exactly
the risk every other rule here refuses — holding THROUGH a print. That is why
P4/P5 are graded raw with no modelled stop, and why a pass ships at tier
`thin` (size accordingly), not `gated`, until it has a live paper record.

## Predictions — recorded before the run, gates frozen

* **Q1**: unchanged from v1 — I expect earnings-driven drops to bounce WORSE.
  An earnings drop is information; a no-news 2.5σ drop is more likely noise.
* **Q2**: fails V0 or E1 — a 5% earnings drop admits names in genuine decline.
* **Q3**: P0 passes easily. P1 marginally passes. **P2 fails in at least one
  two-split cell** — I expect pre-print weakness to be mostly generic dip
  behaviour, with the print adding variance rather than edge. Overall: under
  40% that Q3 ships. The owner's two wins are consistent with both "the edge
  is real" and "two good coin flips"; that is precisely what the 400-event
  sample settles.

If Q3 PASSES cleanly, I am wrong in an interesting way: it means the market
systematically over-punishes uptrending names into their prints, which is a
documented anomaly (pre-earnings drift) and would be the first strategy here
born from the owner's own trading rather than from a screen.

**No threshold above moves after the numbers are seen.** If P2 comes in at
0.49pt, it fails, and the write-up says so.
