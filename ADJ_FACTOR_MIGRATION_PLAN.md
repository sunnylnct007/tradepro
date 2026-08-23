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

## Fold in while the store is open

Two other things are parked on the same window, and touching the store twice is
worse than once:

- **XLC / XLRE hold only 5 years** — truncated at 2021-08-23 by a fetch window,
  not inception (XLC launched 2018-06, XLRE 2015-10; the legacy cache still has
  both from those dates). Fine for a 200-SMA, wrong for any long backtest.
- **History depth is uneven generally** — store first-bar dates cluster at
  2010-01-04 (93 symbols), 2022-01-03 (90), 2019-07-01 (45), 2021-08-23 (11),
  2026-05-04 (12). Those are fetch windows, not inceptions.

Both change the trade population, and G4 moves with population while G5 currently
clears its gate by only 1.1 points. Neither is safe mid-test.
