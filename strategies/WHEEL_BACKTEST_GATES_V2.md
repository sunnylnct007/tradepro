# Wheel backtest v2 — PRE-REGISTERED pass/fail gates

**Committed BEFORE the first v2 run** (13 Aug 2026). v1's gates file
(`WHEEL_BACKTEST_GATES.md`, commit `5817fe2`) is immutable and stays as the
record of that test; this is a NEW file for a NEW test, per its own §4 rule.

**Thresholds are UNCHANGED from v1.** v2 changes what is *simulated*, not what
counts as success — lowering a bar after a failure would make the whole
protocol theatre. One gate is ADDED (G5, correctness).

## What changed vs v1 — and why the result may get WORSE

v1 simulated a *dumber* wheel than the one deployed: it sold a put every time
it was flat, at any premium, in any regime, into any earnings print. v2 models
the live desk's actual gates:

| Modelled in v2 | Rule | Source of truth |
|---|---|---|
| Premium floor | ≥ $0.20/share **and** ≥ 8%/yr on strike collateral — **both legs** | `OptionsRiskConfig` live defaults |
| Regime gate | new CSPs only when regime ∈ {GREEN, YELLOW} and not falling-knife | `regime_from_closes()`, the same pure function the live screen calls |
| Earnings veto | no new CSP when a print falls in [today, expiry] | historical print dates (see coverage caveat) |
| Idle cash | collateral + undeployed cash accrues **4%/yr**, daily | `idle_cash_rate` (registered ON) |

The owner's hand-check of v1 predicted the consequence, recorded here before
the run: the premium floor alone deletes whole cycles (COP-2020: 12 of 14 legs
sub-floor; KO-2022: 9 of 13), so **utilisation should collapse from ~95% and
trade count should fall hard**. Fewer, better trades. Income falls with them;
whether the survivors clear an 8%/yr NAV bar is exactly what G3 tests.

## Coverage caveats — declared before the numbers exist

1. **Earnings gate is UNMODELLED in the 2020 window.** yfinance serves 24
   prints per name, earliest ~Oct 2020. So 2022 and the full period model the
   veto properly; **the 2020 window does not**, and its result is therefore
   optimistic in that one respect. Reported per window as a disclosure, never
   silently.
2. **The IV proxy is unchanged and still wrong in a known direction.**
   Premiums are Black-Scholes on trailing 30d realised vol, which spikes after
   a gap when real IV collapses (the verified META case: modelled $19.04
   against a real ~$8.50). This **overstates income**, most on the worst
   trades. Fixing it needs real historical option quotes — the forward capture
   store (`option_quote_daily`, started 13 Aug 2026) exists for exactly that,
   and no v2 conclusion may claim the proxy is adequate.
3. **Assignment is expiry-only** (European-style); early assignment is not
   modelled, which removes real losses.
4. **Adjusted-close space** — strikes are not quotable historical contracts,
   and dividends are implicitly credited.

Caveats 2–4 all flatter the wheel. A *failing* v2 is therefore a strong
result; a *passing* v2 must be read against them.

## Fixed parameters (not tunable after the fact)

Identical to v1 except the four modelled gates above: universe =
`DEFAULT_UNIVERSE` names with ≥260 cached daily bars before the window's
effective start; $25,000 slice per symbol; 1 contract; strikes ~5% OTM;
30 DTE; 60-bar warm-up; costs = 5% premium haircut + $1.50/contract/leg;
windows 2020 (2019-10-01→2020-12-31), 2022 (2021-10-01→2022-12-31), full
(2019-10-01→latest cached bar).

## Gates

| # | Window | Test | Pass |
|---|--------|------|------|
| G1a | 2022 | Portfolio net total return | ≥ −10% |
| G1b | 2022 | Net return minus same-universe buy-and-hold | ≥ +8 pts |
| G1c | 2022 | Portfolio max drawdown | ≤ 25% |
| G2a | 2020 | Portfolio net total return | ≥ 0% |
| G2b | 2020 | Portfolio max drawdown | ≤ 30% |
| G3 | Full | Net CAGR on total NAV (idle collateral included) | ≥ 8%/yr |
| G4 | Each | Worst single-symbol max drawdown on its slice | ≤ 40% |
| **G5** | **2022 + full** | **CSPs opened whose expiry window contained a known print** | **exactly 0** |

G5 is a **correctness** gate, not a performance one: in the coverage era the
earnings veto either works or the simulation is lying about what it modelled.
Any occurrence invalidates the run rather than scoring it.

Disclosures (reported every run, never gated): utilisation, trade count, peak
simultaneous assigned names, cost drag, premium income vs realised share P&L,
and **earnings-gate coverage per window**.

## Expected outcome — recorded before the work

Utilisation collapses; trade count falls sharply; the XOM/KO-class sub-floor
cycles vanish entirely. **G4 most likely improves** (META-class entries get
regime-blocked before assignment — verified: the live gate reads RED +
falling-knife on 2022-02-03). **G3 remains the hardest bar**: with utilisation
low, NAV return leans on 4% idle cash, and 8%/yr is a real bar for a sleeve
that is flat most of the time. My honest prediction: **G3 still fails, G4
passes, G1b uncertain.**

Recorded so that a pass gets scrutinised and a failure is not treated as a
reason to tune until it clears. If v2 fails, the next move is a decision about
the wheel — not another parameter.

## Phase 1

Phase 1 closes when **G1–G5 all pass on these numbers as committed**. Nothing
else closes it.
