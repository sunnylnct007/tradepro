# Every strategy is graded against these. Written 26 Aug 2026.

Owner: *"if we're not going to use momentum, why did we build it?"*

Because it passed the gates it was given, and **the gates were incomplete.**

Momentum cleared all six of `MOMENTUM_GATES_V2.md` — 5,396 trades, 48.8% win,
+2.2%/trade. Every one of those gates measures TRADE QUALITY. Not one asked how
much capital the strategy needs AT ONCE. When that was finally asked, on
25 Aug: median **55 concurrent positions**, peak 161. At the 5% per position the
forward simulation assumed, that is 275% of capital in the normal case, and at
any cap you could actually finance the mean trade is NEGATIVE.

It was never fundable. Eight weeks of work went into a strategy no gate could
reject, because no gate was pointed at the thing that was wrong.

**Swing had the same hole.** Median 7 concurrent, peak 62 — 310% of capital at
the peak — and it was two days from a twelve-week forward test with nothing
bounding it. The capacity study that killed momentum is what produced Swing's
cap of 12 and its ranking rule.

## The gates, in two groups

### Group A — is the EDGE real? (what we already had)

| # | test |
|---|---|
| V0 | >= 1,000 trades |
| G1 | win rate clears its stated threshold |
| G2 | mean net return > 0 after costs |
| G3 | median hold within the stated horizon |
| G4 | top 1% of trades <= 25% of net profit — not one lucky trade |
| G5 | worst single trade within the stated tolerance |
| — | the TWO-SPLIT: survives a TIME split AND a SYMBOL split, all four cells |

### Group B — can it be FUNDED? (the group momentum would have failed)

| # | test | why |
|---|---|---|
| **C1** | **concurrency measured, not assumed** — median, p95 and peak simultaneous positions over the full window | Nobody had ever computed it. It is one loop over the trade list. |
| **C2** | **peak concurrency x position size <= 100% of capital** | A strategy that needs 275% of capital is not a strategy, it is leverage nobody chose. |
| **C3** | **the per-trade edge SURVIVES the cap** — re-graded at the concurrency limit that will actually be enforced | Momentum's +2.25% becomes NEGATIVE at every fundable cap. G1-G5 on uncapped trades describe a strategy you cannot run. |
| **C4** | **the selection rule is graded, not assumed** — if the cap binds, WHICH signals get taken must beat the arbitrary control on the two-split | Swing lost more than half its edge (+1.10% -> +0.52%) to taking signals alphabetically. Selection is load-bearing whenever a cap binds. |

## The rule that follows

**Group A without Group B is a description of trades, not of a strategy.**
Momentum passed A and fails B. Swing passes both, but only since 25 Aug — and
only because momentum failed first and made us look.

No strategy is funded, forward-tested or given a slot on the desk until both
groups are graded and written down before the run.
