# SWING TREND FLOOR GATES V1 — is the 200-day floor too harsh? Frozen BEFORE the run.

10 Sep 2026. Owner (third time raising it, most recently on SHOP refused at
-2.9σ for sitting 3.3% below its 200-SMA): "is 200 day a bit harsh".
Precedent: the sigma-band study (1a9088f) widened the entry when the owner's
instinct passed frozen gates. Same process, same bars.

## The run
ONE simulation with NO trend floor; every trade tagged by where the entry sat
relative to the name's own 200-SMA:
- **above** (the current rule's population — control)
- **shallow-below**: 0 to 5% below the 200-SMA (the SHOP zone)
- **deep-below**: more than 5% below

## Gates — a bucket EARNS ADMISSION only if it independently passes ALL FIVE
(identical bars to SWING_SIGMA_BAND_GATES_V1):
S0 n ≥ 300 · S1 win ≥ 65% · S2 mean ≥ +0.75%/trade ·
S3 all four time×symbol two-split cells positive · S4 worst ≥ −25%

Decision rule, pre-stated: if shallow-below passes all five, the floor
softens to "within 5% of the 200-SMA"; deep-below passing (unexpected) would
be treated as suspicious and re-split before any change. Any failure = the
floor stands as is.

## Prediction (recorded before the run)
Shallow-below FAILS on S2 or S3 — the SPY-regime split (+0.24%/trade below
trend) suggests below-trend entries earn little, and per-name breaks should
be worse than index breaks. ~25% that shallow-below earns in. Deep-below
fails badly (~5% it passes). If shallow passes cleanly, the owner was right
a second time and the floor was costing real edge.

## RESULTS — run 10 Sep 2026, same session, bars untouched

| bucket | n | win | mean | worst | cells |
|---|---|---|---|---|---|
| above (control) | 4427 | 71.4% | +1.00% | −23.2% | +0.97/+0.96/+0.87/+1.18 |
| shallow-below 0–5% | 1458 | 68.1% | +0.69% | −26.4% | **+0.01**/+0.50/+1.05/+1.20 |
| deep-below >5% | 3424 | 60.6% | +1.09% | −30.6% | +1.18/+1.13/+1.09/+0.98 |

Shallow-below FAILS S2 (+0.69 < +0.75), S4 (−26.4 < −25) and S3 in spirit
(one cell +0.01). Deep-below FAILS S1 and S4 — big-bounce/fat-tail lottery,
not this lane's trade. THE 200-DAY FLOOR STANDS. Prediction correct.
Owner's challenge scoreboard: sigma widened (right), floor kept (this run),
advisor's three V3 fixes all rejected. Six challenges, one earned change.
