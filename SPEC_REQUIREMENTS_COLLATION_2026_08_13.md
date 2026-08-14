# Spec — Requirements collation, week ending 2026-08-13

**Repo state at drafting:** `5af78e1`
**Status:** requirements. Each item states its own acceptance test.

This collates everything surfaced by five sessions of live trading that is **not
already covered** by the two existing specs. Those remain current and are not
restated here:

- `SPEC_OPTIONS_SHORTDATED_AND_EARNINGS_2026_08_12.md` — short-dated CSP tier,
  earnings straddle scanner, backtest requirements
- `SPEC_LEVELS_TO_PROBABILITIES_2026_08_13.md` — implied-move ladder, OI
  distribution, microstructure, structure verdict, calibration testing

Ordered by priority. Items 1–3 each cost a real decision this week.

---

## R1 — The wheel screen is news-blind (highest priority)

### Measured state

| File | news / sentiment / catalyst refs |
|---|---|
| `quant_engine/options/risk.py` | **0** |
| `cli/options_screen.py` | **0** |
| `quant_engine/options/chains_ibkr.py` | **0** |
| `compare.py` (equity screener) | 181 |

The screen the owner actually trades from reads no news. The one he doesn't use
reads plenty.

### The worked failure — ORCL, 2026-08-13

ORCL surfaced as the **best vega read of the week**: IV/HV 1.25, IV percentile
85.7 / 81.7 / 90.8, all three windows clearing the >30 gate comfortably, tight
spread, deep ADV. Recommended for pricing at the Sep04 140 strike (~30%/yr).

What no gate saw:

- S&P Global downgraded Oracle to **BBB−** from BBB — one notch above junk
- **>$122bn long-term debt**; $43bn issued in FY2026, ~$40bn more planned for
  FY2027 including a **$20bn at-the-market equity programme** (dilution)
- Roughly **half the $638bn backlog is tied to OpenAI** — single-customer
  concentration
- Negative free cash flow; estimates of **$80bn to burn** before self-funding
- Worst week since the 2001 dot-com bust; headcount down 13%
- The 13 July −6% session was driven by **Apple suing OpenAI** — i.e. ORCL now
  trades on litigation against its largest customer

Selling that put means agreeing to **buy ORCL at 140**. The strike-as-entry test
was invoked with no information capable of answering it.

### The structural insight

**The wheel screen selects for high IV percentile and never asks why IV is
high.** Elevated implied vol is not a free lunch — it is the market pricing
something. When the something is credit risk, the premium is fair compensation
for a real tail, and selling it means underwriting that tail unknowingly.

### Requirement

Add a **`why_is_iv_elevated`** field, computed for any candidate that passes the
vega gate. Not a news feed — three specific inputs, all with existing
infrastructure:

| Input | Source | Why this one |
|---|---|---|
| **Credit rating actions** | `catalysts_sec_edgar.py` / provider | Binary and unambiguous — no sentiment scoring needed. Highest value of the three |
| **Drawdown attribution** | `catalysts_gdelt.py` + `catalyst_llm.py` | A name >40% off its high gets its *reason* surfaced, not just its percentage |
| **Counterparty / customer concentration** | filings | ORCL's OpenAI dependence is the entire story and it is disclosed |

`catalyst_llm.py` then synthesises one line, e.g.:

> *"IV at the 90th percentile following an S&P downgrade to BBB− and OpenAI
> customer concentration."*

That is enough to stop a trade without needing to be right about direction.

### Gate behaviour

- A **credit downgrade within 90 days** BLOCKS a new CSP. Not a warning — a block.
- A drawdown >40% from the 52-week high with no attribution available renders
  `UNVERIFIED` and blocks, per the existing absence rules.
- Everything else renders as context alongside the vega numbers.

### Acceptance test

Replay ORCL at 2026-08-13 through the screen. It must not appear as eligible,
and the reason must name the downgrade.

---

## R2 — The screen cannot see the open book

### Measured state

TradePro proposed **SLV four times in five sessions** — 10, 11, 12 and 13 August
— while the owner has been short the SLV Sep18 54P throughout. On 13 August it
proposed the **$54.50 strike**: materially the same contract he already holds.

Separately, nothing computes aggregate assignment exposure. Current book:
MRVL 200P $20,000 + SLV 54P $5,400 + VZ 45P $4,500 = **$29,900, 19.4% of NAV**,
computed by hand every time.

### Requirement

1. **Position-aware ranking.** Every candidate row shows existing exposure to the
   same underlying. A candidate on a name already held renders as
   **`ADD TO EXISTING`** with the current position stated, never as a fresh
   candidate.
2. **Aggregate assignment budget.** Σ(strike × 100) across all open short puts,
   as a share of NAV, broken down by sector and by theme. Correlated assignment
   is the real risk — every put filling in the same drawdown.
3. **Correlation flag.** Warn when a candidate shares a theme with an existing
   position. Live example: the MRVL short put and the ORCL candidate are both the
   AI-capex trade; SLV and GDX are both precious metals.

### Acceptance test

With the current book loaded, an SLV candidate must render `ADD TO EXISTING —
short 1× Sep18 54P` and the aggregate budget must read 19.4% of NAV without
manual arithmetic.

---

## R3 — Event windows beyond earnings

### The gap

The earnings gate works — it correctly blocked ORCL, NVDA, HPQ and DELL this
week. But **ETFs are structurally exempt from it**, and that exemption is being
read as "no event risk".

Live example, 2026-08-13: **KRE** (regional banks) passed every gate at the Sep18
expiry. The **September FOMC is 15–16 September**. Sep18 expiry holds straight
through the rate decision — on the most rate-sensitive sector in the market. For
KRE that is the equivalent of an earnings print, and nothing in the screen sees
it.

### Requirement

Extend the earnings blackout into a general **event-window gate**:

| Event class | Applies to | Source |
|---|---|---|
| Earnings | single names | existing `earnings_calendar` store |
| **FOMC decisions** | rate-sensitive: financials, REITs, utilities, TLT, KRE, XLF, XLU | published calendar, fixed dates |
| **CPI / PPI releases** | broad market, metals | published calendar |
| **OPEC meetings** | energy: XOM, CVX, XLE, OXY, SLB | published calendar |

All are known years ahead. This is a static table plus a sector mapping, not a
feed.

### Acceptance test

KRE at the Sep18 expiry must render an FOMC-in-window flag. The Sep11 expiry
must not.

---

## R4 — Stop crowning candidates on the IV/HV bridge alone

### The problem

The bridge (`IV/HV ≥ 0.95`) exists as a stand-in until the local IV dataset
matures to 60 days. It is currently at ~5 days. But the bridge is a **ratio**,
and a ratio is flattered when the denominator collapses.

Verified against IBKR, 2026-08-13:

| Name | Bridge says | IBKR IV percentile 13w/26w/52w | Verdict |
|---|---|---|---|
| **KRE** | 1.28 — eligible | **0.0 / 0.0 / 0.0** | cheapest vol of the year |
| **IWM** | 1.02 — eligible | **0.0 / 0.0 / 0.0** | cheapest vol of the year |
| SLV | 0.97 — eligible | 42.9 / 21.4 / 40.2 | genuine pass |

Two of the three names crowned eligible were at the **absolute floor** of their
implied-vol range, sitting at 52-week highs. The bridge passed them because
realised vol had fallen even further than implied. IBKR's own chain header
confirms it independently: KRE 52W IV Rank **2**, IWM 52W IV Rank **0**.

Selling vol at a zero percentile has one-directional asymmetry — it can only
mean-revert upward.

### Requirement

**IBKR already serves `implied_volatility_percentile` over 13w/26w/52w today.**
Consume it now rather than waiting for the local 60-day window.

- Where IBKR serves a percentile, it is the **primary** gate; the bridge is not
  consulted.
- Where it does not, the bridge applies and the row is labelled `PROVISIONAL`
  as now.
- **When bridge and percentile disagree, block and show both.** A contradiction
  is information, not a tie to be broken silently.

### Acceptance test

KRE and IWM at 2026-08-13 must not render eligible. SLV must.

---

## R5 — "Closest to clearing" is misleading

On 2026-08-12 the screen listed INTC, FCX and DAL as *closest to clearing*, each
blocked solely on `IV-Rank unavailable`. That framing implies the missing field
is a formality.

When INTC's data arrived the next day it read **IV/HV 0.81, IV percentile 3.2 /
28.6 / 55.0** — the missing gate was the one that disqualified it.

### Requirement

Split the "closest" list into two, and label them differently:

- **Blocked on a known value** — genuinely near-miss. Example: IWM blocked on
  OI 213 vs a 250 threshold.
- **Blocked on an unknown value** — status is *unknown*, not *nearly eligible*.
  Unknown resolves either way.

Never rank the two together.

---

## R6 — Scheduled catalyst calendar (directional track — park behind Phase 1)

Raised via the 2026 World Cup lifting lodging stocks. Investigation showed the
theme was **priced months in advance** — MAR +14.5% and HLT +9% YTD by early May,
with Goldman and Deutsche Bank beneficiary lists published before the 11 June
kickoff. Expectations were then *cut* (Hilton's CEO in April: the tournament
"doesn't look as strong as what we had hoped"), and the tradeable move came
**after** the event, on 20 July, when CoStar data beat the lowered bar and
lodging REITs hit new highs on a down day for the indices.

**So the edge was never in the event.** It was in the expectation reset and the
subsequent beat — and it paid best in second-order names (DiamondRock at 34%
host-city exposure) rather than MAR/HLT.

### Requirement

A **scheduled catalyst calendar**: dated, known-in-advance events (World Cups,
Olympics, elections, index rebalances, product cycles) mapped to affected
sectors and symbols. This is a calendar, not a news feed — and unlike breaking
news it is **backtestable**, because the events have historical dates and the
prices exist.

Route it through the generalised event-study engine (§5.6 of the refactor
brief): trigger = *N sessions before a scheduled catalyst affecting sector X*,
measure forward returns split by regime, pre-register the gates. That answers the
real question — does the drift happen at announcement, into the event, or on the
post-event data?

**Explicitly park breaking-news trading.** By the time it is readable it is
priced, and post-hoc every move has a story attached. That is where hindsight
bias lives.

**Sequencing:** this is directional-track work. Hold it behind Phase 1 per §6 of
the refactor brief — directional gets instrumented, not automated.

---

## Priority and cost

| # | Item | Cost | Cost of not doing it |
|---|---|---|---|
| R1 | news / why-is-IV-elevated | medium | recommended a BBB− credit story as the week's best candidate |
| R2 | position awareness | **low** | proposed the same SLV contract four times |
| R4 | consume IBKR IV percentile | **low** | crowned two zero-percentile names as eligible |
| R3 | event windows beyond earnings | low | KRE through an FOMC undetected |
| R5 | closest-to-clearing semantics | **trivial** | INTC framed as nearly eligible when the missing gate disqualified it |
| R6 | scheduled catalyst calendar | high | a directional opportunity, parked by design |

**R2, R4 and R5 are small and would have changed three of this week's
conversations.** R1 is the one that matters most and the one that costs real
money when it is missing.

---

## Standing constraints (apply to all of the above)

1. **Per-field `as_of`.** A single MU pull on 2026-08-13 returned a stock quote
   **2.4 hours newer** than the option marks on the same underlying — which is
   how a 960 put ended up marked above a 960 call at spot 962. Every field
   carries its own timestamp; mixed-freshness computations carry the oldest.
2. **Absence is rendered, never inferred.** Three states — value, `STALE
   (as_of)`, `UNAVAILABLE (reason)`. A gate firing on ~100% of rows is a feed
   failure, not a score.
3. **Nothing ships to the live desk before its test clears.** Same protocol that
   killed QDB and failed the wheel backtest 3 of 4. Thresholds committed before
   the run.
4. **No net growth in surface area during Phase 1.** Anything new names what it
   replaces.
