"""tradepro-verify-tws — smoke-test the IBKR bar provider.

Connects to TWS at localhost:7497 (or TRADEPRO_IBKR_PORT) and fetches
one day of 1m bars for SPY. Prints a pass/fail summary.

Usage:
    .venv/bin/tradepro-verify-tws
    .venv/bin/tradepro-verify-tws --symbol AAPL --date 2026-06-05

Exit 0 = provider connected and returned bars.
Exit 1 = connection refused / no bars / error.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test IBKR TWS bar provider")
    parser.add_argument("--symbol", default="SPY", help="Symbol to fetch (default: SPY)")
    parser.add_argument(
        "--date",
        default=None,
        help="YYYY-MM-DD (default: yesterday). Must be a weekday with data.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=7497, type=int)
    args = parser.parse_args()

    # ── Determine target date ──────────────────────────────────────────
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: bad date {args.date!r} — expected YYYY-MM-DD", file=sys.stderr)
            return 1
    else:
        # roll back to the most recent weekday
        target = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ) - timedelta(days=1)
        while target.weekday() >= 5:   # Sat=5, Sun=6
            target -= timedelta(days=1)

    end = target.replace(hour=23, minute=59, second=59)
    print(f"Attempting IBKR bar fetch: {args.symbol} 1m  {target.date()}")
    print(f"  host={args.host}  port={args.port}")

    # ── Import and attempt fetch ───────────────────────────────────────
    try:
        from tradepro_strategies.bar_cache.providers.ibkr_provider import IBKRProvider
    except ImportError as exc:
        print(f"FAIL: cannot import IBKRProvider — {exc}", file=sys.stderr)
        return 1

    provider = IBKRProvider(host=args.host, port=args.port)

    try:
        df, meta = provider.fetch(
            canonical=args.symbol,
            asset_class="us_etf",
            resolution="1m",
            start=target,
            end=end,
        )
    except Exception as exc:
        print(f"\nFAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        _print_tws_setup_hint(args.port)
        return 1

    if df.empty:
        print(
            f"\nWARN: provider connected but returned 0 bars for {args.symbol} on {target.date()}.\n"
            "  This can happen for non-trading days, or if the TWS market data subscription\n"
            "  doesn't cover SPY (try --symbol AAPL or any equity you have data for).",
            file=sys.stderr,
        )
        return 1

    print(f"\n✓ SUCCESS — {len(df)} bars  provider_used=ibkr  elapsed={meta.get('elapsed_s', '?')}s")
    print(f"  first bar: {df.index[0]}  close={df['close'].iloc[0]:.2f}")
    print(f"  last  bar: {df.index[-1]}  close={df['close'].iloc[-1]:.2f}")
    print("\nIBKR bar cache is live. Friday replay will work.")
    return 0


def _print_tws_setup_hint(port: int) -> None:
    print(
        f"\n── TWS API setup (one-time) ─────────────────────────────────────\n"
        f"  1. Open IBKR Trader Workstation (TWS)\n"
        f"  2. Menu: Edit → Global Configuration → API → Settings\n"
        f"  3. Tick:  ☑ Enable ActiveX and Socket Clients\n"
        f"  4. Port:  {port}   (TWS default; IB Gateway paper uses 4002)\n"
        f"  5. Trusted IPs: add  127.0.0.1  (or leave blank for localhost)\n"
        f"  6. Untick Read-Only API  (optional — we connect with readonly=True)\n"
        f"  7. Click OK / Apply — TWS restarts the socket listener\n"
        f"  8. Re-run: .venv/bin/tradepro-verify-tws\n"
        f"─────────────────────────────────────────────────────────────────\n",
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())
