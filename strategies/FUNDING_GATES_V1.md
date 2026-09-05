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
| D1 | **The funding figure.** Sets paper NAV, swing `--capital-usd`, strangle sizing. All gates below are scale-free so this number can be set without moving them. | OPEN |
| D2 | **YELLOW regime sizing.** Parked 31 Aug: YELLOW permits a short put at "reduced size" and nothing defines the reduction. Real money may not trade an undefined rule. | OPEN |
| D3 | **Paper realignment timing.** Changing IBKR paper NAV usually means a paper account RESET — it wipes the open book. Done deliberately at a clean point, record snapshotted first. | OPEN |

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

Implementation contract: funding starts at **XSP scale** (~$8k margin per
contract); SPX scale only after 10 funded XSP cycles clear the same gates.
NDX stays off (f6a6368 — it cannot be funded; paper money does not change
what the real account can carry).

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
