# Spec — Intraday VWAP-Stretch Reversion on Five High-Range Names

**Date:** 2026-10-20
**Repo state at drafting:** `e7f1036`
**Status:** specification only. Nothing here trades — paper or live — until the
Phase 1 study clears the gates in §5, which are committed with this file,
BEFORE any 5-minute run.

---

## 0. Why

### 0.1 The owner's question

> "I built TradePro to help with trading decisions and it's not assisting me in
> any way … My goal is mostly intraday and short term. We have looked into mean
> reversion and momentum but don't think we are using price movement that well.
> What is the least we can target?"

### 0.2 What `INTRADAY_VWAP_ORB_GATES_V1.md` already told us

The 28 Sep study failed its own gates, but it named the useful half:

* **VWAP reversion carried a real, thin edge**: +0.068%/trade, payoff 1.07,
  545 out-of-sample trades.
* **Opening-range breakout did not**: payoff 0.90, and it supplied 3,804 of the
  4,349 trades, diluting the pooled result.
* **The owner's actual names were untestable.** The 29 gradeable symbols were
  liquid large caps with deep 5m history. MU had 39 sessions, SNDK 34. The
  study said, in its own words, that the volatile-name version was
  *"untested rather than unpromising"*.

This spec is that missing study, plus the module that would run it.

### 0.3 Exploratory evidence (20 Oct 2026) — NOT graded evidence

The source data was IBKR 30-minute RTH bars for 13 Jul to 19 Oct 2026, 69 full
sessions, on nine names the owner trades. Costs were **0.10% round trip**. The
fill model was pessimistic: when one bar holds both the stop and the target,
the stop counts. These numbers chose the hypothesis; they do not grade it
(§5.4).

**Daily movement (median over the 69 sessions):**

| name | range % | open→high % | open→low % | abs gap % |
|---|---|---|---|---|
| SNDK | 5.95 | 2.23 | 2.87 | 2.21 |
| STX  | 5.82 | 2.28 | 2.43 | 2.07 |
| MRVL | 5.60 | 2.19 | 2.24 | 2.50 |
| WDC  | 5.38 | 2.42 | 2.60 | 2.24 |
| MU   | 4.52 | 1.73 | 1.91 | 1.92 |
| AMD  | 4.02 | 1.43 | 1.70 | 1.62 |
| SNOW | 3.95 | 1.97 | 1.48 | 0.74 |
| PLTR | 3.63 | 1.79 | 1.67 | 0.81 |
| NVDA | 2.26 | 0.87 | 1.12 | 1.00 |

**Rule families on the five names in §2 (net of costs, per trade):**

| family | n | win % | expectancy |
|---|---|---|---|
| ORB long (break of first 30m, stop other side) | 105 | 41.9 | **−0.25%** |
| ORB short | 115 | 37.4 | **−0.66%** |
| VWAP reclaim long (momentum entry) | 190 | 45.3 | **−0.11%** |
| **VWAP-stretch long, stretch 0.25×ADR, stop 0.5×ADR** | 71 | 60.6 | **+0.42%** |
| **VWAP-stretch long, stretch 0.35×ADR, stop 0.5×ADR** | 30 | 73.3 | **+0.99%** |

**The same stretch rule split by sector regime** (equal-weight basket of the
five, above or below its 10-day mean at the prior close), stretch 0.35×ADR,
stop 0.5×ADR:

| regime | n | win % | expectancy |
|---|---|---|---|
| up | 10 | 90.0 | +1.71% |
| down | 20 | 65.0 | +0.63% |

Two observations follow, and both shape the study.

1. **Every breakout and momentum entry lost money; the reversion entry made
   money.** This matches the ORB/reversion split in the 28 Sep study, on
   different names, bars and dates.
2. **The regime gate helped but was not required on these five.** On the
   nine-name set the stretch rule only worked in up-regimes. That is why the
   gate is a declared variant in §5.2, not a fixed rule.

The short-side mirror was unstable (+0.84% in the first half, −0.67% in the
second) and is out of scope (§9).

### 0.4 What the owner's own trades show (IBKR CCIT, 3 Aug – 20 Oct 2026)

Long stock trades were matched FIFO into lots, and open lots were marked at
the 19 Oct close.

| holding time | lots | win % | avg win | avg loss | P&L |
|---|---|---|---|---|---|
| intraday (< 7h) | 15 | 93.3 | +2.11% | −0.30% | +$3.7k |
| 1–3 days | 31 | 83.9 | +4.51% | −2.87% | +$11.7k |
| > 3 days | 31 | 61.3 | +5.42% | −5.65% | −$2.0k |

**Open lots in MU, SNDK, WDC, STX and SNOW stand at −$26.6k.** All five
names, closed and open lots together, net to **−$17.5k**. Splitting those lots
by month and regime at entry:

* **October entries:** −$33.0k.
* **Entries taken when the basket was below its 10-day mean:** 35 lots,
  −$16.5k.
* **Replay with a hard −3% stop**, everything else unchanged: −$4.8k instead
  of −$17.5k.

The owner's intraday and 1–3 day trading works. The losses come from:

* entries taken in down-regimes,
* adding to falling positions (SNDK was bought at 1661, 1642, 1590, 1589,
  1581, 1579, 1319, 1285 and 1281),
* letting a day trade become a multi-week hold.

**The module must enforce exits, not just find entries.**

### 0.5 The answer to "what is the least we can target"

| | value | basis |
|---|---|---|
| move targeted per trade | **back to VWAP, ~1.4–1.9%** | 0.35 × 20-day ADR on these names |
| stop per trade | **0.5 × ADR, ~2.0–2.7%** | |
| realistic net expectancy | **≥ +0.30% per trade** | the §5 gate; exploratory showed +0.42% to +0.99% |
| frequency | **~0.5–1 trade per session across the five** | 30–71 trades in 69 sessions |
| per $15k ticket | **≥ $45 per trade, ~$700–1,400 a month** | at the gate floor, not the exploratory figure |

The floor is the gate, and the gate is deliberately below the exploratory
number. A 30-trade sample on one quarter overstates. If the study confirms
only the floor, the floor is what gets planned around.

---

## 1. Scope

**In scope**

1. A pre-registered **study** on ≥ 2 years of 5-minute bars (§5).
2. Only if it passes: a **live signal module** that alerts, with an optional
   IBKR-paper bracket order (§6).
3. **Discretionary guardrails** for the owner's own trades. This part is
   independent of the study and can ship first (§7).

**Out of scope:** see §9.

---

## 2. Universe — five names, fixed

    SNDK  STX  MRVL  WDC  MU

* **Why these five.** They are the names whose median daily range over the
  exploratory window was ≥ 4.5%. The reversion target is a fraction of the
  daily range, so a name whose range is too small cannot clear 0.10% costs.
  NVDA (2.3%) was the one name where the rule lost money in the exploratory
  run.
* **The list is fixed for the study.** No names are added or swapped after
  seeing results.
* **Live membership check, monthly.** A name is *suspended*, not replaced, if
  its 60-session median range falls below **3.5%**. It returns when it is back
  above 4.0%; the hysteresis stops it flapping. MU is the name to watch: its
  20-day ADR is 4.09% today.
* **Earnings blackout.** No entries on a name from the session before its
  report through the session after. Gaps there are event risk, not reversion.

---

## 3. Data requirements — Phase 0 (blocker)

| need | spec |
|---|---|
| resolution | 5-minute, RTH only, 09:30–16:00 ET |
| depth | **≥ 500 sessions per name**, about 2 years; IBKR serves this |
| names | the five in §2, plus SPY for reference only |
| source | IBKR harvester into the existing bar cache (`*/{SYM}/5m/*.parquet`) |
| integrity | no session with < 75 of 78 bars; log gaps and halts, never fill them |
| daily | 1d bars covering the same window, for ADR20 and the regime basket |

**Acceptance for Phase 0:** a coverage report listing, per name, the first
session, last session, session count, and count of short sessions. The study
does not start until all five show ≥ 500 sessions.

SNDK began trading on 24 Feb 2025, so it cannot reach 500 sessions until early
2027. For SNDK, grade on what exists, and report its trade count
separately. Do not drop it silently.

---

## 4. The rule

All parameters below are the study's defaults. §5.2 lists the only variants
allowed.

### 4.1 Inputs, computed without look-ahead

* **`ADR20`**: mean of `(high − low) / open` over the **20 prior sessions**.
  Today's bars are never used.
* **`VWAP_t`**: session VWAP from 09:30 through the **close of bar t**, using
  typical price `(h + l + c) / 3`, accumulated bar by bar. Reuse
  `vwap_series()` from `intraday_vwap_orb_v1.py`.
* **`regime_up`**: equal-weight basket of the five names. True if the basket
  closed above its 10-session mean at the **prior** close.

### 4.2 Entry — long only

The signal fires on the first 5-minute bar *t* that meets all of these:

1. Bar *t* closes inside **10:00–14:30 ET**. The first 30 minutes are skipped
   because the opening print is noise.
2. `close_t ≤ VWAP_t × (1 − 0.35 × ADR20)`, i.e. the bar *closes*
   stretched, not just wicks there.
3. No earnings blackout (§2).
4. No open position in this name today. **No adding, ever.**
5. Fewer than **2** open positions across the strategy.
6. If the regime variant is on: `regime_up` is true.

**Fill:** at the **open of bar t+1**, plus half the cost assumption. Not at
`close_t`: the study must not assume a fill on the bar that generated the
signal.

### 4.3 Exits — first one hit wins; stop first if a bar holds both

| exit | level |
|---|---|
| target | `VWAP_t`, frozen at signal time (not the moving VWAP) |
| stop | `fill × (1 − 0.5 × ADR20)` |
| time | close of the 15:50 ET bar |

**No overnight carry in the systematic version.** Carrying is a separate idea
that `INTRADAY_DIP_GATES_V1.md` already showed turns into the swing sleeve.

### 4.4 Sizing (live phases only)

* **Risk-based:** `qty = floor(RISK_USD / (fill × 0.5 × ADR20))`.
* **Default `RISK_USD` = $300.** With ADR 5% that is about $12k notional,
  similar to the owner's current tickets.
* **Hard cap of $15k notional per position.** At most 2 positions, so at most
  $30k gross.

---

## 5. Phase 1 — the study and its gates (pre-registered)

**Harness:** `strategies/backtests/studies/intraday_vwap_stretch_v1.py`.
Reuse `vwap_series`, `_resolve` (stop-first) and `summarise` from
`intraday_vwap_orb_v1.py`. Do not copy them.

### 5.1 Costs

Use **0.10% round trip** as the grading figure. These names have wider spreads
than the large caps in the 28 Sep study, which used 0.04%. Also report the
result at 0.04% and at 0.20%, so the sensitivity is visible.

### 5.2 Variants — declared now, six maximum

    stretch   0.25 / 0.35 / 0.50  × ADR20
    regime    off / on
    stop      0.5 × ADR20  (fixed)

That makes 3 × 2 = **6 cells**, which meets the
`max_variants_before_oos: 6` discipline of the 28 Sep study. Any cell beyond
these six needs a new gates file.

### 5.3 Walk-forward

* **In-sample:** the first 60% of sessions, pooled across names in date order.
* **Selection:** pick ONE cell in-sample by **expectancy per trade**.
* **Grading:** grade that cell out-of-sample.
* **Report every cell in both windows anyway**, so the choice is visible.

### 5.4 The exploratory window is excluded from grading

The 69 sessions from 14 Jul to 19 Oct 2026 chose this hypothesis. Report
out-of-sample results twice: **with** that window, and **without** it.
**Grade on the "without" figure.** A rule that only works in the window that
suggested it has not been tested.

### 5.5 Gates

These follow `GATE_TEMPLATE.md`: Group A asks whether the edge is real, and
Group B asks whether it can be funded.

| # | gate | threshold |
|---|---|---|
| V0 | out-of-sample trades in the chosen cell | **≥ 300** |
| G1 | win rate | **≥ 50%** |
| G2 | expectancy per trade, net of 0.10% | **≥ +0.30%** |
| G3 | payoff (avg win ÷ avg loss) | **≥ 1.0** |
| G4 | top 1% of trades share of net profit | **≤ 25%** |
| G5 | worst single trade | **≥ −4.0%** (gap-through stops included) |
| G6 | **two-split**: G2 holds in all four cells of the TIME split × the SYMBOL split (SNDK+STX+MU vs MRVL+WDC) | all four positive, ≥ 3 of 4 ≥ +0.30% |
| G7 | per name: no single name contributes > 50% of net profit | |
| C1 | concurrency measured: median, p95 and peak simultaneous positions | report |
| C2 | the 2-position cap: re-grade G2 with the cap enforced in time order | **≥ +0.30%** |
| C3 | days with signals the cap had to skip | report; if > 20% of signals, grade the rule for which signal gets taken |

**Why G1 is 50% and not 55%.** The exploratory payoff came from a target near
1.5× the loss. A 50% win rate at payoff ≥ 1.0 plus G2 is a stricter combined
test than a 55% win rate on its own.

**Why G2 is +0.30%.** On a $15k ticket that is $45 per trade, roughly the
dollar bar the 28 Sep gate set ($40 on $25k). It also sits well below the
exploratory +0.42% to +0.99%, which a single quarter will overstate.

### 5.6 Predictions, on record before running

1. **The breakout family is not re-run.** It has now failed twice, on
   different names and bars.
2. **G2 passes for stretch 0.35 with the regime gate off.** Probability
   about 50%.
3. **The regime gate improves expectancy but costs trades.** With the gate
   on, V0 (≥ 300 trades) is at risk; expect about 40% of the ungated count.
4. **G6 is where it is most likely to die.** In particular, a time split
   where one half is mostly down-regime. That is the failure mode that killed
   the intraday dip study.
5. **Overall: about 35%** that the chosen cell passes every gate.

If it fails, the file is kept with the result. If it fails only G6, the next
step is a regime study, not a parameter change.

---

## 6. Phase 2 — the live signal module (only after §5 passes)

### 6.1 Placement in the codebase

Build a new strategy class, **`IntradayVwapStretchStrategy`**, in
`strategies/tradepro_strategies/paper/strategies/`. It reuses
`IntradayFlatStrategy`'s:

* position-management path (`_manage_open_position`, EOD flatten window,
  `on_session_end` backstop),
* decision log,
* persisted-state restore.

It replaces the Ichimoku basket scanner with the fixed five-name list and the
§4.2 trigger. It **must not** change `IntradayFlatStrategy`'s behaviour.

It runs through the existing `intraday_engine.py` queue. Bars come from IBKR
live 5m RTH, not yfinance.

### 6.2 What the owner sees — one signal card per trigger

The card below is illustrative; every number on it is made up.

Push and email delivery, and an "Intraday" panel in the UI.

```
MU  BUY  — VWAP stretch                      14:05 ET
price 872.40   VWAP 889.10   stretch −1.88% (0.46×ADR)
entry ≤ 873.00   target 889.10 (+1.84%)   stop 854.60 (−2.10%)
size 14 sh  (~$12.2k, risk $300)   flat by 15:50 ET
regime: basket ABOVE 10d (+)   open intraday positions: 1/2
earnings: clear (next 17 Dec)
```

Every field comes from §4; nothing on the card is free text. When a filter
blocks a trigger, the card still goes out, greyed, with the reason, e.g.
*"blocked — 2 positions open"*. That way the owner can see the module is
alive on a day it does not trade.

### 6.3 Execution modes (UI setting)

| mode | behaviour |
|---|---|
| `alert` (default) | card only |
| `paper` | card plus an IBKR **paper** bracket: limit entry, take-profit at target, stop at stop, GTD 15:50 ET, cancel-on-fill OCO |
| `live` | **not built in this spec.** Requires §8 Phase 3 to pass first. |

### 6.4 Decision log entries (for the audit)

`stretch-trigger`, `stretch-blocked-{window|cap|regime|earnings|already-traded}`,
`entry-filled`, `exit-{target|stop|time}`, `eod-flatten`. Each entry carries
the VWAP, ADR20, stretch and the levels.

### 6.5 Feature file — `strategies/features/intraday_vwap_stretch.feature`

These scenarios must be synthetic, with no IBKR calls.

* A bar that **wicks** below the stretch level but **closes** above it does
  not trigger.
* A close at the stretch level at 09:55 does not trigger (window). The same
  close at 10:05 does.
* The target uses VWAP frozen at the signal bar, even after VWAP moves.
* A bar touching both the stop and the target exits at the **stop**.
* A second stretch on the same name, same day, after an exit does **not**
  re-enter.
* With 2 positions open, a third trigger is logged
  `stretch-blocked-cap` and no order is created.
* A name inside its earnings blackout is blocked.
* An open position at 15:50 ET is flattened, and `on_session_end` flattens it
  if the bar never arrives.
* ADR20 for session *d* uses sessions *d−20…d−1* only; a test feeds a huge
  bar on day *d* and asserts ADR20 is unchanged.

---

## 7. Discretionary guardrails — ships first, independent of the study

These watch the owner's **own** IBKR CCIT positions, read-only, through the
existing native IBKR connector path. They need no strategy result, because
they enforce what §0.4 shows.

| guard | rule | output |
|---|---|---|
| hard stop | any long stock lot in the five names is **−3%** from its fill | alert: "MU lot @1039.29 is −3.0% — stop level 1008.11" |
| carry limit | a lot opened as a day or short-swing trade is **> 3 sessions** old | daily alert listing it |
| no averaging down | a new BUY in a name already held, at a price **below** the existing average | alert when the fill lands |
| regime | the basket is below its 10-day mean | morning banner: "regime DOWN — new intraday longs outside the rule" |
| daily summary | at 21:30 London | realised P&L today, open lots with % from fill, guard breaches today |

**Guardrails alert. They never place orders.** The owner decides.

**Reconciliation note.** TradePro reads the IBKR **paper** account
(`DUP656969`). The CCIT live account is a separate book (see project
memory). This phase therefore needs a read-only path to the live account's
positions, or it runs as a scheduled Claude task against the native IBKR
connector. That is an open decision for the owner (§10).

---

## 8. Rollout and kill criteria

| phase | what | passes when | stops if |
|---|---|---|---|
| 0 | 5m backfill, five names | §3 coverage report | — |
| G | §7 guardrails | live on the owner's book | — |
| 1 | §5 study | every gate in §5.5 | any gate fails — the file is kept with the result |
| 2 | module in `alert` + `paper` mode | **12 weeks**, ≥ 40 paper trades, expectancy within the study's out-of-sample 90% band | expectancy below the band's 5th percentile after ≥ 25 trades |
| 3 | live sizing | owner's decision, with a separate gate file | — |

The 12-week paper band is computed the same way as
`get_forward_test_status`: a block bootstrap of the study's out-of-sample
trades, recorded **before** paper trading starts.

---

## 9. Explicitly NOT in this spec

| item | why |
|---|---|
| shorts | the exploratory short side flipped sign between halves |
| ORB / VWAP reclaim / any breakout entry | lost money in the 28 Sep study and again here, on both name sets |
| NVDA, PLTR, AMD, SNOW | daily range too small for the target to clear costs; may be revisited by a separate gates file |
| overnight carry | `INTRADAY_DIP_GATES_V1.md`: carrying turns the trade into the existing swing sleeve |
| LLM / news veto | the earnings blackout covers scheduled events; an LLM gate is a separate, testable add-on |
| parameter changes after Phase 1 | need a V2 gates file |

---

## 10. Open decisions for the owner

1. **Guardrails delivery (§7):** add a read-only live-account path to
   TradePro, or run the guardrails as a scheduled Claude task against the
   native IBKR connector? The second needs no code.
2. **`RISK_USD` for paper (§4.4):** default $300.
3. **Alert channel:** push, email or both (the existing digest is email).

---

## Appendix — exploratory method, so it can be re-run

* IBKR `get_price_history`, 30-minute bars, `outside_rth=false`, 900 bars per
  name, ending 19 Oct 2026. The first partial session was dropped.
* VWAP: cumulative `(h + l + c) / 3 × volume` per session.
* ADR20: rolling mean of the prior 20 sessions' `(H − L) / O`.
* Simulation started at the bar after the signal bar. Stop first on ambiguous
  bars. Exit at the session close if neither level was hit. 0.10% cost
  subtracted per trade.
* Owner trades: `get_account_trades(DAYS_90)` on the live CCIT account, STK
  only, USD, FIFO lot matching. Open lots were marked at the last 30m close.
* 30-minute bars blur the trigger and the exit order inside a bar. That is one
  more reason these numbers chose the hypothesis but do not grade it.
