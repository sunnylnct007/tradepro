"""Backfill 52 weeks of implied volatility, once, so IV-Rank becomes usable.

THE PROBLEM THIS SOLVES
-----------------------
The wheel's premium-richness gate is supposed to be IV-RANK: where today's
implied vol sits in the name's own trailing 52-week range. That is the measure
the owner asked for — "no hardcoded cosntandt but variableper symbol" — because
a rank is defined entirely by the name's own history.

It has never been reachable. IV-Rank needs a deep window (60 days minimum, a
year to mean much) and we have been accumulating it ONE SESSION AT A TIME since
9 Aug 2026. Measured 21 Oct, after ten weeks:

    NVDA 41 days · MSFT 31 · PG 31 · WDC 15 · CRWV 0

That is ~63% of sessions even while the capture lane was alive, and it was dead
from 5 Oct to 21 Oct. At that rate a usable rank arrives around year-end.

Until then the risk engine falls back to the IV/HV ratio BRIDGE — and the owner
disproved that gate on WDC: it passes at a 30-day window and fails at 6-month
and 1-year (1.09x vs 0.78x / 0.80x). The window was never chosen; it was
whatever IBKR happened to return. We have been gating real trades on it.

WHY THIS IS A ONE-OFF AND NOT A LANE
------------------------------------
IBKR serves the entire year of daily implied vol in a SINGLE request per
symbol, via reqHistoricalData whatToShow="OPTION_IMPLIED_VOLATILITY", and
serves it OUTSIDE market hours. So the Gateway — retired on 9 Aug because every
screen run paid a 20s connect timeout — needs to be running for the few minutes
this takes, not permanently. Afterwards the existing daily capture maintains
the window and nothing depends on a Gateway again.

Idempotent: the store upserts on (symbol, trade_date), so re-running is safe
and a partial run can simply be repeated.

    tradepro-iv-backfill --dry-run        # show coverage, touch nothing
    tradepro-iv-backfill                  # backfill the wheel universe
    tradepro-iv-backfill --symbols KO,PG  # or just these
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import requests

from ..quant_engine.options.iv_rank import fetch_iv_history
from .push_to_api import load_credentials

log = logging.getLogger("tradepro.iv_backfill")

# IBKR paces historical requests hard and answers a breach with silence rather
# than an error, which would look exactly like "this symbol has no IV history".
# One request every 2s is far inside the published ceiling and still finishes a
# 90-name universe in about three minutes.
PACE_S = float(os.environ.get("TRADEPRO_IV_BACKFILL_PACE_S", "2.0"))

#: Distinct from the daily snapshot's source so provenance stays honest: these
#: rows came from IBKR's historical IV series, not from a live field read.
SOURCE = "ibkr_gateway_hist_iv"


def _universe() -> list[str]:
    env = os.environ.get("TRADEPRO_WHEEL_UNIVERSE", "").strip()
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    from .options_screen import DEFAULT_UNIVERSE  # late: heavy import
    return list(DEFAULT_UNIVERSE)


def _coverage(base: str, token: str | None, sym: str) -> int:
    """How many distinct days we already hold. Reported before AND after, so
    the run's effect is a measured delta rather than a claim."""
    try:
        r = requests.get(f"{base.rstrip('/')}/api/options/iv-daily/{sym}?days=400",
                         headers={"Authorization": f"Bearer {token}"} if token else {},
                         timeout=20)
        if r.status_code != 200:
            return -1
        d = r.json() or {}
        rows = d.get("rows") or d.get("series") or []
        return len({str(x.get("tradeDate") or x.get("trade_date") or "")[:10]
                    for x in rows if x})
    except Exception:  # noqa: BLE001 — unknown coverage, not zero coverage
        return -1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", help="comma-separated; default = wheel universe")
    ap.add_argument("--lookback", default="1 Y", help='IBKR duration, e.g. "1 Y"')
    ap.add_argument("--dry-run", action="store_true",
                    help="report current coverage and exit without connecting")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    syms = ([s.strip().upper() for s in a.symbols.split(",") if s.strip()]
            if a.symbols else _universe())
    base, token = load_credentials()
    if not base:
        print("no API base configured — nowhere to write the history", file=sys.stderr)
        return 2

    before = {s: _coverage(base, token, s) for s in syms}
    shallow = sum(1 for v in before.values() if 0 <= v < 60)
    print(f"{len(syms)} symbol(s); {shallow} with fewer than the 60 days IV-Rank "
          f"needs (median {sorted(before.values())[len(before)//2]})")
    if a.dry_run:
        for s in syms:
            print(f"  {s:6s} {before[s]:4d} days")
        return 0

    try:
        import ib_insync
    except Exception as e:  # noqa: BLE001
        print(f"ib_insync unavailable: {e}", file=sys.stderr)
        return 2

    host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
    cid = int(os.environ.get("TRADEPRO_IBKR_DATA_CLIENT_ID", "96"))
    ib = ib_insync.IB()
    try:
        ib.connect(host, port, clientId=cid, timeout=20)
    except Exception as e:  # noqa: BLE001
        # The ONE thing this tool needs, so say exactly what to do about it.
        print(f"\nIBKR Gateway not reachable at {host}:{port} ({e}).\n"
              f"This tool needs it running for a few minutes — start IB Gateway "
              f"(or TWS), check the API port matches TRADEPRO_IBKR_PORT, and run "
              f"again. Nothing was written.", file=sys.stderr)
        return 1

    ok = failed = rows_written = 0
    try:
        for i, sym in enumerate(syms, 1):
            try:
                series = fetch_iv_history(sym, ib, lookback=a.lookback)
                payload = {"rows": [{"symbol": sym, "tradeDate": d, "iv": iv,
                                     "hv30": None, "source": SOURCE}
                                    for d, iv in series]}
                r = requests.post(
                    f"{base.rstrip('/')}/api/options/iv-daily", json=payload,
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    timeout=60)
                # A 4xx IS NOT AN EXCEPTION. Without this check a rejected write
                # looks identical to a stored one — the mistake this codebase has
                # already paid for on this exact endpoint.
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:100]}")
                rows_written += len(series)
                ok += 1
                print(f"  [{i:3d}/{len(syms)}] {sym:6s} {before[sym]:3d} -> "
                      f"{len(series):3d} days")
            except Exception as e:  # noqa: BLE001 — one symbol must not end the run
                failed += 1
                print(f"  [{i:3d}/{len(syms)}] {sym:6s} FAILED: {str(e)[:90]}")
            time.sleep(PACE_S)
    finally:
        try:
            ib.disconnect()
        except Exception:  # noqa: BLE001
            pass

    after = {s: _coverage(base, token, s) for s in syms}
    deep = sum(1 for v in after.values() if v >= 60)
    print(f"\n{ok} ok, {failed} failed, {rows_written:,} IV rows written")
    print(f"{deep}/{len(syms)} symbol(s) now have the 60+ days IV-Rank needs "
          f"(was {sum(1 for v in before.values() if v >= 60)})")
    if failed:
        print("Re-running is safe — the store upserts on (symbol, trade_date).")
    return 1 if ok == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
