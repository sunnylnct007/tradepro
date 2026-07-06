# ICH Equity — Post-Mortem: why it lost, with facts

**Result:** −£2,006 (−4.01%) on £50,000 over ~4 weeks (T212, broker-confirmed 337/337 fills).
**Date:** 2026-07-06. **Method:** broker ledger + signal-audit + verbatim-Ichimoku
reconstruction on clean IBKR daily bars.

It did not fail for one reason. It failed for **four**, ranked by impact — and the
biggest is the *signal itself in this regime*, not execution.

---

## Fact 1 — The signal itself lost in a choppy market (biggest cause)

Re-ran the **verbatim** signal (`_equity_trader_signal.compute_position`, 5/32/50,
disp 32) on **clean IBKR bars**, executed *perfectly* (no slippage, no error), across
all 54 traded names:

- **Signal lost on 40 of 54 names**, avg **−5.1% / name**.
- Only **8 of 54** were "good trade execution ruined."
- Coverage 54/54 (IBKR + 1 Yahoo fallback) — not a data gap.

**Mechanism — whipsaw.** It bought and sold the *same* name repeatedly, losing a
little each cycle:

| Name | Round-trips (~7wk) | Result |
|------|--------------------|--------|
| KO   | 4 | −5.3% |
| MRK  | 3 | −8.0% |
| EVR  | 3 | −2.9% |
| SWKS | 3 | +4.8% |

Ichimoku is trend-following; it needs a clean trend and **churns in chop**. May–Jul
2026 was choppy/topping, so the signal flip-flopped. Regime failure — the dominant one.

## Fact 2 — It chased extended entries (amplifier)

The signal has **no "how far above the cloud" check** — a fresh breakout and a name
already +100% fire the same BUY.

- **15 of 54 entries were stretched** (>30% over the cloud): DOCN **+108%**, SYNA
  +59%, VICR +50%, CSCO +47%, AMKR +35% *at entry*.
- Deepest losers were all chases: VRT −27%, CIEN −22%, ON −20%.
- The signal **wins early** in a trend (April: HPE +32%, KLAC +27%), **bleeds chasing
  late** (June). Our book was almost all June late-chases.

## Fact 3 — Bad data delayed the exits (execution)

Live system read **yfinance, graded BRONZE/FICTIONAL by our own quality layer**. An
empty/rate-limited frame → scanner emitted **HOLD instead of SELL** → a sell signal on
day X wasn't acted on until X+2/3, at a worse price.

- SWKS: signal exit at **+1%**, rode to **−17%**. ORCL: exit at −13%, rode to −34%.
- 6 names (AMKR, STRL, TTMI, STX, AEIS, WDC) flagged **exit-overdue** — sell signal
  2–3 days old, still held.

## Fact 4 — Over-trading / churn

- **337 round-trips** on ~£39k deployed = **−£5.86 / round-trip**.
- Of that: ~£1–1.5 cost (spread + FX conversion on US stocks in a GBP account),
  ~£4.3 adverse price move.
- **98% of the loss is *realized*** (−£1,975 booked vs −£31.57 unrealised) — the
  damage is in *closed* trades (churn), not stuck holdings.

---

## Synthesis

ICH Equity is a **trend signal with real edge in clean trends** (early entries won
big). This window was a **perfect storm**: a choppy market it whipsawed in (lost
40/54 even executed perfectly) + no entry discipline (chased 15 extended names) + bad
data that delayed exits + over-trading (337 round-trips paying friction). Not one bug
— a regime-sensitive signal run without guards, on bad data, too often.

## Fixes (mapped to each fault)

| Fault | Fix | Status |
|-------|-----|--------|
| Whipsaw in chop | Regime gate `entry_max_flips=3` (skip choppy names) | live on clone |
| Chasing extended | Don't-chase `entry_max_ext_pct=50` + `entry_rsi_max=80` | live on clone |
| Bad data → late exits | IBKR→Yahoo→fail-loud price chain | proven 54/54; prod wiring pending |
| Exits not executing (clone) | Broker-confirmed routing (real IBKR order id) | proven |
| Over-trading / churn | Min-hold / re-entry cooldown guard | this change |

All guards are **clone-only, opt-in, config-driven**; the control `ichimoku_equity`
stays trader-verbatim (parity-tested).
