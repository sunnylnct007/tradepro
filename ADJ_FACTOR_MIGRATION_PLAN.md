# Close convention: RAW OHLC + a real `adj_factor`

**Owner decision, 2026-08-23.** The canonical store will hold **raw** OHLC plus a
populated `adj_factor`, so both the raw and the adjusted series are derivable and
neither is lost. This file is the plan; it is NOT done.

**Do not execute this during the forward test.** See "When" below — and read the
atomicity constraint first, because a partial run makes the data *worse* than
leaving it alone.

## The defect being fixed

`adj_factor` is 1.0 for all 271 symbols, and it means two different things:

- **yfinance rows** are fetched with `auto_adjust=True`, so `close` is the
  DIVIDEND-ADJUSTED price and `adj_factor = 1.0` is self-consistent
  (`yfinance_provider.py` says exactly this at the assignment).
- **ibkr / ibkr_web rows** are RAW, and also carry `adj_factor = 1.0` — which
  asserts "already adjusted" and is false.

Measured on SPY against the legacy cache: yfinance rows sit 0.26% from legacy
`adj_close` and 14.4% from raw; ibkr rows sit 0.00–0.09% from raw. Sources
alternate by monthly partition, so a single symbol's series changes convention
partway through.

## Target state

| column | meaning after migration |
|---|---|
| `open/high/low/close` | **raw**, as printed, every provider |
| `adj_factor` | `adj_close / close` for that symbol-date; 1.0 means genuinely no adjustment |
| adjusted series | derived on read as `close * adj_factor` — never stored |

Raw is the right base because it is the one convention **every** provider can
supply consistently. IBKR cannot supply an adjustment factor at all, so anything
that stores adjusted-by-default is unobtainable from the golden source.

## Steps

1. **Provider.** `yfinance_provider._call_yfinance_single`: `auto_adjust=True` →
   `False`, then set `adj_factor = adj_close / close` from the returned frame
   (the code already documents this as the intended formula; it short-circuits to
   1.0 only because auto_adjust hides the raw close). IBKR providers already
   return raw — leave their bars alone and let the factor come from step 2.
2. **Factor source.** Derive `adj_factor` per symbol-date from ONE place —
   yfinance `adj_close / close` — regardless of which provider supplied the bar.
   The factor is a property of the instrument's corporate actions, not of who
   printed the bar, so it must not vary by source.
3. **Backfill.** Recompute the full store: convert existing yfinance-sourced
   `close` back to raw (`close / factor`) and populate the factor everywhere.
   Idempotent, marker-guarded, and reversible — the legacy cache still holds an
   independent `adj_close` for cross-checking, so verify a sample against it
   before and after.
4. **Consumers.** Repoint `wheel_backtest_run` and `straddle_scan` off
   `load_cached("yahoo", …)`. Both prefer `adj_close`, so they must read
   `close * adj_factor`, not `close` — swapping them to raw silently would change
   backtest results, which is the whole reason they are still on the legacy cache.
5. **Retire `cache.py`.** After step 4 its only remaining users are the visible
   fallbacks in `compare.py` and `ibkr_bars.py`.
6. **Guard.** A test asserting no symbol's series mixes conventions — i.e. every
   partition of a symbol agrees about what `close` means.

## Atomicity — the constraint that decides the timing

**This cannot be done incrementally.** Today there is one convention boundary per
symbol. If the provider change (step 1) ships without the backfill (step 3), new
bars land raw while history stays adjusted, creating a **second, fresher**
boundary — right at the live edge, where the strategy actually reads. That is
strictly worse than the current state.

Steps 1 and 3 ship together, per symbol, or not at all.

## When

Not during the 12-week Swing forward test. Recorded in `DATA_CHANGE_LOG.md` as a
condition deliberately carried in, with the measurement showing it changes no
entry decision today: SMA200 bias median 0.241%, and zero symbols sit closer to
their 200-SMA than their own bias. It also shrinks weekly as raw IBKR bars
accumulate and the mixed rows age out of the 200-day window.

Re-check before assuming that still holds:

```
uv run python scripts/check_sma200_seam.py
```

It exits non-zero only if a gate could actually flip.

## Fold in while the store is open — FOUR items, one session

Touching the store twice is worse than once, and all four change the trade
population. G4 moves with population size and G5 currently clears its gate by
only 1.1 points, so none of this is safe before the forward test closes
(≈16 Nov 2026).

**1. Close convention + `adj_factor`** — the body of this document.

**2. The 5-year daily cap — DO THIS FIRST, or 3 and 4 silently undo themselves.**

```
bar_cache/providers/ibkr_web_provider.max_history()
  returns 365*5 days for any resolution NOT in its measured table
  -> and "1d" is not in that table
```

1825 days, earliest reachable 2021-08-25 — which is exactly where the store's
2021-08-23 first-bar cluster sits. It is a defect rather than a judgement call
for two reasons: every neighbouring entry in that table carries its measured
evidence in the comment ("worked at 6 months, failed at 12") while `365*5` is an
unmeasured fallback; and `IBKRDailyBackfillService` pages back **15 years** on
the same broker every night, so IBKR plainly serves the depth — only the Python
side declines to ask.

**A backfill that does not raise this cap first will re-truncate to five years.**

**3. XLC / XLRE hold only 5 years** — 2021-08-23, the cap above, not inception
(XLC launched 2018-06, XLRE 2015-10; the legacy cache still holds both from
those dates). Fine for a 200-SMA, wrong for any long backtest.

**4. Uneven depth generally — TWO mechanisms, and fixing one looks like done.**
Store first-bar clusters: 2010-01-04 (93 symbols), 2022-01-03 (90), 2019-07-01
(45), 2021-08-23 (11), 2026-05-04 (12).

- The 2021/2022 clusters are the **cap** (item 2).
- The 2010-01-04 cluster is an old `--from 2010` **seed window** — a different
  cause needing a different fix. SPY, AAPL, MSFT, NVDA, QQQ, MU, KLAC, GOOGL and
  IWM all sit at 4,185 local against 5,000 served, from 2010-01-04 versus
  2006-10-05. Raise the cap alone and the mega-caps stay short at 2010 while the
  job reports success.

## Why the cap matters more than "less history" suggests

It does not shorten the sample evenly — it removes the hard part. **2022 is the
only losing year this strategy has** (−1.07% on 88 trades, research lane). A
per-symbol record beginning 2022-01-03 contains no 2020 crash and no 2022 bear
market, so it is not a shorter measurement of the same thing; it is a
measurement over a different regime mix. The universe-level result is unaffected
— 2,310 trades pooled across 244 names, and the clean two-split runs an identical
74-symbol set across both decades — but **per-symbol** numbers, which are the
ones on screen, needed the caveat and now carry it.


## MEASURED 2026-08-25: the factor is DERIVABLE from data we already hold

The research lane suggested pointing `check_store_agrees_with_api.py` at the
seam, on the grounds that it makes a falsifiable prediction. It does, and the
prediction held — with a consequence for how this migration is executed.

Close divergence between the parquet store and the API, by the parquet row's
source (SPY/AAPL/MSFT, 4,184 overlapping dates each):

| source | n | median diff | worst |
|---|---|---|---|
| `ibkr_web` | 478 | **0.000%** | 0.000% |
| `ibkr` | 230 | ~0.00% | 1.5–6.1% |
| `yfinance` | 3476 | **−8% to −14%** | 16–25% |

Two things follow, and both change the plan:

**1. The API is RAW throughout.** Its closes agree with the parquet store
EXACTLY (0.000%) on every ibkr_web-sourced row, and differ only on
yfinance-sourced rows. So Postgres holds one convention — raw — while parquet
holds a mixture. That removes the ambiguity this document opened with: we no
longer have to decide which store is which, we measured it.

**2. `adj_factor` does not need re-fetching. It is the RATIO.** For any
yfinance-sourced row, `parquet_close / api_close` IS the adjustment factor.
Measured on SPY it runs 0.7463 (2010-01-04) → 0.9832 (recent), rising smoothly
toward 1.0 as dates approach the present — the shape of a cumulative dividend
adjustment.

So the backfill becomes, per symbol-date where both stores hold the bar:

```
adj_factor := parquet_close / api_close     # captured BEFORE overwriting
close      := api_close                     # raw, matching every other row
```

No provider call, no re-seed, no rate limit, and no dependence on the 5-year
cap (item 2) for the rows both stores already have.

**Caveats that must be respected when this runs:**

- The ratio is **not perfectly monotonic** — there is noise in it. Validate the
  derived series (smooth, or reject outliers) rather than applying it blindly;
  a spurious factor silently rewrites a price.
- It only covers dates **both** stores hold. The API caps at ~5,000 bars, so
  older parquet dates have no counterpart and still need item 2's deeper fetch.
- `ibkr`-sourced rows show a worst-case 1.5–6.1% divergence that is NOT the
  dividend seam — median is ~0, so this is a small number of individual bad
  bars, not a convention difference. Investigate those separately; they look
  like the same class as the TXN bad write.


## 7. The `ibkr` SOCKET provider wrote bad closes — 4.6% of its bars

Isolated 2026-08-25 while chasing the 1.5–6.1% residual the seam analysis left
behind. It is NOT the dividend seam and NOT a date shift (tested: only 27 of 980
PLTR rows match an adjacent session, which is noise). It is bad values.

22,452 IBKR-family rows compared against the API across 31 symbols:

| disagreement | rows | share |
|---|---|---|
| >0.1% | 6,084 | 27.1% — mostly rounding, ignore |
| **>1.0%** | **1,038** | **4.62%** |
| >5.0% | 97 | 0.43% |
| >10% | 31 | 0.14% |

**Every one is `source == "ibkr"` — the socket provider. Zero from `ibkr_web`.**
24 symbols, concentrated in volatile names: PLTR 164, WDC 152, APP 132, COHR 129,
LITE 65, TSLA 52. Worst individual: APP 2025-02-12 local 490.75 vs api 380.32.

Medians are ~0.00%, so this is a scatter of individually wrong bars rather than a
convention difference — the same class as the TXN bad write the research lane
found, but historical and far more numerous.

The socket provider is largely retired in favour of `ibkr_web` (owner ruling
9 Aug, OAuth-only), so this is cleanup of a legacy write path rather than an
ongoing leak. Repair is straightforward: the API holds the correct value for
every one of these dates, so it is the same overwrite as the `adj_factor` work.

## RUN THE HARNESS BEFORE AND AFTER — a store repair silently re-grades

Research lane, 2026-08-25: their Swing baseline moved from 2,310 trades / +1.06%
to 2,503 / +1.10% **purely because the store changed underneath the harness**
(158 de-duplicated partitions, 244 re-sourced August partitions). Direction
favourable, no gate flipped, so today it is a footnote.

It will not be a footnote for this migration, which is a far larger repair:
raw/adjusted conversion across 3,476 rows per symbol, plus deeper history, plus
1,038 corrected closes. Every one of those changes the trades the harness finds.

**So: run the graded harness immediately BEFORE and immediately AFTER, and record
both numbers in DATA_CHANGE_LOG.** Otherwise the strategy's measured edge changes
during the repair and nobody can tell whether the data got better or the strategy
got worse. Attribution has to be designed in; it cannot be recovered afterwards.

## Sizing the backfill: "available" is a FLOOR

Several symbols return **exactly 5,000** bars from the API, which is the API's
own cap rather than the instrument's age. Anyone sizing this work off that number
is sizing off a cap. The true depth at IBKR is greater than both the local and
the "available" figure.

## Verify before and after

```
uv run python scripts/check_store_vs_api_history.py --limit 244
```

Compares first bar and bar count per symbol across the parquet store and the API,
and exits non-zero on any material shortfall. Run it before the backfill to size
the work, and after to prove it landed — including on the mega-caps, which are
the ones a cap-only fix would leave behind.
