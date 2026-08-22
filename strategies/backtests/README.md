# Study harnesses — committed, because one of them wasn't

Every script here produced a number that appears somewhere in the product or
in a gates file. They live in git so a result can be RE-RUN rather than
believed.

## Why this directory exists

The mean-reversion v1 study produced the figures on the live Swing screen —
2,413 trades, 62.4% win, +0.77%/trade, worst −12.5%, median hold 4 bars. When
those numbers needed reconciling against an independent replay (which gave a
median hold of 8 and a worst trade near −22%), **the harness could not be
found.** It had been written in a scratch directory and never committed. All
that survived was `logs/mr_sweep.log` — and that log does not even record
median hold, so the "4 bars" figure traces to nothing at all.

A pre-registered gate is worthless if the run behind it cannot be repeated.
The gates file said what would be tested; without the harness there is no way
to check that it was tested that way.

## The rule

**A study is not finished until its harness is committed here**, alongside:

* the gates file it was graded against (repo root or `strategies/`),
* the commit sha of those gates, recorded BEFORE the run,
* its log in `logs/`,
* its record in `studies.json`.

## Contents

| script | study | verdict |
|---|---|---|
| `studies/mom_v3.py` | Momentum v3 — entry volume as a gate | REJECTED — the edge inverts pre-2020 |
| `studies/dip_v1.py` `dip_v1b.py` `dip_v1c.py` | Intraday dip — the owner's own idea | REJECTED — 66% win, −0.41%/trade |
| `studies/ext.py` | Does entry extension predict failure? | No — it does not |
| `studies/pullback.py` | Real pullback vs the average catching up | Weak; volume was the stronger signal, and volume then failed its own gates |
| `studies/intraday_cov.py` | What intraday data actually exists | Median 14 sessions of 5m — the blocker |
| `studies/analog_v1.py` | Evaluate a candidate at a point in time | PARKED before running — wrong priority |

`dip_v1.py` → `v1b` → `v1c` are kept as three files rather than one because the
corrections matter: `v1` compared expectancy per TRADE against a benchmark per
DAY; `v1b` used `mean(return/days)`, which overweights short trades and
reported 11.6x the benchmark; `v1c` uses `sum(returns)/sum(days)` and gets
5.08x on the full sample — and 0.29x in the first half. Keeping only the final
version would hide that two plausible-looking metrics were wrong.

## Not reproducible

`logs/mr_sweep.log` — mean-reversion v1. Log only; the harness is gone. **The
Swing screen's published evidence rests on this.** Re-running it is not
optional housekeeping, it is the only route to numbers anyone can stand
behind.
