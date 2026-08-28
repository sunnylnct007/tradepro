# Post-earnings cash-secured puts — PRE-REGISTERED gates

**Committed BEFORE the graded run (28 Aug 2026).** Exploratory numbers already
exist and are quoted below as the PRIOR, not as a result. The point of this
file is that the gates are fixed while the outcome is still unknown.

## What the owner asked for

> *"for the wheel I have a simpler strategy. for e.g MRVL is a good stock and
> was trading at 240 before quarterly result and now it corrected to 220 so i
> can safely play a put at 195. this strategy can work normally after every
> quarterly results"*

and, clarifying the universe:

> *"the strategy i m saying is not fully a wheel but we will consider only
> symbols we happy to hold"*

and, choosing how to apply that:

> universe = **all 244, sized by volatility** — cap the tail by SIZE rather
> than by excluding names.

## The thesis, stated so it can fail

Selling a put AFTER a quarterly report is safer than selling one before it,
because the binary gap risk is spent. The next report is ~90 days out, beyond a
30-day expiry, and the stock has already repriced. Assignment is acceptable —
these are names the owner wants to own — so the measure is P&L, not
assignment rate.

The thesis is FALSE if the post-report drop signals more downside rather than
less. That is the real question, and it is not obvious: volatility clusters.

## What is being tested

| | |
|---|---|
| trigger | a confirmed EARNINGS report date, from `earnings_history.json` |
| entry | first session AFTER the report, when the stock fell ≥ 8% on it |
| structure | cash-secured put, 10% OTM, 30 sessions |
| premium | Black-Scholes at realised vol measured BEFORE the drop (the IV-crush proxy — deliberately pessimistic) |
| exit | expiry; assigned if below strike |
| sizing | collateral scaled by `target_vol / symbol_vol`, so a 60%-vol name gets ~1/3 the size of a 20%-vol name |
| costs | commission + half-spread, charged per contract |

Sizing is part of the STRATEGY, not a presentation choice, so gates are graded
on size-weighted returns.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Events (validity) | ≥ 300 real earnings events |
| **G1** | Win rate | ≥ 80% |
| **G2** | Mean return per trade, NET of costs, size-weighted | > +0.75% of collateral |
| **G3** | **Beats the null** — same test on random non-earnings entries | mean must exceed it by ≥ 0.5pt |
| **G4** | 5th-percentile trade | ≥ −8% |
| **G5** | **Two independent halves** (2020-2023 / 2023-2026) both pass G1 and G2 | true |
| **G6** | Survives the IV crush — premium at PRE-drop vol | still passes G2 |

**G3 is the gate that matters most, and it is the one the exploratory work has
not yet run.** Selling any 10% OTM put returned +0.44%/trade in this universe.
If the post-earnings version cannot clearly beat that, the earnings event is
decoration and the edge is just "selling puts works", which we already knew.

**G4 is set at −8%, not at the worst trade.** Vol-scaled sizing is supposed to
cap the tail; if the 5th percentile is still worse than −8% after scaling, the
sizing rule has not done its job and the strategy is not what it claims.

**G5 is here because this repo has been burned by exactly this.** The v3 volume
filter looked strong and was rejected when a time split showed the edge
inverted pre-2020. A single-window pass is not evidence.

## The honest limit, recorded up front

**Earnings history reaches back only to ~October 2020** (yfinance
`earnings_dates`, 25 rows/symbol, measured 28 Aug 2026). A pre-2020 regime test
is IMPOSSIBLE for this strategy at any bar depth — deeper bars do not help when
there are no report dates to pair them with.

So G5 splits 2020-2023 against 2023-2026. That is two independent halves of one
regime, not two regimes. **A pass here licenses paper-forward testing, never
funding**, and any published result must carry this sentence.

## Prediction — recorded before the graded run

**G1, G2 and G6 pass.** The exploratory run gave 88.6% win and +2.49%/trade,
falling only to +2.23% when priced at pre-drop vol, over 604 drop-events.

**G3 is where I expect trouble.** The exploratory comparison was +2.49% against
a +0.44% baseline, which looks decisive — but that baseline sampled every 5th
session indiscriminately, while the earnings set is concentrated in volatile
names and volatile weeks. A like-for-like null may close much of that gap.

**G4 is the second risk.** Unscaled, p5 was −9.9%. Vol-scaling should pull that
inside −8%, but that is a prediction, not a measurement.

**G5 I expect to pass**, because both halves sit in the same post-2020 regime —
which is precisely why passing it proves less than it appears to.

If G3 fails, the correct conclusion is that the earnings trigger adds nothing
and the owner should simply sell puts on a schedule. The answer is NOT to move
the drop threshold until something passes.

## Scope

Clearing these gates licenses a PAPER FORWARD TEST and a candidate surface. It
does not license funding. The existing wheel verdict (v3: DO NOT FUND) stands
independently of this study.
