# SELLOFF TECHNICALS V1 — result (22 Nov 2026)

Marks: SELLOFF_TECHNICALS_V1.md (cb21bb2a, before this run). Script:
scripts/study_selloff_technicals_v1.py. Golden daily store, 178 harvested
names 2006–2026, next-open fills, 0.10% round trip. SPY had 212 sessions at
≤ −2% and 162 at ≥ +2%.

| rule | n | win | mean/trade | cells | null | verdict |
|---|---|---|---|---|---|---|
| R1 RS leaders on a sell-off day, long 3 sessions | 1,605 | 49.7% | −0.35% | all four negative | all stocks same days **+0.44%** | **FAIL** |
| R2 the dip rule BELOW the 200-day | 5,496 | 62.4% | +0.64% | all positive | the live rule above the 200-day **+1.00%** | **FAIL** (does not beat its null) |
| R3 RS laggards on a rally day, short 3 sessions | 1,187 | 42.0% | −0.90% | all four negative | all stocks short −0.00% | **FAIL** |

What it says:

* **Strength on a sell-off day is not a buy.** The names that held up fell
  behind over the next three sessions; the *whole* universe bounced +0.44%
  (55% win, all four cells positive, n=32,728). Relative strength on the day
  selected the names with the least to recover.
* **The 200-day filter earns its keep.** The dip rule still makes money below
  the 200-day (+0.64%, 62% win) but 0.37 points a trade less than above it,
  with a worse tail (−37% vs −33%). The live rule stays as it is.
* **Shorting weakness on a rally day loses** −0.90% a trade; shorting
  everything on those days is flat. Consistent with TECHNICAL_SHORTS_V1: this
  universe does not reward technical shorts.

CAVEATS, stated not removed: the universe is today's harvested names — survivors
— which flatters every "buy after a drop" number here, including the +0.44%
null. That null is a NEW observation, not a tested rule: if it is to be
anything it needs its own pre-registration on a survivorship-free universe.

Nothing here changes a live rule or earns a screen row. Not to be re-raised.
