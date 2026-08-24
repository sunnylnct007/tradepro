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
