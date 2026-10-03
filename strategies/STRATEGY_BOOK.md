# TradePro Strategy Book

*3 Oct 2026. The single reviewable description of every strategy this desk
runs, has parked, or has killed — written for an external reviewer who has
never seen the codebase.*

**How to review this document.** Every number below is copied verbatim from a
pre-registered study in this directory, named inline like
`SWING_OUT_OF_SAMPLE_GATES_V1.md`. Nothing is quoted from memory. If a claim
here disagrees with its source file, the source file wins and the discrepancy
is a bug in this book. The live parameters are imported by the running code
from the modules named below — the book describes the same constants the
engine executes, not a parallel account of them.

**The discipline.** Before any study runs, its thresholds AND a written
prediction are frozen in a `*_GATES_V*.md` file and committed. Results are
recorded verbatim, including failures and wrong predictions — of the last five
predictions recorded, three were wrong, and saying so is the point: the gates
decide, not the narrator. A strategy change requires a new pre-registered
amendment, never an edit to a published result.

---

## 1 · SWING — mean reversion (LIVE, paper)

**Rule** (`tradepro_strategies/signals/mean_reversion.py`): buy when the close
is **≥ 2.25σ below its 20-day mean** while **above its 200-day average**. Exit
at the 20-day mean (the target), a **−8% stop**, or **20 sessions**, whichever
first. Long only. σ was widened from 2.5 to 2.25 on 8 Sep 2026 by
pre-registered amendment (band study; band C failed on tail).

**Evidence** — `SWING_OUT_OF_SAMPLE_GATES_V1.md`: the rule was frozen on the
~250-name development universe, then run on **745 unseen symbols**:

    out of sample   21,948 trades   71.3% win   +0.90%/trade   worst −32.5%
    in sample                                    +0.81%/trade
    → no decay on unseen names. All gates PASS.

**The rule's honest weaknesses, measured on purpose:**

- **It buys falling knives by design.** `SWING_V3_GATES_V1.md` Q3 tested
  waiting for a confirmation bar: win rate rises to 73.9% but mean/trade
  **halves** (+0.42% vs +1.00%). The edge lives in the close nobody wants to
  buy, so entry stays at the signal close and the KNIFE label on the board is
  a display verdict, not a filter.
- **The stop does not survive gaps.** Stops are checked on the close; the
  worst historical trade is −32.5% against an −8% stop. The board says this.
- **It loses money in a crash.** `SWING_CRISIS_GATES_V1.md`: 2008 alone was
  **142 trades, 42.3% win, −2.49%/trade**. The 200-day filter halves activity
  in a crash and caps single-trade damage (−13.7% worst in 2008) but does not
  make the year positive. The honest headline is "an edge in normal regimes
  that a crash interrupts", not "an edge that survives crashes".
- **Regime dependence is measured** — `REGIME_CONDITIONING_GATES_V1.md`,
  22,254 trades, all gates pass: best cell med-vol/trend (+1.56%, 76.8%),
  worst high-vol/range (**−0.15%** — the one negative cell). Trend matters
  more than volatility. Not currently used as a gate; published as context.

**Sizing**: `SWING_SIZING_GATES_V1.md`. Live: 2% of capital per position.

**Rejected extensions** (both pre-registered, both killed):
- *Sell a put instead of buying the dip* — `SWING_PUT_OVERLAY_GATES_V1.md`:
  assignment is rare (8.4% at −5% strike) but concentrated precisely in the
  dips that kept falling (−8.46% in assignment weeks vs +0.83% overall).
  EV ≈ −3.3%/yr gross. **G3 FAIL — do not build.**
- *Confirmation entry* — see Q3 above. Halves the return. Rejected.

---

## 2 · MOMENTUM — pullback (LIVE, paper)

**Rule** (`tradepro_strategies/signals/momentum_pullback.py`, imported from
the screen's own `_entry_signal` — one definition, never retyped): buy a
pullback to the **10-day SMA** inside an established uptrend (close > 200-SMA,
20-SMA > 50-SMA, close > 20-SMA, close ≤ 10-SMA × 1.005, prior close above
its 10-SMA). **No fixed target**: exit on an **8% trail from the peak**, a
hard **−8% stop**, or **60 sessions**. Long only.

**Evidence** — `MOMENTUM_GATES_V2.md`, variant C (pullback + hard −8%):

    5,815 trades   47.0% win   +1.53%/trade   34-bar median hold   worst −14.7%
    ALL SIX GATES PASS on the development universe.

**The amendment a reviewer must read** (same file, 24 Sep 2026): re-measured
on the full 969-symbol screening universe the rule is actually run against:

    41,023 trades   +1.42%/trade   worst −36.7%   → G5 FAILS (−36.7% vs −25%)

The edge survived seven times the sample; the tail did not. The desk's
response was a portfolio-level sizing study rather than pretending the badge
still held: `MOMENTUM_SIZING_GATES_V1.md` swept sizes and found **G1 fails at
5%** (37.6% drawdown share) while **2.5% passes everything**. The live lane
runs **2% × 20 positions** — inside the passing region with margin. The
per-trade G5 stands FAILED and is disclosed, managed by sizing, not spin.

**Character**: 51% of trades lose and the median trade loses 0.33% — the
positive mean is carried by the winners' tail. A low-win-rate trend rule;
judging it on a week of marks measures noise (median hold is 34 bars).

---

## 3 · INDEX STRANGLE — short vol, 8 markets (SHADOW ONLY)

**Rule**: sell a delta-targeted strangle per market (SPX/XSP/SPY/QQQ/NDX/
GOLD/NIFTY/BANKNIFTY), weekly and monthly tenors, **gated per VOL INDEX** —
each market trades only below its own vol threshold (e.g. VIX < 13.5,
VXN < 17.5, INDIAVIX < 12.5). The gate has refused every US session recently;
that is the gate working, not failing.

**Honest status**: the gate's economics are **unproven**. Decisions and
shadow trades (gate-refused, traded anyway for data) are logged to a
decision ledger; the shadow book reads **−$2,009.91 over 24 pairs / 30 days**
— evidence the gate's refusals were correct. A Monte Carlo on gated trades
cannot see a crash (`project_index_strangle_eight_markets`), so tail risk is
asserted, not measured. **Not funded; must not be described as gated-and-
proven.** P&L truth lives in `strangle_execution`, not the decision log.

---

## 4 · WHEEL — cash-secured puts (SCREEN ONLY, DO NOT FUND)

Screens ~82 quality names for CSP candidates through hard gates: regime,
IV/HV ≥ 1.0 bridge (until IV-Rank matures), delta 0.20–0.35, 25–50 DTE,
OI ≥ 250, spread cap, premium floor (≥$0.20 and ≥8%/yr), no earnings in
window, capital limits. Every rejection names its gate; no silent green
lights.

**Backtest verdict stands**: `WHEEL_BACKTEST_V3` — the 200-SMA trend floor
FAILED (G4 META −71.4% drawdown). **Do not fund.** The screen exists as a
candidate list for manual judgement, which is the product's stated purpose.
Related measurement: short-put P&L is 73% profitable when the underlying
rises and 8% when it falls — premium selling here is a directional trade
wearing an income costume, and the book says so.

---

## 5 · Directional scope — why the desk is LONG-ONLY

`SHORT_SIDE_GATES_V1.md` ran the exact mirrors of both live rules — nothing
re-tuned, same universe, same harness, 2006→2026:

    long  mean reversion   71.0% win   +0.90%/trade
    short mean reversion   57.1% win   +0.25%/trade   G5 FAIL (worst −30.7%)
    long  momentum         47.0% win   +1.53%/trade
    short momentum         32.1% win   −1.26%/trade   G1 G2 G5 G6 FAIL

Mean reversion is symmetric but thin and decaying on the short side
(+0.43% → +0.08% across halves) with an uncapped tail; momentum is not
symmetric at all. **Neither mirror licenses a lane.** Both sleeves are
long-only by measurement, not by temperament.

---

## 6 · The graveyard — tested and killed

A reviewer should weigh what was rejected as heavily as what runs:

| idea | study | verdict |
|---|---|---|
| Earnings-window strategies (v2) | `EARNINGS_GATES_V2_RESULT.md` | failed 6/8, both vetoes rejected |
| S/R level edges | `project_sr_level_study_v1` | 76,260 events, both edges negative vs placebo |
| Intraday VWAP/ORB | `INTRADAY_VWAP_ORB_GATES_V1.md` | fails 3/5; costs = half the edge |
| QDB candidate | OOS run | failed; killed |
| Swing confirmation entry | `SWING_V3_GATES_V1.md` Q3 | halves return; rejected |
| Put overlay on swing | `SWING_PUT_OVERLAY_GATES_V1.md` | adverse selection; G3 fail |
| Short mirrors (both) | `SHORT_SIDE_GATES_V1.md` | fail; long-only stands |

---

## 7 · Execution reality (what a reviewer should stress-test)

- **Fills**: 77/77 orders reaching the broker since 28 Sep have recorded
  fills. Placement is proven only by `brokerOrderId` — an OMS 409 can hide
  both dedupe and refusal.
- **Pre-trade guards**: a C# RiskGate every order passes — market-hours
  (exits exempt on trading days), size/velocity/cash, and since 1 Oct an
  **oversell guard**: a SELL may not exceed the ledger-net position, so a
  long-only sleeve can never sell itself short again (it did, once: a cached
  broker read re-sold ESNT 23 times; root cause fixed the same day — every
  position read now forces a fresh book, enforced by test).
- **Known fragility**: IBKR grants one market-data session per account.
  13% of in-session health probes see a dark snapshot; equity sleeves are
  insulated (they read settled bars), options screens are not. Telemetry to
  attribute this (own line-budget saturation vs external theft) shipped
  2 Oct; verdict pending.
- **Stops are close-checked**: gaps go through them. Both live rules' worst
  realised trades exceeded their stop distance. Disclosed on every board.

## 8 · Open questions a reviewer should press on

1. Is 2%×20 momentum sizing robust to the G5 tail recurring in a cluster
   (several −30% gaps in one week)? The sizing study says yes at portfolio
   level; it has not been stress-tested against correlated gaps.
2. Swing's regime table says high-vol/range is negative — should that cell
   gate entries rather than merely be disclosed?
3. The strangle sleeve has no measured tail. Should shadow data accumulate to
   a pre-registered gate before any funding question is entertained?
4. Signal-archive replay (live-vs-backtest drift) has 6 days of data. At what
   n does divergence from the backtest become actionable?
