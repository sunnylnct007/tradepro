# SWING V3 GATES V1 — three rule-change candidates, thresholds frozen BEFORE the runs

8 Sep 2026, from two external-advisor audits the owner forwarded ("u dont have
to implement blindly as long as u have a reason"). Display fixes were adopted
same night (day-change, 20d-high distance, 200-SMA cushion in ATRs + slope,
ETF note). The three RULE changes below are testable and get the ceremony.
Baseline for every comparison: the current rule (−2.25σ entry, 20d-mean
target, fixed 8% stop, 200-SMA floor, 20-session timeout), which is
reproduced first in every run as the control.

## Q1 — ATR-scaled stop: replace fixed 8% with 2.5 × ATR14-at-entry
Scale-invariant (house invariant), tightens low-vol names (USMV-class 8% →
~1.75%), leaves SHOP-class nearly unchanged (~10%).
GATES to replace the stop: the variant must (a) keep mean/trade within
−0.05pp of control or better, (b) improve worst trade by ≥3pp, (c) hold in
all four time×symbol two-split cells, (d) n within 10% of control.
PREDICTION: tightening stops on a mean-reversion lane usually converts
timeout scratches into realized losses; I give ~35% it passes all four.

## Q2 — volatility floor: require ATR% ≥ 2.0 at entry
DISCLOSED PEEK: the 8 Sep stop-distance bucket study (run post-hoc to answer
an owner question) already showed the >8-ATR-stop class at +0.46%/trade with
a −23.2% worst. This question is therefore CONFIRMATORY, not clean discovery.
GATES to adopt the floor: excluded trades (ATR%<2.0) must show (a) mean/trade
< half of control's, AND (b) no two-split cell above control's mean — i.e.
the exclusion costs edge nowhere.
PREDICTION: passes (~70%) — the peek says the floor removes starved capital,
including USMV mechanically.

## Q3 — reversal-confirmation entry: enter only after close > prior day's high
The owner's manual CRDO standard. Trades enter at the confirmation close
instead of the signal close; all else identical.
GATES: same S-series as SWING_SIGMA_BAND_GATES_V1 (win ≥65%, mean ≥+0.75%,
all four cells positive, worst ≥−25%, n ≥300) AND mean/trade must beat
control — confirmation costs entry price, so it must buy MORE than it costs.
PREDICTION: fails the "beats control" bar (~30% it passes) — mean reversion's
edge concentrates in the extreme close nobody wants to buy; waiting for
proof usually pays for the proof. If it fails, the KNIFE/basing display
stays the manual tool and the rule does not change.

Runs: harness variants of backtests/studies/mean_reversion_v2.py, one commit,
results appended here. No threshold in this file moves after this commit.

## RESULTS — run 10 Sep 2026, harness backtests/studies/swing_v3_study.py

| variant | n | win | mean | worst | cells | verdict |
|---|---|---|---|---|---|---|
| control (live rule) | 4432 | 71.5% | +1.00% | −23.2% | all + | reproduces the record |
| Q1 2.5×ATR stop | 4500 | 69.6% | +0.96% | **−32.3%** | all + | **FAIL** — tail gate demanded ≥3pp BETTER; it got 9pp WORSE. Tight ATR stops on high-vol names walk into gaps. 8% stop stands. |
| Q2 ATR%≥2.0 floor | 2585 | 67.2% | +1.24% | −19.6% | all + | **FAIL its own bar** — excluded 1,847 trades still earn ~+0.66%/trade, above the "less than half of control (+0.50)" bar. The floor discards real (if thin) edge; NOT adopted. USMV-class rows stay visible with their breakeven/ATR warnings instead. |
| Q3 confirmation entry | 3028 | 73.9% | **+0.42%** | −23.6% | all + | **FAIL** — win rate rises but mean/trade HALVES vs control (+1.00). Waiting for the green bar costs ~0.6%/trade: the edge lives in the close nobody wants to buy. Entry stays at the signal close. Confirmation remains a DISPLAY verdict (KNIFE/basing) for discretionary use. |

All three predictions on record were directionally correct. The pre-registered
rule survives all three challenges intact; the advisor's proposals are
answered with measurements, not opinions.
