# Funding the combined book — PRE-REGISTERED gates, v1

**Committed 6 Sep 2026, before any funding decision.** Owner, 5 Sep: fund the
index strangle AND swing together — *"swng fires very few timnes anyways"*.
Correct, and this doc holds the decision to a record instead of a feeling.

Funding is the event every deferred tripwire named: the short-vol
concentration review, YELLOW sizing, real money. So this doc is three things
at once: the evidence bars, the tripwire review, and the owner checklist.

## The book being funded

* **Index strangle** — short vol, systematic, cash-settled European index
  options, vol-gated per market (thresholds computed, not chosen).
* **Swing (mean_reversion_swing)** — long equity, sporadic (~31% of sessions),
  LIMIT entries capped at signal × 1.015.

Complementary by construction: a long-equity sleeve beside a short-vol book
reduces the all-short-vol concentration flagged on 31 Aug rather than adding
to it.

## Owner decisions that BLOCK funding (no metric can substitute)

| # | Decision | State |
|---|---|---|
| D1 | **The funding figure.** Sets paper NAV, swing `--capital-usd`, strangle sizing. All gates below are scale-free so this number can be set without moving them. | **CLOSED 16 Sep 2026 — $150,000** |
| D2 | **YELLOW regime sizing.** Parked 31 Aug: YELLOW permits a short put at "reduced size" and nothing defines the reduction. Real money may not trade an undefined rule. | OPEN — but NOT a blocker for this book, see below |
| D3 | **Paper realignment timing.** Changing IBKR paper NAV usually means a paper account RESET — it wipes the open book. Done deliberately at a clean point, record snapshotted first. | **CLOSED 16 Sep 2026 — no realignment needed** |

### D1 — $150,000, and why that number (owner, 16 Sep 2026)

Derived from the gates below using **observed** credits, not modelled ones
(see the `credit_modelled` defect note further down — it is why this was not
settled on 6 Sep).

    S5  one 8.8x-credit loss day <= 10% of NAV
        observed XSP monthly credit 1,365-1,490; take the larger
        8.8 x 1,490 = 13,112  ->  NAV >= 131,120
        13,112 / 150,000 = 8.7%                                    PASS

    B1  combined open book stress <= 35% of NAV
        XSP  1 contract =  9,000 =  6.0% of NAV                    PASS
        SPX  1 contract = 90,420 = 60.3% of NAV                    FAIL

The SPX line is the check that the number is RIGHT, not a problem: it fails
B1 decisively, which is exactly the "XSP scale first, SPX only after 10 clean
XSP cycles" rule this doc already carries. A NAV that let SPX through would be
too large.

### D3 closes because of the value D1 took

D3 exists only because changing paper NAV forces an IBKR paper reset that wipes
the open book. $150,000 is what the paper account already holds, so there is
**no realignment, no reset, and no snapshot to take**. Had D1 landed materially
away from the standing balance, D3 would still be open.

### D2 is NOT on this book's critical path — checked, not assumed

`YELLOW` appears in exactly three files: `cli/options_screen.py`,
`quant_engine/options/wheel_backtest.py`, `quant_engine/options/risk.py`. All
wheel/puts. **Neither funded sleeve — index strangle or swing — reads it.**
D2 therefore blocks the WHEEL (already DO-NOT-FUND on its own v3 backtest),
not this funding decision. Left OPEN because striking it from the doc is the
owner's call, not the implementer's; it is simply not blocking.

## The evidence window

Starts when BOTH hold: failure-visibility is deployed (done — 5dfa6f7,
5 Sep: every placement failure now lands on the desk row in the broker's own
words) AND paper NAV equals the D1 figure. **Ends no earlier than 6 calendar
weeks later.** Sleeves are graded independently and may fund separately.

## B — combined-book gates (graded over the whole window)

| # | Test | Threshold |
|---|------|-----------|
| B1 | Stress test of the combined open book | ≤ 35% of NAV at ALL times |
| B2 | Positions the system could not close (the 31 Aug class) | exactly 0 |
| B3 | Failures discovered only in logs, not on the desk | exactly 0 |

## S — strangle sleeve. RELIABILITY BEFORE PERFORMANCE.

The first live week's placements were MAJORITY failures (contract resolution
on SPY/QQQ/GOLD, an NDX rejection, SPX orders cancelled), and none of it was
visible outside the Lambda logs. A performance record built on top of that
would be a coin toss wearing a lab coat — so the reliability gates come first
and their clock starts only at 5dfa6f7.

| # | Test | Threshold |
|---|------|-----------|
| S1 | Consecutive cycles placed AND closed by the system, zero silent failures | ≥ 10 |
| S2 | Completed cycles / distinct markets / elapsed weeks | ≥ 12 / ≥ 3 / ≥ 6 |
| S3 | Median credit received vs modelled credit | ≥ 70% — the number no backtest can give |
| S4 | Win rate and mean cycle P&L | ≥ 65% and > 0 |
| S5 | Sizing math, written down: one 8.8×-credit loss day (the modelled worst) | NAV drawdown ≤ 10% |

### SHADOW CYCLES COUNT FOR THE RELIABILITY GATES — owner, 16 Sep 2026

**The problem this answers.** S1 wants ≥10 placed-and-closed cycles and S2 wants
≥12 across ≥3 markets. Measured over the 40 sessions to 16 Sep, the vol gate
declined **26 of 26** US sessions on every market — VIX ran 14.3–17.8 against a
13.5 threshold. That is the gate working exactly as designed, not a fault. But
it means the evidence window can run its full six weeks with **S1 still reading
zero**, and the sleeve would be no closer to funding than on day one.

**The ruling.** S1, S2, S3, B2 and B3 may be satisfied by **shadow** cycles.
S4 may NOT.

**Why the split is principled and not a convenience.** Read what each gate
actually tests:

| gate | what it measures | needs a gate-approved trade? |
|---|---|---|
| S1 | can the system place AND close without silent failure | no — plumbing |
| S2 | does it do so repeatedly, across markets, over time | no — plumbing |
| S3 | does the credit received match the credit modelled | no — pricing |
| B2 | were there positions the system could not close | no — plumbing |
| B3 | did any failure hide in the logs instead of the desk | no — observability |
| **S4** | **win rate and mean P&L** | **YES — this one is the edge** |

A shadow fill is a REAL paper fill at a REAL price, placed and closed through
the identical code path; it is tagged `shadow: true` and never blended with
signal fills. For everything except S4 the gate cannot tell the difference and
should not try. S4 is the only gate asking "does this strategy make money", and
grading it on days the strategy refused to trade would answer a question nobody
asked.

**The constraint that keeps this honest.** Shadow cycles are days the gate said
STAND ASIDE. Counting them toward S4 would invert the strategy. The populations
are already tagged at the source (`place_paper`, `record_execution`), so the
split is enforced by data, not by discipline.

**Standing evidence at the time of the ruling**, so it is not re-derived later:
17 closed shadow pairs, +$191.54 total — SPX 6 pairs +207.47, XSP 9 pairs
−1.92, QQQ 1 −8.28, SPY 1 −5.73. Ex-SPX that is roughly break-even, so this
ruling is **not** a claim the gate is too tight, and must never be quoted as
one. It is a claim that the PLUMBING can be graded while the regime is wrong.

Implementation contract: funding starts at **XSP scale** (~$8k margin per
contract); SPX scale only after 10 funded XSP cycles clear the same gates.
NDX stays off (f6a6368 — it cannot be funded; paper money does not change
what the real account can carry).

### S3 and S5 were measuring a broken denominator — fixed 16 Sep 2026

`economics()` is documented "per ONE weekly contract" and hardcodes
`legs["weekly"]` and `7/365`. `push_decisions()` computed that ONE block and
stamped it onto EVERY expiry row in its loop, so the monthly row carried
`dte: 21` beside a credit priced at 7 DTE. Live proof, XSP 15 Sep — same
`credit_modelled` on both rows, reality nothing like it:

    leg              strikes      credit_modelled   actually received
    weekly  (7 DTE)  750 / 774          546              245.56
    monthly (21 DTE) 751 / 776          546            1,364.56

The file's own DTE table puts 7-DTE and 21-DTE credits ~2.2x apart; they
cannot both be 546. Consequences, both of which had to be fixed BEFORE the
window opened rather than explained after it closed:

* **S3** read 45% on weekly and 250% on monthly. Neither number meant
  anything — the gate was not testing slippage, it was testing a mislabelled
  field.
* **S5** sizes off credit. Taken from this field, a monthly position was
  understated ~2.5x, which is a funding figure wrong by the same factor.

Fixed by computing economics PER EXPIRY KIND. `row["economics"]` still holds
the weekly block so the email bodies keep their "one weekly contract" meaning;
`row["economics_by_kind"]` carries one block per kind and `push_decisions()`
selects the matching one. Collateral and margin move with it (they were also
taken from the weekly leg's strike).

Validated against the live row rather than against the fix's own logic — the
old code reproduces the recorded 546/546 exactly, the new code does not:

    OLD   weekly 546   monthly 546     <- what the desk actually recorded
    NEW   weekly 546   monthly 1,441
    GOT   weekly 245.56  monthly 1,364.56

### What the fix reveals, and does NOT solve

With the monthly priced at its own expiry, S3 becomes meaningful there:
1,364.56 / 1,441 = **94.7%**, comfortably past the 70% bar.

The weekly still reads **45%** (245.56 / 546) — the model asks more than twice
what the market paid, at identical strikes on the same day. The likely cause is
in this file already: `iv_used` is the **30-day** vol index, applied unchanged
to every expiry. In contango a 30-day IV overprices a 7-DTE option and lands
close at 21 DTE, which is exactly the pattern above. So the residual is a
TERM-STRUCTURE blindness, not a strike or pricing-code error.

NOT fixed here, deliberately: correcting it needs a per-expiry IV the desk does
not yet capture (the nightly chain lane targets ONE DTE). Until it is fixed,
**grade S3 on the monthly leg only** — the weekly's denominator is known wrong
and a gate must not be graded against a number we already know is broken.

### Where the D1 figure actually lives (two sites, one of them not in this repo)

* `strategies/lambda_handler.py` — `FUNDING_NAV_USD = 150_000`, feeding
  `paper_swing_dryrun`'s `--capital-usd`.
* `~/Library/LaunchAgents/com.tradepro.paper-swing-ibkr.plist` — the lane that
  actually places, `--capital-usd 150000`. **Not repo-tracked**, so it cannot
  be covered by a test. Change one, change the other in the same breath.

Consequence worth stating plainly: the live swing sleeve's capital moved
**100,000 -> 150,000**, a 50% increase in position sizing on an auto-placing
lane. That follows from D1 as this doc defines it ("sets ... swing
`--capital-usd`"). B1 still holds at the new size — 15 positions x 5% = 75% of
NAV gross, ~15% of NAV under a -20% stress, plus the strangle's 8.7% — but if
the intent was a swing SUB-ALLOCATION rather than the full NAV, this is the
line to change and it is a one-word correction.

## W — swing sleeve

| # | Test | Threshold |
|---|------|-----------|
| W1 | Closed trades, each traced to a published signal (F2) | ≥ 10 |
| W2 | Median entry slippage vs published signal ref (F3) | ≤ 0.5% |
| W3 | Live mean/trade | > 0, and 0 rule breaches (stop honoured, hold cap honoured) |
| W4 | Entries filled above signal × 1.015 (the chase cap, proven live) | exactly 0 |

W1 may take longer than 6 weeks at swing's firing rate. Then the window
extends for the sleeve — a rare rule is not a failing rule, and thin evidence
does not ripen by impatience.

## The caveat that stays attached to any strangle pass

The vol gate keeps the book out when volatility is ALREADY high. It cannot
stop volatility ARRIVING after entry, and the Monte Carlo behind the
strategy, run on gated trades only, structurally cannot see a crash. A full
pass here means "the machine works and the record matches the claim" — it
does NOT mean crash risk has been measured. S5 is the acknowledgement: size
as if the 8.8× day happens, because one day it does.

## Predictions — recorded now

* **S1 breaks at least once in the first two weeks** (~60%): a silent failure
  mode not yet on the desk will surface. That is the gate working.
* **W2/W4 pass** (~75%): the LMT cap makes chase impossible by construction.
* **W1 is the slowest gate** and the likeliest reason the swing sleeve funds
  after the strangle sleeve rather than with it.

**Thresholds do not move after the numbers are seen.** A stress reading of
35.1% is a B1 fail, and the write-up will say so.
