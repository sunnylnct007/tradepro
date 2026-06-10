# TradePro — Strategy Performance Review

**Date:** 2026-06-10 (intraday, ~14:45 UTC) · **Accounts:** paper/demo (T212 DEMO, IG DEMO, IBKR PAPER)
**Data basis:** broker-golden where stated (T212/IG report their own marks); at-entry indicators reconstructed from the clean daily cache + broker entry price/date.

---

## 1. Executive summary

The book is **net losing, and no desk has a trustworthy edge yet** — but the losses are **not a signal problem**. They trace to three fixable failures of *discipline and data*:

1. **Entry** — the equity strategy systematically **buys extended/blow-off-top momentum** (median entry at the **97.5th percentile of the 52-week range**, **+42% above the 200-day average**).
2. **Exit** — **no stop**, so reversals run to −17% / −34% (**39 of 81 equity names are past −8%, = 86% of the loss**).
3. **Data** — the **live price feed is unguarded**: a phantom bar fired a **false stop** on a healthy position (UNH printed 377; it traded 406–413 all week).

**Implication:** before adding strategies or features, put the trader's discipline (entry filter + stop) and clean data behind the *one* signal we already have. The trend signal itself is sound; the execution around it is not.

---

## 2. Per-strategy scorecard

| Desk | Broker | Realised LTD¹ | Open (unrealised) | Trades | Win-rate | Verdict |
|---|---|---:|---:|---:|---:|---|
| **ichimoku_fx_mr** | IG DEMO | **+£222** | **−£114** | ~160 | — | Fragile breakeven; currently red |
| **intraday_flat** | IG DEMO | **−£3,114** | — | 383 | **27.5%** | Cost-driven churn — biggest drag |
| **ichimoku_equity** | T212 DEMO | n/a² | **−£2,493** | (held 81) | 21% in-book³ | Holding losers, no stop |
| **ichimoku_equity_ibkr** | IBKR PAPER | unmeasurable⁴ | unmeasurable⁴ | — | — | A/B treatment — can't yet measure |

¹ Net of spread/financing, from broker closed-deal history. ² T212 FIFO realised not yet surfaced. ³ 17 winners / 64 losers among current holdings. ⁴ Clone P&L not reconciled to OMS (shows £0); stops fire on a divergent feed — see §6.

**Net:** clearly losing, dominated by `intraday_flat` churn and the unhedged `ichimoku_equity` book. FX is the least-bad but not a money-maker.

---

## 3. Equity control (`ichimoku_equity`) — the core problem

**Book:** 81 holdings, **−£2,493 open**. 64 losers (−£2,892) vs 17 winners (+£400). Average winner ≈ +£24; losers run −£57 to −£196.

### 3a. We buy at maximum extension (at-entry indicator medians)

| Group | RSI | % above 200-SMA | 20-day run-up | 52-week range %ile |
|---|---:|---:|---:|---:|
| **Losers (64)** | 64 | **+42%** | +16.6% | **97.5th** |
| Winners (16) | 68 | +12.7% | +6.4% | 84.5th |
| All (80) | 65 | +37% | +15% | 96.5th |

- **90% of entries were "extended"** (RSI ≥ 65, or > 25% above the 200-SMA, or top-15% of the 52w range).
- The relationship is **monotonic**: the more extended the entry, the worse the outcome. The strategy's own winners came from *less-stretched* entries — so an extension cap would have skipped the worst losers while keeping winners.

### 3b. Deepest losers — bought parabolic, at the highs

| Symbol | Now | RSI @entry | % > 200-SMA | 20-day run | 52w %ile |
|---|---:|---:|---:|---:|---:|
| HPE | −18.7% | **92.3** | **+128%** | **+96%** | 100th |
| CIEN | −32.1% | 57.5 | +114% | +14% | 99th |
| ON | −17.6% | 66.0 | +107% | +30% | 100th |
| SMCI | −34.0% | 86.4 | +39% | +80% | 74th |
| AVGO | −23.0% | 73.5 | +36% | +16% | 100th |
| AA | −19.8% | 76.2 | +62% | +34% | 100th |
| QCOM | −23.4% | 62.0 | +53% | +34% | 99th |

HPE is the archetype: bought after a **+96% run in 20 days, RSI 92, at a 100th-%ile high** → −18.7%.

### 3c. No exit discipline

- **39 of 81 holdings (48%) are past −8%**, totalling **−£2,149 (86% of the loss)**.
- Concentrated in **semiconductors / high-beta tech** (SMCI, CIEN, QCOM, AVGO, RMBS, ON, NOW…) — one correlated theme drawing down together; no sector cap.

---

## 4. `intraday_flat` (IG intraday) — death by churn

- **Realised −£3,114** (after correcting a −£458 options-misattribution; see §6).
- **Win-rate 27.5%** (55 wins / 145 losses); avg win £11.23, avg loss £7.68 → **≈ −£2.50 expected per trade**.
- Per-name the *directional* P&L is near-flat but **cost (spread + financing) dominates**: e.g. AMD 24 trades, gross −£60 but **cost −£552**; Apple 21 trades, gross −£88, cost −£485.
- **Diagnosis:** marginal/negative edge **overtraded** into the ground by transaction cost.

---

## 5. `ichimoku_fx_mr` (IG FX) — fragile, concentrated

- **Realised +£222 net** — but carried by **two pairs**: AUD/USD +£234, GBP/USD +£157. The **other 8 pairs net ≈ −£170**; **6 of 10 are net losers**.
- **Open now −£114** (GBP/USD −£87, EUR/GBP −£35). GBP/USD was the 2nd-biggest realised winner and is now the biggest open loser — **mean-reversion getting run over by a trend.**
- **Net (realised + open) ≈ +£108** — not a robust edge; it's two trends that worked.

---

## 6. Data quality

| # | Severity | Finding | Impact |
|---|---|---|---|
| 1 | 🔴 | **Live bus feed has no garbage-bar guard** (the daily *cache* does, via `_drop_garbage_bars`; the live path in `bar_bus.py`/`sources/` does not) | Phantom bar (**UNH 377** vs real 406–413) fired a **false stop** today |
| 2 | 🟠 | **Cross-provider divergence** — signal/stop use yahoo, book is IBKR (e.g. **AMD yahoo ~460–500 vs IBKR ~203**) | IBKR-clone stops & P&L untrustworthy; A/B unmeasurable |
| 3 | 🟡 | **Two disconnected caches** — strategy reads `~/.tradepro/cache/` (yahoo); the quality-scored harvest writes `~/.tradepro/bar_cache/` (IBKR), **empty for daily equities + not wired in** | Harvest/scorecard work doesn't feed live trading |
| 4 | ✅ | **Daily signal *history* is clean** — ~4,133 bars/name, zero NaN, ~matches reality | The rot is the live feed (#1) + feed-mixing (#2), not the archive |

> Note: cache parquet columns are **lowercase** (`open/high/low/close/adj_close/volume`) — code must use `"close"`, not `"Close"`.

---

## 7. Prescription (prioritized, all post-session / live-path)

| # | Fix | Evidence | Expected impact |
|---|---|---|---|
| 1 | **Bus spike-guard** — mirror `cache._drop_garbage_bars` on live bus bars | phantom UNH 377 → false stop | Stops false trades immediately (safety) |
| 2 | **Entry-extension gate** — skip when >25–30% over 200-SMA / RSI>70 / top-decile 52w | losers entered far more extended than winners | Skips worst losers, keeps winners |
| 3 | **Pullback entry** — enter on a retrace to tenkan/kijun, not the extended breakout | entries at 97.5th %ile reverse | Better fills; winners run |
| 4 | **Stops (8%) + sector cap** — the IBKR clone | 39 names past −8% = −£2,149 | Caps the residual loss; diversifies the theme |
| 5 | **IBKR-native data** — daemon-side IBKR stop + IBKR daily into `cache.py` + clone P&L reconciliation | yahoo↔IBKR divergence | Correct stops; **A/B becomes measurable**; feeds "stress this book" |
| 6 | **`intraday_flat` anti-churn** — trade-frequency cap / conviction threshold / cost-aware gate | −£2.50 expected/trade | Stops the bleed on the worst desk |

**One-line takeaway:** *the signal is fine; the trader isn't in the loop yet.* Entry discipline + stops + clean data turn the existing book from a −£5k bleed into something tradeable — before any new strategies are added.

---

## 8. Quantified fix impact — simulated on the actual held book

Applying the two equity fixes to the **current 81-name book** (£45,063 cost basis, −£2,826 open):

| Scenario | Book P&L | vs actual | Effect |
|---|---:|---:|---|
| **Actual** | **−£2,826** | — | 21% win-rate |
| 8% stop only | −£1,838 | **+£988** | caps the 42 names past −8% |
| **Entry-filter only** | **−£15** | **+£2,811** | keeps 13/81 names; **win-rate 21% → 46%** |
| Both (filter + stop) | +£1 | +£2,827 | ≈ breakeven |

The **entry-extension filter is the dominant lever** (bigger than the stop): it would have skipped 68 names — **57 losers (−£3,047) but only 11 winners (+£236)** — shedding £3,047 of losses to forgo just £236 of gains, turning −£2,826 into ~flat. The stop is secondary: it limits damage *after* a bad entry; the filter avoids the bad entry entirely.

**Caveats (this is a snapshot estimate, NOT yet a walk-forward backtest):**
1. Scores the rule against names *currently held* — doesn't re-simulate which names would have been bought over time.
2. **Tape-dependent** — semis are currently selling off, which flatters a "don't buy extended semis" filter; in a melt-up it would forgo more (trend-following's fat winners live in extended names).
3. Optimistic stop fills (assumes exact −8% exit; real stops gap/slip).
4. Small kept sample (13 names).

**→ Confirmation step (in progress): walk-forward backtest** of `ichimoku_equity` with vs without the entry-extension gate over 1–2 years, comparing Sharpe / max-drawdown / return out-of-sample — to prove the gate generalizes and doesn't choke off the rare big trend winner. Results appended in §9 when complete.

---

## 9. Walk-forward backtest — the gate is sleeve-specific (and the strategy isn't broken)

`ichimoku_equity` sleeve, 3yr (2023-06 → 2026-06), costs on, gate OFF vs ON:

**LARGE_50 (mega-cap)**
| Config | Return | Sharpe | maxDD |
|---|---:|---:|---:|
| baseline | +24.1% | 1.34 | −5.8% |
| **25% + RSI≤70** | +24.2% | **1.46** | **−4.6%** |
| 15% + RSI≤70 | +20.7% | 1.43 | −3.6% |

**HIGH-BETA / semis (where the live losers are)**
| Config | Return | Sharpe | maxDD |
|---|---:|---:|---:|
| **baseline** | **+121.3%** | 1.54 | −11.4% |
| 25% over SMA | +83.3% | 1.57 | −10.7% |
| 25% + RSI≤70 | +67.3% | 1.47 | −9.7% |
| 15% + RSI≤70 | +44.9% | 1.28 | −6.3% |

**Findings (these correct the §8 snapshot, which was fooled by the current down-tape):**
1. **The strategy backtests well — it is NOT broken.** +24%/Sharpe 1.34 (mega-cap) and **+121%/Sharpe 1.54** (high-beta) over 3yr; maxDD only −11%. The live −£2.5k is a **normal drawdown** captured mid-pullback, not a broken signal.
2. **The entry gate is sleeve-specific:** a *free* Sharpe/drawdown improvement on **large_50** (1.34→1.46), but it **destroys the high-beta edge** (return 121%→45–83% for ≤0 Sharpe gain) — on momentum names the *extended entries are the winners*.
3. **Don't ship a blanket gate.** The on-book snapshot (§8) said "gate → flat"; the walk-forward says "gate craters high-beta." The snapshot dodged the names falling *now*; over 3yr those extended names mostly kept running.

**Optimization conclusion:**
- **large_50:** apply a mild gate (25% over 200-SMA + RSI≤70).
- **high-beta:** NO gate; add the 8% **stop** for tail protection (caps the −11% without killing the +121%).
- **The real gap is execution parity** — the backtest is idealized (clean cache, close-based). The live bleed vs this backtest is phantom-bar data + MOO timing + costs + missing stop. **Fix the live execution to match the backtest; don't change the signal.**

> *Method note:* gate implemented as opt-in `entry_max_ext_pct` / `entry_rsi_max` in `quant_engine` (default OFF → backtest parity; 17/17 sleeve tests pass). Backtest run on the clean daily cache, regime filter off (isolates the gate effect), large_50 + a 33-name high-beta/semis set.
