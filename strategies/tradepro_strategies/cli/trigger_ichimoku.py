"""tradepro-trigger-ichimoku — fetch universe symbols then POST to
/api/ops/run-intraday, kicking off an Ichimoku paper session on the
server without the CLI needing to own strategy logic.

Usage
-----
  # Equity — fetches sp500 symbols from DB, posts to run-intraday
  tradepro-trigger-ichimoku --strategy ichimoku_equity --universe sp500

  # FX — no symbol list needed; G10 pairs are built into the strategy
  tradepro-trigger-ichimoku --strategy ichimoku_fx_mr
"""
from __future__ import annotations

import argparse
import os
import sys

import requests


def _base_url() -> str:
    return os.environ.get("TRADEPRO_API_BASE", "http://16.60.201.137").rstrip("/")


def _token() -> str | None:
    return os.environ.get("TRADEPRO_INGEST_TOKEN")


def _fetch_universe_symbols(base: str, universe: str) -> list[str]:
    url = f"{base}/api/universes/{universe}"
    print(f"Fetching universe: {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    symbols = [
        s["ticker"]
        for s in data.get("symbols", [])
        if s.get("effective") is True
    ]
    print(f"  {len(symbols)} effective symbols in {universe}")
    return symbols


def main() -> None:
    p = argparse.ArgumentParser(
        prog="tradepro-trigger-ichimoku",
        description="Trigger an Ichimoku intraday session via /api/ops/run-intraday.",
    )
    p.add_argument(
        "--strategy", required=True,
        choices=["ichimoku_equity", "ichimoku_fx_mr"],
        help="Strategy to run.",
    )
    p.add_argument(
        "--universe", default=None,
        help="Universe name (e.g. sp500). Required for ichimoku_equity.",
    )
    p.add_argument(
        "--placement-mode", default="manual",
        help="Order placement mode (default: manual).",
    )
    p.add_argument(
        "--capital-usd", type=float, default=100_000,
        help="Capital in USD (default: 100000).",
    )
    args = p.parse_args()

    base = _base_url()
    token = _token()

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload: dict[str, object] = {
        "strategy": args.strategy,
        "placement_mode": args.placement_mode,
        "capital_usd": args.capital_usd,
    }

    if args.strategy == "ichimoku_equity":
        universe = args.universe or "sp500"
        symbols = _fetch_universe_symbols(base, universe)
        payload["symbols"] = symbols
        payload["universe"] = universe

    url = f"{base}/api/ops/run-intraday"
    print(f"POST {url}  strategy={args.strategy}")
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    print(f"  -> {resp.status_code} {resp.text[:200]}")


if __name__ == "__main__":
    main()
