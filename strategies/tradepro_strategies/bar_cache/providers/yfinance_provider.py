"""yfinance provider — first concrete Provider.

yfinance is the default for every (asset_class, resolution) tuple
TradePro consumes today. It's free, no auth, but rate-limits
aggressively under load (HTTP 429 spikes around the US market open).

This provider wraps the existing yfinance call pattern (already used
by ``cache.py``) but normalises the output to the BarStore's column
contract + raises typed errors.

Known yfinance limits we encode in ``max_history``:
  * 1m bars: 7 days back from now
  * 2m / 5m / 15m / 30m: 60 days
  * 1h: ~730 days
  * 1d / 1wk / 1mo: ~max (decades; treated as unlimited)

These are documented in ``CURRENT_BACKTEST_LIMITATIONS.md`` §L1 — they
are the reason the trustworthy-data roadmap exists.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from ..errors import (
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
)
from .base import Provider, register_provider


_log = logging.getLogger("tradepro.bar_cache.yfinance")


# Resolutions yfinance accepts mapped to its interval strings + the
# documented depth limit. ``None`` = unlimited.
_RESOLUTION_LIMITS: dict[str, tuple[str, timedelta | None]] = {
    "1m":  ("1m",  timedelta(days=7)),
    "2m":  ("2m",  timedelta(days=60)),
    "5m":  ("5m",  timedelta(days=60)),
    "15m": ("15m", timedelta(days=60)),
    "30m": ("30m", timedelta(days=60)),
    "1h":  ("1h",  timedelta(days=730)),
    "1d":  ("1d",  None),
    "1wk": ("1wk", None),
    "1mo": ("1mo", None),
}


class YFinanceProvider(Provider):
    """yfinance wrapper. Injectable ``_fetch_fn`` for tests so the
    BDD suite doesn't hit the network."""

    name = "yfinance"

    def __init__(
        self,
        *,
        _fetch_fn=None,  # callable(symbol, interval, start, end) -> DataFrame
    ) -> None:
        self._fetch_fn = _fetch_fn

    def supports_resolution(self, resolution: str) -> bool:
        return resolution in _RESOLUTION_LIMITS

    def max_history(self, resolution: str) -> timedelta | None:
        if resolution not in _RESOLUTION_LIMITS:
            return timedelta(0)   # signal "not supported" via zero depth
        return _RESOLUTION_LIMITS[resolution][1]

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
                message=f"resolution {resolution!r} not supported by yfinance",
            )
        interval, _ = _RESOLUTION_LIMITS[resolution]

        try:
            df = self._call_yfinance(canonical, interval, start, end)
        except _RateLimitSentinel as exc:
            raise ProviderRateLimitError(self.name, canonical, str(exc)) from exc
        except _NetworkSentinel as exc:
            raise ProviderNetworkError(self.name, canonical, str(exc)) from exc

        # Normalise columns. yfinance returns capitalised column names
        # and a DatetimeIndex; we want lowercase + tz-aware UTC index.
        if df.empty:
            return df, {"provider_version": self._yf_version(), "rows": 0}

        df = self._normalise(df)
        metadata = {
            "provider_version": self._yf_version(),
            "rows": int(len(df)),
            "interval": interval,
        }
        return df, metadata

    # ── Internal helpers ────────────────────────────────────────────

    def _call_yfinance(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Dispatch to the injected fn (tests) or real yfinance.

        Production yfinance raises a variety of exceptions on rate
        limit / network failure; we sniff them and re-raise as our
        typed sentinels so ``fetch`` can map cleanly. The sniff is
        intentionally tolerant — yfinance error strings change across
        releases and we'd rather over-classify as rate-limit than miss
        a real 429."""
        if self._fetch_fn is not None:
            return self._fetch_fn(symbol, interval, start, end)

        # Production path — late import so the BDD tests don't pay
        # the yfinance import cost when they inject a fake fetch.
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ProviderNetworkError(
                self.name, symbol, "yfinance not installed",
            ) from exc

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                interval=interval,
                start=start,
                end=end,
                auto_adjust=True,
                actions=False,
            )
            return df
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "429" in msg or "too many requests" in msg or "rate" in msg:
                raise _RateLimitSentinel(str(exc)) from exc
            raise _NetworkSentinel(str(exc)) from exc

    @staticmethod
    def _normalise(df: pd.DataFrame) -> pd.DataFrame:
        """Standardise to lowercase columns + tz-aware UTC index +
        ``adj_factor`` + ``source`` columns. The downstream asset-
        class validator checks the result has the schema columns."""
        df = df.copy()
        # Lowercase columns
        df.columns = [str(c).lower() for c in df.columns]
        # Guarantee tz-aware UTC index. yfinance returns tz-naive in
        # some configurations; tz-aware in others. Make it consistent.
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        df.index.name = "timestamp"
        # Some yfinance frames include 'adj close'; map it to adj_factor
        # (close / unadjusted_close). When auto_adjust=True the close
        # IS the adjusted price so adj_factor is implicit 1.0.
        if "adj_factor" not in df.columns:
            df["adj_factor"] = 1.0
        df["source"] = "yfinance"
        return df

    @staticmethod
    def _yf_version() -> str:
        try:
            import yfinance as yf
            return getattr(yf, "__version__", "unknown")
        except ImportError:
            return "not-installed"


# Sentinels used internally so the public API only raises the typed
# BarFetchError subclasses.
class _RateLimitSentinel(Exception):
    pass


class _NetworkSentinel(Exception):
    pass


# Auto-register a default instance for production use. Tests inject
# their own via the injectable fetch_fn.
register_provider(YFinanceProvider())
