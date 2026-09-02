"""tradepro-earnings-backfill — historical earnings dates, so the question is answerable.

    uv run tradepro-earnings-backfill --dry-run     # count, write nothing
    uv run tradepro-earnings-backfill               # backfill the universe

Owner, 2 Sep 2026, after trading DELL and SNOW around their prints and finding
the platform flagged neither: *"yes we need those startgey"*.

## Why this exists before the strategy does

The strategy the owner actually trades — buy a quality name that has sold off
INTO its print, exit on the post-print move — could not be tested at all,
because we hold no historical earnings dates.

`earnings_calendar` is fed by a FORWARD-ONLY feed. Measured 2 Sep, asking it to
backfill returned `fetched: 0` for every past window tried — 2024 (49 chunks),
June 2026, July 2026, and early August 2026. It holds ONE event per symbol, its
own docstring says it is "a forward calendar and useless for backtesting", and
that is exactly right.

yfinance has what it does not:

    AAPL   50 dates   2014-07-22 .. 2026-10-29
    DELL   30 dates   2019-03-29 .. 2026-05-28
    SNOW   24 dates   2020-12-03 .. 2026-09-02

Seven to twelve years per name. With that, a pre-earnings strategy becomes
FALSIFIABLE — which is the only condition under which anything ships here. Two
profitable manual trades are not evidence; they are precisely the sample size
this desk refuses to act on, and six strategy candidates have been rejected for
claims built on less.

## What this is careful about

**SOURCE IS RECORDED per row.** These arrive as `yfinance_hist`, never blended
namelessly with the Finnhub forward feed. When a backtest later says "the edge
is real", the next question is which dates it was measured on, and a row that
cannot answer that is a row that cannot be trusted.

**FORWARD DATES ARE SKIPPED.** yfinance returns scheduled future prints too.
Those belong to the live feed, and letting a Yahoo guess overwrite a confirmed
upcoming date would corrupt the screens that trade off it TODAY.

**A SYMBOL THAT FAILS IS NAMED, never silently absent.** A universe that
half-loaded and said nothing is how a backtest ends up measured on 100 names
while reporting 244.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import time

log = logging.getLogger("tradepro.earnings_backfill")

BATCH = 200          # rows per POST — the ingest takes a list
PAUSE_S = 0.4        # be kind to Yahoo; this is a one-off, not a hot path


def fetch_history(symbol: str, limit: int = 60) -> list[str]:
    """Past report dates for one symbol, ISO, oldest first.

    PAST ONLY. Scheduled future prints belong to the live forward feed, and a
    Yahoo guess must never overwrite a confirmed upcoming date that today's
    screens are trading off.
    """
    from ..yahoo_session import yahoo_session
    import yfinance as yf

    today = _dt.date.today()
    tk = yf.Ticker(symbol, session=yahoo_session())
    df = tk.get_earnings_dates(limit=limit)
    if df is None or df.empty:
        return []
    out = []
    for idx in df.index:
        try:
            d = _dt.date.fromisoformat(str(idx)[:10])
        except (ValueError, TypeError):
            continue
        if d < today:
            out.append(d.isoformat())
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-earnings-backfill")
    ap.add_argument("--symbols", help="comma list (default: the committed universe)")
    ap.add_argument("--limit", type=int, default=60, help="max dates per symbol")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

    import requests

    from .push_to_api import load_credentials
    from ..universe import universe_symbols

    base, token = load_credentials()
    base = base.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else universe_symbols(strict=False))
    print(f"earnings backfill — {len(syms)} symbol(s), past dates only, "
          f"source=yfinance_hist{' (DRY RUN)' if args.dry_run else ''}")

    rows: list[dict] = []
    failed: list[str] = []
    empty: list[str] = []
    per_symbol: dict[str, int] = {}

    for i, sym in enumerate(syms, 1):
        try:
            dates = fetch_history(sym, args.limit)
        except Exception as exc:  # noqa: BLE001 — a dead symbol must not stop the run
            failed.append(f"{sym}({type(exc).__name__})")
            continue
        if not dates:
            empty.append(sym)
            continue
        per_symbol[sym] = len(dates)
        rows += [{"symbol": sym, "reportDate": d, "session": None,
                  "source": "yfinance_hist"} for d in dates]
        if i % 25 == 0:
            print(f"  {i}/{len(syms)} · {len(rows)} rows so far")
        time.sleep(PAUSE_S)

    total = len(rows)
    covered = len(per_symbol)
    med = (sorted(per_symbol.values())[covered // 2] if covered else 0)
    print(f"\ncollected {total} past report dates across {covered} symbol(s) "
          f"(median {med} per name)")

    # NAMED, not silently absent. A universe that half-loaded quietly is how a
    # backtest ends up measured on 100 names while reporting 244.
    if empty:
        print(f"  no history returned for {len(empty)}: {', '.join(sorted(empty)[:20])}"
              + (" …" if len(empty) > 20 else ""))
    if failed:
        print(f"  FAILED for {len(failed)}: {', '.join(sorted(failed)[:20])}"
              + (" …" if len(failed) > 20 else ""))

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    sent = 0
    for i in range(0, total, BATCH):
        chunk = rows[i:i + BATCH]
        try:
            r = requests.post(f"{base}/api/earnings-calendar/", headers=headers,
                              json={"rows": chunk}, timeout=120)
            if r.status_code == 200:
                sent += len(chunk)
            else:
                print(f"  batch {i // BATCH + 1}: HTTP {r.status_code} {r.text[:120]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  batch {i // BATCH + 1}: {type(exc).__name__} {str(exc)[:110]}")
    print(f"\nupserted {sent} of {total} rows")

    try:
        from ..run_log import log_run
        log_run("earnings-backfill", "harvest",
                "ok" if sent == total and not failed else "partial",
                error=(f"{len(failed)} symbol(s) failed" if failed else None),
                summary=f"{sent} past dates across {covered} symbols")
    except Exception:  # noqa: BLE001
        pass
    return 0
