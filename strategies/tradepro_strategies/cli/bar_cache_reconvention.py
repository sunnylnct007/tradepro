"""Rewrite stored daily bars into ONE price convention (12 Nov 2026).

WHY. Daily histories interleaved two conventions about 55 times per symbol:
IBKR rows are RAW (split-adjusted only), yfinance rows were written with
auto_adjust=True (split AND dividend adjusted), and adj_factor was 1.0
everywhere. At every switch the price jumped by the cumulative dividend
adjustment: daily moves above 10% were four times as common at a switch as
elsewhere (ACN +68% on 2012-02-23, AAPL +16% on 2010-01-04, VZ rows 64% apart).
A momentum ranking reads those jumps as returns.

WHAT. Every stored 1d row ends up RAW, with adj_factor = yahoo adj close /
yahoo close on that date, so `close * adj_factor` is the total-return price:
  * an IBKR row keeps its prices (they match yahoo's raw close exactly),
    gains the factor, and takes yahoo's VOLUME (IBKR's was 0.5x-7.9x off);
  * any other row takes yahoo's raw OHLC for that date.
Timestamps of stored rows are kept exactly and no row is removed. A row
yahoo has no date for is converted with the nearest factor and COUNTED.

GAPS ARE FILLED. The same check found whole YEARS missing inside stored
spans: SPY Feb 2007 -> Jan 2010 (the market filter had no 2008), VZ
2010 -> 2015, ACN 2010 -> 2012. Every yahoo session between a symbol's first
and last stored bar that the store lacks is added, timestamped like the
existing yfinance rows (New York midnight, in UTC).

HOW. Through BarStore's own write path (force_refresh with a provider that
serves the rewritten rows), so manifests, the shrink guard and the S3 mirror
all apply. Never calls IBKR. The yfinance provider writes this convention
from now on, so the seam does not come back.

    uv run tradepro-bar-cache-reconvention --symbols AAPL,ACN --dry-run
    uv run tradepro-bar-cache-reconvention --all
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

log = logging.getLogger("bar_cache_reconvention")

ASSET = "us_etf"
_PRICE = ["open", "high", "low", "close"]


def _yahoo_raw(sym: str) -> pd.DataFrame | None:
    """yfinance daily history through the store's own yfinance provider (RAW
    closes, adj_factor = adj close / close), indexed by session date."""
    from ..bar_cache.providers.yfinance_provider import YFinanceProvider
    df, _ = YFinanceProvider().fetch(
        sym, ASSET, "1d", datetime(1990, 1, 1, tzinfo=timezone.utc),
        datetime.now(timezone.utc) + timedelta(days=1))
    if df is None or df.empty:
        return None
    df = df.copy()
    # yfinance's daily index is New York midnight; the session date is the
    # New York date, not the UTC one.
    idx = df.index.tz_convert("America/New_York") if df.index.tz is not None else df.index
    df.index = pd.to_datetime([str(i)[:10] for i in idx])
    return df[~df.index.duplicated(keep="last")]


def convert(stored: pd.DataFrame, yahoo: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """The stored rows in the one convention. Pure: no I/O."""
    out = stored.copy()
    days = pd.to_datetime([str(i)[:10] for i in out.index])
    is_ibkr = out["source"].astype(str).str.startswith("ibkr").values
    stats = {"rows": len(out), "ibkr_kept": int(is_ibkr.sum()), "replaced": 0,
             "no_yahoo_date": 0, "gap_filled": 0, "ibkr_vs_yahoo_raw_p95": None}
    if yahoo is None:
        stats["no_yahoo_date"] = int((~is_ibkr).sum())
        return out, stats
    y = yahoo.reindex(days)
    have = y["close"].notna().values
    fac = y["adj_factor"].values
    # nearest known factor for dates yahoo lacks
    near = yahoo["adj_factor"].reindex(days, method="nearest").values

    out["adj_factor"] = np.where(have, fac, near)
    rep = (~is_ibkr) & have
    for c in _PRICE:
        out.loc[rep, c] = y[c].values[rep]
    # VOLUME from yahoo on EVERY row it has a date for, IBKR rows included.
    # IBKR's history volume is not consolidated share volume: against yahoo it
    # ran 0.5x (NVDA) to 7.9x (MU) on the same days of Nov 2026, with no fixed
    # unit, so a liquidity rank on it put MU above NVDA. Prices stay IBKR's.
    out.loc[have, "volume"] = y["volume"].values[have]
    out.loc[rep, "source"] = "yfinance"
    # a non-IBKR row yahoo has no date for: it was dividend-adjusted; undo it
    # with the nearest factor so it sits on the raw scale
    miss = (~is_ibkr) & ~have
    if miss.any():
        for c in _PRICE:
            out.loc[miss, c] = out.loc[miss, c].values / near[miss]
    stats["replaced"] = int(rep.sum())
    stats["no_yahoo_date"] = int(miss.sum())
    if is_ibkr.any():
        both = is_ibkr & have
        if both.any():
            d = np.abs(out["close"].values[both] / y["close"].values[both] - 1)
            stats["ibkr_vs_yahoo_raw_p95"] = float(np.percentile(d, 95))
    # fill sessions missing INSIDE the stored span
    lo, hi = days.min(), days.max()
    gap = yahoo.index[(yahoo.index > lo) & (yahoo.index < hi) & ~yahoo.index.isin(days)]
    stats["gap_filled"] = int(len(gap))
    if len(gap):
        add = yahoo.loc[gap, _PRICE + ["volume", "adj_factor"]].copy()
        add["source"] = "yfinance"
        add.index = (pd.DatetimeIndex(gap).tz_localize("America/New_York")
                     .tz_convert("UTC"))
        add.index.name = out.index.name
        out = pd.concat([out, add[out.columns]]).sort_index()
    return out, stats


def switch_jumps(df: pd.DataFrame) -> tuple[float, float]:
    """Share of daily moves >10% AT a source switch vs elsewhere."""
    src = df["source"].astype(str).values
    r = np.abs(np.diff(np.log(df["close"].astype(float).values)))
    m = src[1:] != src[:-1]
    at = float((r[m] > 0.10).mean()) if m.any() else 0.0
    el = float((r[~m] > 0.10).mean()) if (~m).any() else 0.0
    return at, el


def _rewrite_provider(frame: pd.DataFrame, name: str):
    """A store provider that serves the converted rows of one symbol."""
    from ..bar_cache.providers.base import Provider

    class _Rewrite(Provider):
        def fetch(self, canonical, asset_class, resolution, start, end):
            f = frame[(frame.index >= start) & (frame.index < end)]
            return f, {"provider_version": "reconvention-1", "rows": len(f)}

        def max_history(self, resolution):
            return None

        def supports_resolution(self, resolution):
            return resolution == "1d"

    p = _Rewrite()
    p.name = name
    return p


def _one(sym: str, reader, dry_run: bool) -> dict:
    """Rewrite one symbol. Returns its stats (or {'failed': msg})."""
    from ..bar_cache.providers import register_provider
    from ..bar_cache.store import BarStore

    stored = reader.read(sym, ASSET, "1d", fetched_by="reconvention").df
    if stored is None or stored.empty:
        return {"skipped": True}
    stored = stored[~stored.index.duplicated(keep="last")].sort_index()
    yahoo = _yahoo_raw(sym)
    new, st = convert(stored, yahoo)
    st["no_yahoo"] = yahoo is None
    st["before"], _ = switch_jumps(stored)
    st["after"], _ = switch_jumps(new)
    if not dry_run:
        prov = register_provider(_rewrite_provider(new, f"reconvention_{sym}"))
        store = BarStore(reader.base_dir, provider_chain=[prov.name])
        first = new.index.min()
        store.get(sym, ASSET, "1d",
                  datetime(first.year, first.month, 1, tzinfo=timezone.utc),
                  datetime.now(timezone.utc) + timedelta(days=1),
                  force_refresh=True, allow_partial=True, fetched_by="reconvention")
    return st


def run(symbols: list[str], *, dry_run: bool, workers: int = 1) -> int:
    """Each symbol is independent and the rewrite is idempotent (re-running a
    half-written symbol gives the same result), so symbols run in parallel.
    The cost is S3 uploads (~230 partitions a symbol), not CPU."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..bar_cache import asset_classes  # noqa: F401 — registers plugins
    from ..ibkr_bars import bar_store

    reader = bar_store()
    totals = {"symbols": len(symbols), "rewritten": 0, "no_yahoo": 0, "failed": 0,
              "rows": 0, "no_yahoo_date": 0, "gap_filled": 0, "still_jumpy": 0}
    worst: list[tuple[str, float]] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_one, s, reader, dry_run): s for s in symbols}
        for i, f in enumerate(as_completed(futs), 1):
            sym = futs[f]
            try:
                st = f.result()
            except Exception as exc:  # noqa: BLE001 — one symbol never stops the run
                totals["failed"] += 1
                failed.append(sym)
                log.warning("%s FAILED: %s", sym, str(exc)[:200])
                continue
            if st.get("skipped"):
                continue
            totals["rewritten"] += 0 if dry_run else 1
            totals["no_yahoo"] += int(st["no_yahoo"])
            totals["rows"] += st["rows"]
            totals["no_yahoo_date"] += st["no_yahoo_date"]
            totals["gap_filled"] += st["gap_filled"]
            totals["still_jumpy"] += int(st["after"] > 0.05)
            if st["ibkr_vs_yahoo_raw_p95"] is not None:
                worst.append((sym, st["ibkr_vs_yahoo_raw_p95"]))
            log.info("%s rows=%d ibkr_kept=%d replaced=%d gap_filled=%d no_yahoo_date=%d "
                     ">10%%-at-switch %.3f -> %.3f", sym, st["rows"], st["ibkr_kept"],
                     st["replaced"], st["gap_filled"], st["no_yahoo_date"],
                     st["before"], st["after"])
            if i % 50 == 0:
                log.info("progress %d/%d %s", i, len(symbols), totals)
    worst.sort(key=lambda x: -x[1])
    print("RECONVENTION", "DRY-RUN" if dry_run else "WRITE", totals)
    print("failed:", failed[:40])
    print("IBKR rows furthest from yahoo raw (p95):",
          [(s, round(v, 4)) for s, v in worst[:8]])
    return 1 if totals["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--symbols", help="comma-separated")
    g.add_argument("--all", action="store_true", help="every symbol this machine's store holds")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8, help="symbols in parallel (S3-bound)")
    ap.add_argument("--from", dest="start_from", help="skip symbols sorting before this one (resume)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if a.all:
        from ..daily_bars import local_symbols
        syms = local_symbols()
    else:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if a.start_from:
        syms = [x for x in syms if x >= a.start_from.upper()]
    return run(syms, dry_run=a.dry_run, workers=a.workers)


if __name__ == "__main__":
    sys.exit(main())
