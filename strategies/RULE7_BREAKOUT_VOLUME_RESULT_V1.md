# Rule 7 — breakout-bar volume: RESULT (16 Nov 2026)

Pre-registered in RULE7_BREAKOUT_VOLUME_GATES_V1.md (78a11d92) before the run.
949 rule 7 trades, 380 sessions, 2024-09-03 -> 2026-11-16, 29 names, frozen
rule 7 at 2x first-candle volume, same code as the forward record.

| | All | High (>= 1.5x) | Low (< 1.5x) |
|---|---|---|---|
| 1st half | +0.118% (n=482) | +0.160% (n=369) | -0.021% (n=113) |
| 2nd half | +0.122% (n=467) | +0.093% (n=370) | +0.230% (n=97) |
| Full | +0.120% (n=949) | +0.127% (n=739) | +0.095% (n=210) |

High-volume full-period 90% CI (day bootstrap): [-0.139%, +0.396%].

**FAIL.** High-volume breakouts beat all trades by 0.04pp in the first half and
LOST to them in the second; the CI includes zero.

Why: 78% of breakouts are already high-volume. Rule 7 only trades stocks whose
first candle ran 2x normal, so the breakout bar is usually busy as well — the
"in play" filter already does the job a breakout-volume filter would do.

No other threshold will be tried on this history. The forward record keeps the
high/low split at 1.5x as free evidence.
