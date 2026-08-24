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

---

# RESULT — REJECTED. And the near-miss is the most instructive part.

Portfolio simulation, £100k, positions held from entry to exit, signals skipped
when capital is not free.

| variant | size | taken | skipped | return | max DD | ret/DD |
|---|---|---|---|---|---|---|
| A control (the rule) | 5.0% | 2,173 | 328 | 132% | -13.3% | 9.9 |
| B variant E | 5.0% | 5,221 | 1,527 | 2,066% | -17.9% | 115.3 |
| C variant E half | 2.5% | 6,454 | 294 | 499% | -13.2% | 37.8 |
| D variant E third | 1.67% | 6,707 | 41 | **256%** | **-9.0%** | **28.3** |

**B fails S2** — more trades at full size deepens the drawdown, as expected.
**C and D pass S1-S4**, and D looked outstanding: nearly double the control's
return at two thirds of its drawdown.

## Then S5, and the reason this file matters

D passed the two-split — **until the split was the one actually
pre-registered.**

The gates specify a TIME split and a SYMBOL split. The first run substituted
odd/even entry DATES for the symbol cut, because the trade tuples did not carry
the symbol. Under that substitute, D passed all four cells.

Date parity is a much weaker test: it splits the same symbols across both
cells, so it cannot detect an effect that lives in a subset of names. With the
real symbol split:

| cell | D return | control | verdict |
|---|---|---|---|
| time 1st half | 63% | 38% | PASS |
| time 2nd half | 118% | 68% | PASS |
| symbols A-M | 119% | 76% | PASS |
| **symbols N-Z** | **64%** | **76%** | **FAIL** |

**D's advantage lives in half the alphabet.** That is the signature of an
effect concentrated in a subset of names rather than a property of the rule —
exactly what the symbol split exists to catch, and exactly what date parity
cannot see.

## Three accounting bugs on the way, all caught by implausible output

1. P&L credited at ENTRY rather than exit — returned 2,384%.
2. Equity marked INSIDE the settle loop with the position list half-rebuilt —
   returned a 95% drawdown on the control, which has none.
3. The substituted split above.

Each was caught because the number was obviously wrong, not because the code
was reviewed. A portfolio simulation with overlapping positions is fiddly, and
this one needed three attempts.

## Prediction grading (committed at 6540686)

| # | predicted | actual | |
|---|---|---|---|
| 1 | C or D passes S2 | both did | right |
| 2 | S1 return is where it dies | S1 passed comfortably (256% vs 132%) | **wrong** |
| 3 | S4 utilisation is a real risk I have not checked | never binding — skips handled it | wrong, and worth noting I flagged an unchecked risk that turned out not to matter |
| 4 | S3 decides it, ~35% ships | S5 decided it; nothing shipped | half right |

## Consequence

**No change.** Swing keeps the settled-close entry at 5% per position.

The resting-limit family is now closed across two studies. It reliably finds
more trades at a better average, and it reliably fails to survive being cut by
symbol — in v1 on the tail, in v2 on where the return comes from. That is
consistent enough to treat as a property of the idea rather than bad luck.
