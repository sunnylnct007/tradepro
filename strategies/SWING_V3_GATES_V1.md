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
