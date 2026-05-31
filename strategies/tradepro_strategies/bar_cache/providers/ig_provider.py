"""IG provider — second provider in the chain.

Proxies through the backend's ``/api/admin/data-trust/ig/prices``
endpoint (Phase B-4 added it). The backend wraps IGClient.cs which
owns the IG REST auth + session — no IG credentials in the Python
process.

What this provider gives the trustworthy data layer:
  * Multi-year 1-minute equity history (yfinance is capped at 7 days
    — see CURRENT_BACKTEST_LIMITATIONS.md §L1, the CRITICAL one).
  * Documented depth per resolution (IG demo allows ~10k datapoints
    /week; max_history is effectively unlimited for daily, deep for
    intraday subject to the allowance).

The bar shape we get back from the backend is already normalised
(timestamp + OHLCV). The provider's job is:
  1. Resolve canonical → IG epic via IGEpicMap.
  2. Map BarStore resolution (1m, 5m, 1h, 1d) to IG's strings
     (MINUTE, MINUTE_5, HOUR, DAY).
  3. POST timestamps as ISO 8601 UTC.
  4. Convert the response to a tz-aware pandas DataFrame matching
     us_equity_v1 schema.
  5. Surface typed errors so the BarStore chain can fall through.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd

from ..errors import (
    BarFetchError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
)
from ...paper.ig_epic_map import (
    DEFAULT_MAP_PATH,
    IGEpicMap,
    IGEpicMissingError,
)
from .base import Provider, register_provider


_log = logging.getLogger("tradepro.bar_cache.ig")


# Canonical (BarStore) → IG resolution string.
_RESOLUTION_MAP: dict[str, str] = {
    "1m":  "MINUTE",
    "2m":  "MINUTE_2",
    "5m":  "MINUTE_5",
    "10m": "MINUTE_10",
    "15m": "MINUTE_15",
    "30m": "MINUTE_30",
    "1h":  "HOUR",
    "2h":  "HOUR_2",
    "1d":  "DAY",
    "1wk": "WEEK",
    "1mo": "MONTH",
}


# Documented max history per resolution. ``None`` = effectively
# unlimited (IG keeps multi-year DAY history; intraday depth is
# bounded by allowance not by absolute cutoff).
_MAX_HISTORY: dict[str, timedelta | None] = {
    "1m":  timedelta(days=730),    # 2-year typical demo intraday depth
    "5m":  timedelta(days=1825),
    "15m": timedelta(days=1825),
    "30m": timedelta(days=1825),
    "1h":  None,
    "1d":  None,
    "1wk": None,
    "1mo": None,
}


class IGProvider(Provider):
    """IG /prices provider.

    Construction notes:
      * ``api_base`` — the TradePro backend URL. Without it the provider
        raises ProviderNetworkError on every fetch (a degraded mode
        the chain can fall through cleanly).
      * ``epic_map`` — IGEpicMap to resolve canonical → epic. Defaults
        to the package-shipped map; tests inject a synthetic one.
      * ``auth_token`` — Bearer token sent to the backend admin
        endpoint. Falls back to TRADEPRO_API_TOKEN.
      * ``_http_get`` — injectable for tests so the BDD doesn't hit
        the network.

    The provider is registered globally at import time only when an
    API base is set in the environment; for unit tests / offline
    Python sessions, instantiate explicitly and register via
    ``register_provider``."""

    name = "ig"

    def __init__(
        self,
        *,
        api_base: Optional[str] = None,
        epic_map: Optional[IGEpicMap] = None,
        auth_token: Optional[str] = None,
        timeout_seconds: float = 30.0,
        _http_get: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._api_base = (
            api_base.rstrip("/") if api_base
            else os.environ.get("TRADEPRO_API_URL", "").rstrip("/")
        )
        self._epic_map = epic_map
        self._auth_token = auth_token or os.environ.get("TRADEPRO_API_TOKEN")
        self._timeout = timeout_seconds
        self._http_get = _http_get

    # ── Provider API ────────────────────────────────────────────

    def supports_resolution(self, resolution: str) -> bool:
        return resolution in _RESOLUTION_MAP

    def max_history(self, resolution: str) -> timedelta | None:
        return _MAX_HISTORY.get(resolution, timedelta(0))

    def fetch(
        self,
        canonical: str,
        asset_class: str,
        resolution: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        if not self.supports_resolution(resolution):
            raise ProviderParseError(
                provider=self.name,
                canonical=canonical,
                message=f"resolution {resolution!r} not supported by IG provider",
            )
        if not self._api_base:
            raise ProviderNetworkError(
                provider=self.name,
                canonical=canonical,
                message=(
                    "no API base configured; set TRADEPRO_API_URL or "
                    "pass api_base to IGProvider"
                ),
            )

        epic = self._resolve_epic(canonical)
        ig_resolution = _RESOLUTION_MAP[resolution]

        params = {
            "epic": epic,
            "resolution": ig_resolution,
            "from": _ensure_utc(start).strftime("%Y-%m-%dT%H:%M:%S"),
            "to":   _ensure_utc(end).strftime("%Y-%m-%dT%H:%M:%S"),
            "max":  5000,
        }
        url = f"{self._api_base}/api/admin/data-trust/ig/prices"
        headers = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        try:
            if self._http_get is not None:
                resp = self._http_get(url, params=params, headers=headers,
                                      timeout=self._timeout)
            else:
                import requests
                resp = requests.get(
                    url, params=params, headers=headers,
                    timeout=self._timeout,
                )
        except Exception as exc:  # noqa: BLE001
            raise ProviderNetworkError(
                provider=self.name,
                canonical=canonical,
                message=f"IG endpoint unreachable: {exc}",
            ) from exc

        status = getattr(resp, "status_code", None)
        if status == 403:
            # IG returns 403 for both auth failure and exceeded-allowance.
            # We bucket as rate-limit so the chain falls through to
            # yfinance; the cockpit telemetry shows the distinction
            # via the error_message field.
            raise ProviderRateLimitError(
                provider=self.name,
                canonical=canonical,
                message=f"IG 403 (likely weekly allowance exceeded): {self._error_body(resp)}",
            )
        if status == 429:
            raise ProviderRateLimitError(
                provider=self.name,
                canonical=canonical,
                message=f"IG 429: {self._error_body(resp)}",
            )
        if not getattr(resp, "ok", False):
            raise ProviderParseError(
                provider=self.name,
                canonical=canonical,
                message=(
                    f"IG /prices returned {status}: {self._error_body(resp)}"
                ),
            )

        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderParseError(
                provider=self.name,
                canonical=canonical,
                message=f"IG response was not JSON: {exc}",
            ) from exc

        bars = body.get("bars") or []
        if not bars:
            return pd.DataFrame(), {
                "provider_version": "ig/v3",
                "rows": 0,
                "allowance_remaining": body.get("allowanceRemaining"),
                "allowance_total": body.get("allowanceTotal"),
                "epic": epic,
                "ig_resolution": ig_resolution,
            }

        df = pd.DataFrame(bars)
        # Backend wire format: timestamp, open, high, low, close, volume.
        # Add adj_factor=1.0 + source for the schema; tz-aware UTC index.
        try:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        except Exception as exc:  # noqa: BLE001
            raise ProviderParseError(
                provider=self.name,
                canonical=canonical,
                message=f"could not parse IG timestamps: {exc}",
            ) from exc
        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        df["adj_factor"] = 1.0
        df["source"] = "ig"

        metadata = {
            "provider_version": "ig/v3",
            "rows": int(len(df)),
            "allowance_remaining": body.get("allowanceRemaining"),
            "allowance_total": body.get("allowanceTotal"),
            "epic": epic,
            "ig_resolution": ig_resolution,
        }
        return df, metadata

    # ── Helpers ─────────────────────────────────────────────────

    def _resolve_epic(self, canonical: str) -> str:
        try:
            epic_map = self._epic_map or IGEpicMap.load(DEFAULT_MAP_PATH)
        except Exception as exc:  # noqa: BLE001
            raise ProviderParseError(
                provider=self.name,
                canonical=canonical,
                message=f"could not load IG epic map: {exc}",
            ) from exc
        try:
            entry = epic_map.get(canonical)
        except IGEpicMissingError as exc:
            raise ProviderParseError(
                provider=self.name,
                canonical=canonical,
                message=f"no IG epic mapped for {canonical}: {exc}",
            ) from exc
        return entry.epic

    @staticmethod
    def _error_body(resp) -> str:
        try:
            return (getattr(resp, "text", "") or "")[:200]
        except Exception:  # noqa: BLE001
            return "<unreadable response>"


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


# Register a default instance for production. The provider is safe to
# register without an API base — fetches will raise ProviderNetworkError
# until TRADEPRO_API_URL is set, and the BarStore chain falls through
# cleanly. The Python BarStore code path always reads the chain from
# the preferences table (Phase B-3), so this registration just makes
# "ig" resolvable when an operator lists it in their chain.
register_provider(IGProvider())
