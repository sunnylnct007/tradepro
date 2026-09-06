# Feedback on MU Pre-Earnings Swing & Core Spec v1.0

From: TradePro desk · 6 Sep 2026
Evidence: 44 MU pre-print windows (2015→2026), ATR-stop calibration over the
26 qualified setups. Code + artifacts: github.com/sunnylnct007/tradepro,
branch live-main — strategies/tradepro_strategies/cli/preearnings_profile.py,
strategies/backtests/mu_preearnings_profile.json, mu_atr_variants.json
(commits f4e51a2, 3826826).

## Verified independently before anything else

- Every observed value in §3.1 matches our own bar store to within pennies
  (close 1016.59 vs 1014.91 one session apart; EMA20 944.38/944.22;
  SMA50 938.28/938.25; SMA200 606.75/606.47). The chart work is accurate.
- The earnings date was the one real data hazard: our calendar held TWO
  forward dates (2026-09-21 and 2026-09-30) from source drift. Owner
  confirmed 30 Sep AMC; the phantom row is deleted and the confirmed date is
  stamped. §3.4's "cannot remain hardcoded" rule is right — wire the module
  to a validated calendar, and treat a source disagreement as a blocking
  condition, not a coin flip.

## What the evidence supports keeping exactly as written

1. **The mandatory pre-print swing exit.** Across 44 windows it forgoes
   +0.1% of mean return to avoid a −8.6% worst overnight gap (|gap|≥5% in
   17/44, ≥10% in 5/44). Near-free tail insurance. Do not soften it.
2. **The setup family.** EMA20 pullback-and-reclaim occurred in 26 of 44
   pre-print windows; the spec's path (reclaim → exit before print) ran
   +2.7% mean, 73% win, worst −15.8% as a daily analogue. Frequent enough to
   monitor, good enough to calibrate on — not proof of durable alpha (n=26,
   one symbol, a decade-long bull run, no comparison arm).
3. **The framing correction you already made:** MU drifts UP into prints
   (+1.2% mean over the last 5 sessions; a ≥4% pre-print selloff appeared in
   only 8/44). Pullback/reclaim into typical drift, not a DELL-style
   washout hunt.
4. Position tags, anti-averaging, options-as-context (§13.3's prohibited
   conclusions in particular), alert-first phasing, the audit journal, and
   the framework/parameters separation in §18. All house-compatible; §13.3
   we would adopt verbatim for our own screens.

## The one mandatory change (agreed with your revision)

**Fixed-dollar invalidation is structurally too tight for current MU vol**
(ATR14 ≈ $55, 5.4% of price; the spec's distances are 0.4–0.5×ATR; winning
setups' MAE averaged −3.9% ≈ 0.7×ATR). Calibration over the 26 setups, with
the untestable 15m rule BRACKETED between a daily touch-stop (harshest) and
daily close-stop (most lenient):

| stop | win | mean | mean-R | stop-outs | stopped-but-would-have-won |
|---|---|---|---|---|---|
| 0.5 ATR touch | 46% | +1.8% | +0.95 | 50% | 7 of 13 |
| 0.5 ATR close | 65% | +2.4% | +1.24 | 27% | 2 |
| 0.8 ATR touch | 62% | +2.2% | +0.75 | 31% | 3 |
| 0.8 ATR close | 65% | +2.4% | +0.77 | 23% | 2 |
| 1.0 ATR touch | 62% | +1.9% | +0.53 | 31% | 3 |
| 1.0 ATR close | 65% | +2.4% | +0.63 | 19% | 2 |

We endorse **0.8 ATR as the default — on robustness, not return**. Note the
trap: 0.5 ATR/close is the best risk-adjusted cell (+1.24R), but the tight
stop's outcome swings violently on WHICH intraday confirmation applies
(46%↔65% win across the bracket) — precisely the thing no daily backtest can
verify. 0.8's result is nearly identical under both readings. 1.0 buys no
fewer touch-stops than 0.8 and costs worst-case and R. Your
structure-plus-ATR selection with the REVIEW_REQUIRED overflow is right as
drafted; risk-sized quantity replaces the fixed 25-share clip (which at
$1,016 is a ~$25k notional decided by an arbitrary distance).

## Items to fix in v1.1

1. **§18.3's own REQUIRED fields are unset for MU**:
   `max_risk_per_trade_currency` and `max_gap_risk_currency` have no values.
   The sizing formula cannot run without the first; the CORE-through-earnings
   acknowledgement (§12.3) is empty without the second.
2. **C1 bypasses the regime filter** — necessarily, since 935–955 sits below
   the SMA50 at 938, where `long_regime` is false by definition. Deliberate,
   but say so explicitly: as written, the regime filter protects SWING only
   and CORE buys weakness with manual approval as its only guard.
3. **The profit-review zones have the same disease as the stops**: fixed
   dollars (1,070–1,080 / 1,095–1,110 ≈ 1.0–1.7 ATR above reference). Keep
   them as chart reference, but the review triggers should be dynamic
   (ATR/structure objectives recalibrated as the trend evolves) — same
   argument that rewrote the invalidations.
4. **`MATERIAL_BREAKDOWN`, `material_*_news_flag`, `no_unreviewed_material_news`
   have no feed or definition.** Either define them mechanically (e.g. SMH
   close below SMA50 on expanding volume) or mark them as MANUAL-INPUT
   fields with a default of "not reviewed" — otherwise DATA_OK is partly
   undefined and the gate cannot fail loudly.
5. **Gap-down protection (§8.6) keys on a fixed $955.** ATR-ize it (e.g.
   open below EMA20 − 1.0×ATR) or it decays with every volatility change,
   like the stops did.
6. **§19.1's reclaim-vs-blind comparison cannot be done historically** —
   intraday history does not exist at depth. Amend it to a FORWARD
   requirement: the journal must record the touch time, the reclaim time,
   and the hypothetical blind-fill price for every setup, so one earnings
   cycle of paper operation produces the comparison the backtest cannot.
7. **Alert hygiene (add to §14):** every alert fires once per state change
   (stateful/deduped), rides one delivery channel, and the whole alert set
   auto-expires after the 30 Sep cycle — renewal is a decision. Without
   this, ten alert types on one symbol becomes noise, and noise is how real
   signals die.
8. Minor: sector proxy SMH is fine as DATA (we harvest it), though a UK
   retail account cannot trade it (PRIIPs) — irrelevant for v1 but worth a
   note if the framework ever trades the hedge.

## Bottom line

v1.0 is the most disciplined spec this desk has reviewed, and the evidence
strengthens it rather than undermining it. With the ATR risk rewrite (your
draft is correct), the v1.1 items above, and dynamic zones, we are ready to
build Phase 1 — alerts only, config-driven, nothing executable — and to run
one full journaled MU cycle as the forward test §19 actually needs.
