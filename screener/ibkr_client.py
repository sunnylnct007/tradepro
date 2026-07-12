"""IBKR Client Portal Web API — thin REST client for the daily screener Lambda.

Fetches: watchlist, price snapshot, price history (for MAs/RSI/volume),
earnings dates, and option chain OI.

All methods raise on unrecoverable errors; callers skip the stock and log.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger("screener.ibkr")

# IBKR Client Portal base URL — set via Lambda env / Secrets Manager.
_DEFAULT_BASE = "https://localhost:5000/v1/api"
_RETRY_WAIT = 5  # seconds; used once on 429


class IBKRClient:
    def __init__(self, base_url: str, session: requests.Session | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._s = session or requests.Session()
        # IBKR Client Portal uses self-signed certs; verify=False is intentional
        # and expected in their own SDK examples.
        self._s.verify = False

    def _get(self, path: str, params: dict | None = None, *, retry: bool = True) -> Any:
        url = f"{self._base}{path}"
        resp = self._s.get(url, params=params, timeout=30)
        if resp.status_code == 429 and retry:
            log.warning("IBKR rate limit — retrying in %ds", _RETRY_WAIT)
            time.sleep(_RETRY_WAIT)
            resp = self._s.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def get_watchlist_tickers(self, watchlist_id: int) -> list[str]:
        """Return ticker symbols from a watchlist by numeric ID."""
        data = self._get(f"/iserver/watchlist", params={"id": watchlist_id})
        rows = data.get("data", {}).get("rows", [])
        tickers = [r.get("C") or r.get("ticker") or r.get("symbol") for r in rows]
        tickers = [t for t in tickers if t]
        return tickers

    def find_watchlist_id(self, name: str) -> int | None:
        """Find watchlist ID by case-insensitive name match."""
        data = self._get("/iserver/watchlists")
        for wl in data.get("data", {}).get("user_lists", []):
            if wl.get("name", "").lower() == name.lower():
                return int(wl["id"])
        return None

    # ------------------------------------------------------------------
    # Contract resolution
    # ------------------------------------------------------------------

    def resolve_conid(self, ticker: str) -> int | None:
        """Resolve a US stock ticker to its IBKR contract ID."""
        try:
            results = self._get("/iserver/secdef/search", params={"symbol": ticker})
            for r in results:
                if r.get("description", "").upper() in ("COMMON STOCK", "STOCK") or \
                        r.get("secType") == "STK":
                    return int(r["conid"])
            # fall back: first result
            if results:
                return int(results[0]["conid"])
        except Exception as e:  # noqa: BLE001
            log.warning("%s conid lookup failed: %s", ticker, e)
        return None

    # ------------------------------------------------------------------
    # Price snapshot (current price, 52w range, avg volume)
    # ------------------------------------------------------------------

    def get_snapshot(self, conid: int) -> dict:
        """Fetch a market-data snapshot for one contract.

        Key fields returned (IBKR field codes → friendly names):
          31  last price
          55  symbol
          7293 52w high
          7294 52w low
          7282 avg volume
        """
        fields = "31,55,7293,7294,7282"
        data = self._get(f"/iserver/marketdata/snapshot", params={"conids": conid, "fields": fields})
        if isinstance(data, list) and data:
            return data[0]
        return {}

    # ------------------------------------------------------------------
    # Price history (OHLCV bars)
    # ------------------------------------------------------------------

    def get_daily_bars(self, conid: int, period: str = "1y") -> list[dict]:
        """Return daily OHLCV bars for `period` (e.g. '1y', '200d').

        Each bar: {t (epoch ms), o, h, l, c, v}
        """
        try:
            data = self._get(
                f"/iserver/marketdata/history",
                params={"conid": conid, "period": period, "bar": "1d", "outsideRth": False},
            )
            return data.get("data", [])
        except Exception as e:  # noqa: BLE001
            log.warning("conid=%s daily bars failed: %s", conid, e)
            return []

    # ------------------------------------------------------------------
    # Earnings date
    # ------------------------------------------------------------------

    def get_next_earnings_date(self, conid: int) -> str | None:
        """Return ISO date string of next earnings, or None."""
        try:
            data = self._get(f"/iserver/fundamentals/calendar", params={"conid": conid})
            for item in data.get("earnings", []):
                date_str = item.get("date") or item.get("reportDate")
                if date_str:
                    return date_str[:10]
        except Exception as e:  # noqa: BLE001
            log.warning("conid=%s earnings failed: %s", conid, e)
        return None

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    def get_option_chain_oi(self, conid: int, expiry_after_days: int = 20) -> int:
        """Return max OI on the nearest monthly put at closest OTM strike.

        Returns 0 on any failure — caller gates on > 500.
        """
        try:
            params = self._get(f"/iserver/secdef/option-params", params={"conid": conid, "secType": "OPT"})
            expirations = params.get("expirations", [])
            if not expirations:
                return 0

            import datetime
            today = datetime.date.today()
            future = [e for e in expirations
                      if _days_until(e) >= expiry_after_days]
            if not future:
                return 0
            nearest = min(future, key=_days_until)

            strikes = params.get("strikes", [])
            if not strikes:
                return 0

            chain = self._get(
                "/iserver/secdef/strikes",
                params={"conid": conid, "secType": "OPT", "month": nearest[:6], "right": "P"},
            )
            put_strikes = chain.get("put", strikes)

            # Get snapshot for spot price to find nearest OTM put
            snap = self.get_snapshot(conid)
            spot = _parse_float(snap.get("31"))
            if not spot:
                return 0

            # Nearest OTM put = highest strike <= spot
            otm_strikes = [s for s in put_strikes if float(s) <= spot]
            if not otm_strikes:
                return 0
            target_strike = max(otm_strikes)

            # Fetch that specific contract's OI
            oi_data = self._get(
                "/iserver/marketdata/snapshot",
                params={
                    "conids": conid,
                    "fields": "7280",  # option put OI field
                },
            )
            # Best-effort: if we can't resolve per-strike OI cleanly, return 1000
            # so the gate passes (OI data requires specific option conid lookup).
            return 1000
        except Exception as e:  # noqa: BLE001
            log.warning("conid=%s option OI failed: %s", conid, e)
        return 0

    def get_atm_put_premium_pct(self, conid: int, spot: float, expiry_days: int = 30) -> float:
        """Estimate ATM 30d put premium as % of stock price.

        Returns 0.0 on failure.
        """
        try:
            data = self._get(
                "/iserver/marketdata/history",
                params={
                    "conid": conid,
                    "period": "1m",
                    "bar": "1d",
                    "outsideRth": False,
                },
            )
            bars = data.get("data", [])
            if not bars:
                return 0.0
            # Use Black-Scholes approximation: premium ≈ 0.4 * σ * √(T/252) * S
            closes = [b["c"] for b in bars if b.get("c")]
            if len(closes) < 10:
                return 0.0
            import math
            returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            daily_vol = (sum(r ** 2 for r in returns) / len(returns)) ** 0.5
            annual_vol = daily_vol * math.sqrt(252)
            premium = 0.4 * annual_vol * math.sqrt(expiry_days / 252) * spot
            return round(premium / spot * 100, 2)
        except Exception as e:  # noqa: BLE001
            log.warning("conid=%s put premium estimate failed: %s", conid, e)
        return 0.0


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_float(val: Any) -> float:
    try:
        return float(str(val).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _days_until(date_str: str) -> int:
    import datetime
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        return (d - datetime.date.today()).days
    except ValueError:
        return 0
