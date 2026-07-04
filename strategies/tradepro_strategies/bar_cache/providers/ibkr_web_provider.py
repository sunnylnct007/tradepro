"""ibkr_web — bar_cache provider that sources OHLCV from IBKR via the CENTRAL
backend endpoint ``/api/integrations/ibkr/price-history`` (which speaks the IBKR
OAuth Web API).

CENTRAL-API design (user rule 2026-07-04, 'central api handling, not scattered'):
the backend ``IBKRClient`` is the SINGLE IBKR handler; this provider is a THIN HTTP
consumer of one endpoint — it does NOT re-implement OAuth, and it avoids the local
IB Gateway (``ibkr_provider``), which hangs on trader+harvester session contention.
Graded GOOD by the quality grader (added to ``_TRUSTED_PROVIDERS``).

FAIL-LOUD (user rule 'don't swallow the error'): a symbol the backend can't price
(unresolved contract / empty history / non-200) raises ProviderNetworkError /
ProviderParseError WITH the backend's reason — never a silent empty frame — so the
harvest records the failure and the cockpit can FLAG the symbol.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from ..errors import ProviderNetworkError, ProviderParseError
from .base import Provider, register_provider

_log = logging.getLogger("tradepro.bar_cache.ibkr_web")

# resolution -> IBKR bar size. Daily + hourly for now (what UNIVERSE_CORE needs).
_BAR = {"1d": "1d", "1h": "1h"}


def _ibkr_period(start: datetime, end: datetime) -> str:
    """IBKR history 'period' window (counts back from now) that covers [start, end]."""
    days = max(1, (end - start).days)
    if days <= 30:
        return "1m"
    if days <= 180:
        return "6m"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"


class IBKRWebProvider(Provider):
    """Fetch bars from the central backend IBKR endpoint (Option B / Web API)."""

    name = "ibkr_web"

    def __init__(
        self,
        base: str | None = None,
        token: str | None = None,
        _get: Callable[[str, dict, int], tuple[int, dict]] | None = None,
    ) -> None:
        # base/token resolved lazily from the secret chain unless injected (tests).
        self._base = base
        self._token = token
        self._get = _get

    def supports_resolution(self, resolution: str) -> bool:
        return resolution in _BAR

    def max_history(self, resolution: str) -> timedelta:
        return timedelta(days=365 * 5) if resolution in _BAR else timedelta(0)

    def _resolve_base(self) -> tuple[str, str | None]:
        if self._base:
            return self._base, self._token
        from ...cli import push_to_api
        return push_to_api.load_credentials()

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
                provider=self.name, canonical=canonical,
                message=f"resolution {resolution!r} not supported by ibkr_web (1d/1h only)",
            )
        base, token = self._resolve_base()
        base = (base or "").rstrip("/")
        period, bar = _ibkr_period(start, end), _BAR[resolution]
        url = (f"{base}/api/integrations/ibkr/price-history"
               f"?symbol={canonical}&period={period}&bar={bar}")
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            if self._get is not None:
                status, payload = self._get(url, headers, 30)
            else:
                import requests
                r = requests.get(url, headers=headers, timeout=30)
                status, payload = r.status_code, (r.json() if r.content else {})
        except Exception as exc:  # noqa: BLE001 — surface as a typed network error
            raise ProviderNetworkError(
                provider=self.name, canonical=canonical,
                message=f"ibkr_web request failed: {exc}") from exc

        payload = payload or {}
        if status != 200:
            # The endpoint FAILS LOUD (502 + reason); propagate the reason.
            reason = payload.get("error") or f"HTTP {status}"
            raise ProviderNetworkError(
                provider=self.name, canonical=canonical, message=f"ibkr_web: {reason}")

        bars = payload.get("bars") or []
        if not bars:
            raise ProviderParseError(
                provider=self.name, canonical=canonical,
                message="ibkr_web returned no bars (backend reported success but empty)")

        df = pd.DataFrame(bars)
        # t = epoch MILLISECONDS UTC → tz-aware index; o/h/l/c/v → lowercase schema.
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = (df.set_index("timestamp")
                .rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
                [["open", "high", "low", "close", "volume"]]
                .sort_index())

        s = pd.Timestamp(start); e = pd.Timestamp(end)
        if s.tz is None:
            s = s.tz_localize("UTC")
        if e.tz is None:
            e = e.tz_localize("UTC")
        df = df[(df.index >= s) & (df.index <= e)]
        if df.empty:
            raise ProviderParseError(
                provider=self.name, canonical=canonical,
                message=f"ibkr_web: {len(bars)} bar(s) returned but none within "
                        f"[{s.date()}, {e.date()}]")

        meta = {"provider": self.name, "rows": int(len(df)),
                "conid": payload.get("conid"), "period": period, "bar": bar}
        return df, meta


register_provider(IBKRWebProvider())
