"""tradepro-bar-reconcile — prove the chart store still matches the golden one.

Owner, 26 Sep 2026: *"why we have split reading of same data"*, *"we need to
have a golden source of data that is used across the system"*, *"can we stick
to basics of programming fundamentals"*.

THE PROBLEM, STATED PLAINLY. Two stores hold the same bars:

    parquet + S3            written by the harvest DIRECTLY — the GOLDEN store,
                            read by every strategy and every backtest
    postgres ibkr_price_bars  written by a separate HTTP push — read by the
                            charts and the UI

One harvest, two destinations, and only the second can fail on its own. It did,
on 16, 18 and 25 September, and on 26 September the charts still showed 23 Sep
for OVV and AES while the parquet the strategies read held 25 Sep. AES was a
position momentum had opened the day before, charted against bars from before
the trade.

A COPY NOBODY CHECKS IS NOT A COPY, IT IS A SECOND SOURCE. That is the whole of
the owner's point. There are exactly two honest resolutions: delete the copy
and have the API read the golden store, or keep the copy and CONTINUOUSLY PROVE
it matches. Retrying the push (26 Sep) made the split safe; it did not make the
copy verified — a retry that exhausts still leaves a silent gap.

This is the proof. It reads the golden store from disk, reads what the chart
store reports, compares the newest bar per symbol, and REPORTS every symbol
where they disagree. With --repair it re-pushes exactly those symbols.

It fails LOUD and it fails CLOSED: a symbol it cannot check is reported as
unchecked, never as agreeing. An empty answer from either side is an absence,
not a match — the standing rule on this desk.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger("tradepro.bar_reconcile")

_DEFAULT_BASE = Path.home() / ".tradepro" / "bar_cache"

#: A chart a session behind is a chart that can mislead a trade. One session of
#: lag is tolerated because the push runs after the close; two is a fault.
DEFAULT_MAX_LAG_SESSIONS = 1


def golden_last_bar(base_dir: Path, symbol: str, asset: str,
                    resolution: str) -> dt.date | None:
    """Newest bar for this symbol in the GOLDEN store, or None if absent."""
    import pandas as pd
    parts = sorted(glob.glob(str(base_dir / asset / symbol / resolution / "*.parquet")))
    if not parts:
        return None
    try:
        df = pd.read_parquet(parts[-1])
    except Exception as exc:  # noqa: BLE001 — unreadable is NOT "up to date"
        log.warning("%s: golden partition unreadable (%s)", symbol, str(exc)[:80])
        return None
    if df.empty:
        return None
    return pd.to_datetime(df.index.max()).date()


def chart_store_last_bars(api_base: str, token: str | None,
                          resolution: str) -> dict[str, dt.date]:
    """Newest bar per symbol as the CHART store reports it. One call."""
    r = requests.get(f"{api_base.rstrip('/')}/api/integrations/ibkr/bar-coverage",
                     headers={"Authorization": f"Bearer {token}"} if token else {},
                     timeout=60)
    r.raise_for_status()
    out: dict[str, dt.date] = {}
    for row in (r.json() or {}).get("coverage") or []:
        if str(row.get("resolution")) != resolution:
            continue
        ts = str(row.get("lastTs") or "")[:10]
        if not ts:
            continue
        try:
            out[str(row.get("symbol", "")).upper()] = dt.date.fromisoformat(ts)
        except ValueError:
            continue
    return out


def _sessions_between(a: dt.date, b: dt.date) -> int:
    """Weekday count between two dates — a Friday bar is not stale on Sunday."""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def reconcile(*, base_dir: Path, asset: str, resolution: str, api_base: str,
              token: str | None, max_lag: int) -> dict:
    chart = chart_store_last_bars(api_base, token, resolution)
    symbols = sorted(
        p.name for p in (base_dir / asset).iterdir()
        if p.is_dir() and (p / resolution).is_dir()
    ) if (base_dir / asset).is_dir() else []

    behind, missing, unchecked, agree = [], [], [], 0
    for sym in symbols:
        g = golden_last_bar(base_dir, sym, asset, resolution)
        if g is None:
            unchecked.append(sym)
            continue
        c = chart.get(sym.upper())
        if c is None:
            missing.append((sym, g))
            continue
        lag = _sessions_between(c, g)
        if lag > max_lag:
            behind.append((sym, g, c, lag))
        else:
            agree += 1
    return {"symbols": len(symbols), "agree": agree, "behind": behind,
            "missing": missing, "unchecked": unchecked}


def _credentials() -> tuple[str | None, str | None]:
    from .push_to_api import load_credentials
    try:
        return load_credentials()
    except Exception:  # noqa: BLE001
        return os.environ.get("TRADEPRO_API_BASE_URL"), os.environ.get("TRADEPRO_API_TOKEN")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asset", default="us_etf")
    ap.add_argument("--resolution", default="1d")
    ap.add_argument("--base-dir", default=str(_DEFAULT_BASE))
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--max-lag-sessions", type=int, default=DEFAULT_MAX_LAG_SESSIONS)
    ap.add_argument("--repair", action="store_true",
                    help="re-push exactly the symbols found behind or missing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    base, token = _credentials()
    base = args.api_base or base
    if not base:
        log.error("no API base — cannot reconcile, and NOT reporting success")
        return 2

    r = reconcile(base_dir=Path(args.base_dir), asset=args.asset,
                  resolution=args.resolution, api_base=base, token=token,
                  max_lag=args.max_lag_sessions)

    print(f"\nGOLDEN vs CHART STORE — {args.asset} {args.resolution}")
    print(f"  symbols in the golden store : {r['symbols']}")
    print(f"  up to date                  : {r['agree']}")
    print(f"  BEHIND                      : {len(r['behind'])}")
    print(f"  MISSING from the chart store: {len(r['missing'])}")
    print(f"  could not check             : {len(r['unchecked'])}")

    for sym, g, c, lag in sorted(r["behind"], key=lambda x: -x[3])[:20]:
        print(f"    ✗ {sym:8} golden {g}  chart {c}  ({lag} session(s) behind)")
    for sym, g in r["missing"][:10]:
        print(f"    ✗ {sym:8} golden {g}  chart HAS NO BARS")
    if r["unchecked"]:
        print(f"    ? unchecked: {', '.join(r['unchecked'][:10])}"
              f"{'…' if len(r['unchecked']) > 10 else ''}")

    stale = [s for s, *_ in r["behind"]] + [s for s, _ in r["missing"]]
    if args.repair and stale:
        from .bar_cache_push import push_bars
        print(f"\n  repairing {len(stale)} symbol(s) …")
        end = dt.datetime.now(dt.UTC)
        out = push_bars(base_dir=Path(args.base_dir), symbols=stale,
                        asset_class=args.asset, resolution=args.resolution,
                        start=end - dt.timedelta(days=20), end=end,
                        api_base=base, token=token)
        print(f"  pushed {out.get('rows')} row(s) for {out.get('symbols')} symbol(s)")

    # A divergence is a FAULT, not information. Non-zero so the lane that runs
    # this cannot report a green day over a stale chart store.
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
