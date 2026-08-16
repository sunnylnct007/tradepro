"""yfinance provider — first concrete Provider.

yfinance is the default for every (asset_class, resolution) tuple
TradePro consumes today. It's free, no auth, but rate-limits
aggressively under load (HTTP 429 spikes around the US market open).

This provider wraps the existing yfinance call pattern (already used
by ``cache.py``) but normalises the output to the BarStore's column
contract + raises typed errors.

Known yfinance limits we encode in ``max_history``:
  * 1m bars: 30 days back from now (yfinance actual limit; was incorrectly coded as 7d)
  * 2m / 5m / 15m / 30m: 60 days
  * 1h: ~730 days
  * 1d / 1wk / 1mo: ~max (decades; treated as unlimited)

These are documented in ``CURRENT_BACKTEST_LIMITATIONS.md`` §L1 — they
are the reason the trustworthy-data roadmap exists.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from ...yf_noise import quiet_yfinance_delisted_noise
from ..errors import (
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
)
from .base import Provider, register_provider


_log = logging.getLogger("tradepro.bar_cache.yfinance")

# Silence yfinance's benign "possibly delisted; no price data" ERROR spam
# (holiday gaps trip it across the whole universe on every warmup fetch).
# Typed ProviderRateLimitError / ProviderNetworkError still surface real
# failures.
quiet_yfinance_delisted_noise()


# Resolutions yfinance accepts mapped to its interval strings + the
# documented depth limit. ``None`` = unlimited.
_RESOLUTION_LIMITS: dict[str, tuple[str, timedelta | None]] = {
    "1m":  ("1m",  timedelta(days=30)),
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

    # Yahoo only allows 7 days of 1m data per single request; chunk
    # larger windows to get up to 30 days total.
    _1M_CHUNK_DAYS: int = 7

    def _call_yfinance(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Dispatch to the injected fn (tests) or real yfinance.

        For 1m resolution, Yahoo enforces a per-request limit of ~7
        days.  Requests covering a wider window silently return an empty
        DataFrame (no exception, just no data).  We automatically slice
        the window into ≤7-day chunks and concatenate so callers don't
        have to know about this quirk.

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

        # For 1m, split into 7-day chunks so each request stays within
        # Yahoo's per-request limit.  For other resolutions, make one call.
        if interval == "1m":
            return self._call_yfinance_chunked(yf, symbol, interval, start, end)
        return self._call_yfinance_single(yf, symbol, interval, start, end)

    def _call_yfinance_chunked(
        self, yf: Any, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Fetch 1m bars in ≤7-day slices and concatenate."""
        from datetime import timezone as _tz

        chunk = timedelta(days=self._1M_CHUNK_DAYS)
        frames: list[pd.DataFrame] = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + chunk, end)
            df_chunk = self._call_yfinance_single(yf, symbol, interval, cursor, chunk_end)
            if not df_chunk.empty:
                frames.append(df_chunk)
            cursor = chunk_end

        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames)
        # Drop duplicates that can appear at chunk boundaries.
        result = result[~result.index.duplicated(keep="first")]
        return result.sort_index()

    @staticmethod
    def _session():
        """A yfinance session with a REAL timeout.

        Without one, `ticker.history()` blocks on a socket read that has no
        deadline. On 15 Aug 2026 the identical omission in the OPTION-CHAIN
        fetcher (`quant_engine/options/chains.py`) turned a single day's
        options-screen run into 2h50m while Yahoo rate-limited this machine.
        This is the same bug in the BAR path, and it is what stopped the 5-minute
        harvest: it started 2026-08-16T10:17:54Z, never printed a completion
        line, and never reported to run_log — so the Data screen showed the lane
        as broken while the job was simply hanging.

        A fallback with no time bound is not a fallback, it is a hang.
        """
        cached = getattr(YFinanceProvider, "_YF_SESSION", None)
        if cached is not None:
            return cached
        timeout = float(os.environ.get("TRADEPRO_YF_TIMEOUT_S", "8"))
        sess = None
        try:
            from curl_cffi import requests as _cr
            sess = _cr.Session(timeout=timeout)
        except Exception:  # noqa: BLE001 — no session beats no bars
            try:
                import functools
                import requests as _rq
                s = _rq.Session()
                s.request = functools.partial(s.request, timeout=timeout)  # type: ignore[method-assign]
                sess = s
            except Exception:  # noqa: BLE001
                sess = None
        YFinanceProvider._YF_SESSION = sess
        return sess

    @staticmethod
    def _call_yfinance_single(
        yf: Any, symbol: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        try:
            sess = YFinanceProvider._session()
            ticker = yf.Ticker(symbol, session=sess) if sess is not None else yf.Ticker(symbol)
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
