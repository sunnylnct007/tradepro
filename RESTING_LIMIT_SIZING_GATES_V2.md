# Resting limit at reduced size — pre-registered. 24 Aug 2026.

**Committed BEFORE the run.** Follow-up to `RESTING_LIMIT_GATES_V1.md`, which
rejected all four variants.

## Why v1's rejection may have measured the wrong thing

Variant E (limit at the level + trend re-check on the fill bar) beat the
shipped rule on three of four measures:

| | trades | win% | mean% | tail% | worst% |
|---|---|---|---|---|---|
| control (the rule) | 2,501 | 72.9% | +0.96% | 19.8% | **-23.5%** |
| E — limit + trend recheck | 6,748 | **74.9%** | **+1.15%** | **15.6%** | **-28.1%** |

It failed on R3, worst trade >= -25%.

**But R3 is a PER-TRADE PERCENTAGE, and percentages do not care about position
size.** A -28% trade at half size costs the same as a -14% trade at full size.
The gate as written cannot see that, so it rejects a variant whose actual
portfolio risk might be lower.

That is a flaw in my gate, not necessarily in the variant — and it is worth
saying plainly that I only noticed it because the variant failed.

## So this measures the PORTFOLIO, not the trade

Equity curves, £100k start, positions taken as they arise up to a cap:

* **A — control at 5%/position** — the shipped rule, the live daemon's sizing.
* **B — variant E at 5%** — same size, 2.7x the trades.
* **C — variant E at 2.5%** — half size, the direct test of the idea.
* **D — variant E at 1.67%** — third size.

## Gates

| # | Gate | Threshold | Why |
|---|---|---|---|
| S1 | Total return | >= the control's | More trades at a better average should compound to more, or there is no point. |
| S2 | **Max drawdown** | **<= the control's** | The whole claim. If smaller positions do not buy a shallower drawdown, sizing is not the answer. |
| S3 | Return per unit of drawdown | >= the control's | The honest risk-adjusted comparison — S1 and S2 can both be gamed by leverage in opposite directions. |
| S4 | Capital utilisation | <= 100% at all times | A variant that only wins by being more invested is not better, it is bigger. |
| S5 | **Survives both splits** | S1 and S2 hold in all 4 cells | The test that has killed four candidates this month. |

## Prediction, on record

1. **C or D passes S2** — halving size should roughly halve drawdown; that is
   arithmetic, not insight.
2. **S1 is where it dies.** Half size on 2.7x the trades ends up at similar
   deployed capital, so the extra trades will not compound into a materially
   larger return — I expect C's total return to land within a few points of the
   control, either side.
3. **S4 is a real risk and I have not checked it.** 6,748 trades against 2,501
   means far more concurrent positions; at 5% each the limit variant may exceed
   100% invested, which would make B's apparent return leverage rather than
   skill.
4. **S3 is the gate that decides it**, and I genuinely do not know. ~35% that
   anything ships.

If nothing passes, the sizing idea is closed and the file joins the others.
