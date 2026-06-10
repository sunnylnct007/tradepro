"""IBKR historical-bar provider — third provider in the chain.

What IBKR gives us that other providers can't:
  * 1-minute bars going back ~1 year (yfinance: 7 days only)
  * 5m / 15m / 30m bars going back ~3 years
  * Daily bars going back decades
  * Institutional-quality adjusted OHLCV (split + dividend adjusted)
  * Sub-day resolution without the IG weekly-allowance constraint

Wire protocol: ``ib_insync`` talking to IB Gateway paper (port 7500 for DUP656969) or IB
Gateway (paper port 4002). The auth pipeline must be running before
this provider is called — connect() is lazy and cached per instance.

Design principles:
  * fetch() is SYNCHRONOUS — BarStore.get() is sync. ib_insync's IB
    class exposes sync wrappers (``connect()`` + ``reqHistoricalData()``
    run the event loop internally when called outside an async context).
  * Connection is CACHED on the instance. Never re-auth per request
    (ib_insync floods the gateway → pacing violations). The singleton
    pattern mirrors the T212 broker caching rule.
  * Long ranges are CHUNKED: IBKR has per-request size limits that
    vary by barSizeSetting. The provider splits automatically and
    concatenates. A brief inter-request pause avoids pacing violations.
  * whatToShow defaults to "TRADES" (real transaction prices) for
    us_equity / us_etf; operators can pass ``what_to_show`` override.
  * useRTH=False: include pre/post market (strategies filter by their
    own session gates). Configurable.

IBKR max history (documented TWS API limits):
    "1 min"    →  ~1 year back from today  (reqHistoricalData chunk ≤30D)
    "5 mins"   →  ~3 years                 (chunk ≤60D)
    "15 mins"  →  ~3.5 years               (chunk ≤60D)
    "30 mins"  →  ~3.5 years               (chunk ≤60D)
    "1 hour"   →  ~3.5 years               (chunk ≤90D)
    "1 day"    →  decades                  (chunk ≤365D)

Pacing rule: IBKR enforces ≤6 identical requests within 10 seconds
and ≤60 historical requests within 10 minutes. A 0.5 s inter-chunk
sleep keeps us well inside the per-minute ceiling for normal batch
fetches.
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd

from ..errors import (
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
)
from .base import Provider, register_provider


_log = logging.getLogger("tradepro.bar_cache.ibkr")


# ── Resolution mapping ────────────────────────────────────────────────
# BarStore canonical resolution → (IBKR barSizeSetting, chunk_days,
#                                  max_history_days | None)
# chunk_days is the maximum single-request span to keep well inside
# IBKR's pacing limits. max_history_days=None means "effectively
# unlimited" (decades of daily bars).
_RESOLUTION_MAP: dict[str, tuple[str, int, int | None]] = {
    "1m":  ("1 min",   30,  365),
    "2m":  ("2 mins",  30,  365),
    "5m":  ("5 mins",  60, 1095),  # 3 years
    "15m": ("15 mins", 60, 1277),  # 3.5 years
    "30m": ("30 mins", 60, 1277),
    "1h":  ("1 hour",  90, 1277),
    "1d":  ("1 day",  365, None),
    "1wk": ("1 week", 365, None),
    "1mo": ("1 month",365, None),
}

# Asset class → IBKR whatToShow. "TRADES" = real transaction prices
# (correct for equities and ETFs). Forex uses MIDPOINT.
_WHAT_TO_SHOW: dict[str, str] = {
    "us_equity":  "TRADES",
    "us_etf":     "TRADES",
    "uk_equity":  "TRADES",
    "eu_equity":  "TRADES",
    "forex":      "MIDPOINT",
    "crypto":     "TRADES",
}
_DEFAULT_WHAT_TO_SHOW = "TRADES"

# Pacing: wait between chunk requests to stay inside the 60-req/10min
# ceiling. 0.6 s ≈ 100 req/min → safely under the limit even if other
# processes share the same client gateway.
_INTER_CHUNK_SLEEP_SECONDS = 0.6


def _lazy_import_ib_insync():
    """Deferred import — keeps the rest of the engine importable on
    machines without ib_insync. Returns the module or None."""
    try:
        import ib_insync  # type: ignore
        return ib_insync
    except ImportError:
        return None


@dataclass
class _IBConnection:
    """Singleton connection wrapper. One per IBKRProvider instance.
    Reconnects automatically if the socket drops between fetches."""

    host: str = "127.0.0.1"
    port: int = 7500       # IB Gateway paper (DUP656969); override via TRADEPRO_IBKR_PORT
    client_id: int = 18    # offset from IBKRBarBus (17) to avoid collision
    timeout_seconds: float = 10.0
    _ib: Any = field(default=None, init=False, repr=False)

    def get(self) -> Any:
        """Return a live IB instance, connecting if needed."""
        ib_insync = _lazy_import_ib_insync()
        if ib_insync is None:
            raise RuntimeError(
                "ib_insync is not installed. Run: uv add ib_insync"
            )
        if self._ib is None:
            self._ib = ib_insync.IB()
        if not self._ib.isConnected():
            _log.info(
                "ibkr_provider: connecting to %s:%s clientId=%s",
                self.host, self.port, self.client_id,
            )
            self._ib.connect(
                self.host, self.port,
                clientId=self.client_id,
                timeout=self.timeout_seconds,
                readonly=True,   # historical data only — no order placement
            )
            _log.info("ibkr_provider: connected (TWS/Gateway)")
        return self._ib

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
            _log.info("ibkr_provider: disconnected")


class IBKRProvider(Provider):
    """IBKR historical-bar provider via ib_insync.

    Construction:
        IBKRProvider()                          # reads env vars
        IBKRProvider(host="127.0.0.1", port=7500)
        IBKRProvider(_fetch_fn=my_mock)         # BDD / unit tests

    Environment variables (all optional):
        TRADEPRO_IBKR_DATA_HOST       (preferred) dedicated data Gateway host
        TRADEPRO_IBKR_DATA_PORT       (preferred) dedicated data Gateway port
        TRADEPRO_IBKR_DATA_CLIENT_ID  (preferred) data-connection clientId
        TRADEPRO_IBKR_HOST        fallback, default 127.0.0.1
        TRADEPRO_IBKR_PORT        fallback, default 7500
        TRADEPRO_IBKR_CLIENT_ID   fallback, default 18
        Set the DATA_* trio to point harvesting at a SEPARATE IBKR user/Gateway
        (isolated session + pacing). Unset → shares the trading Gateway (legacy).

    For tests: pass ``_fetch_fn`` — a callable
        (symbol, bar_size, end_dt, duration_str, what_to_show, use_rth)
        -> list[dict]   (each dict: open/high/low/close/volume/timestamp)
    that bypasses the real ib_insync call entirely.
    """

    name = "ibkr"

    def __init__(
        self,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        timeout_seconds: float = 10.0,
        use_rth: bool = False,
        what_to_show: Optional[str] = None,
        _fetch_fn=None,  # (symbol, bar_size, end_dt, duration, wts, rth) → list[dict]
    ) -> None:
        # Prefer a DEDICATED data/harvest connection (TRADEPRO_IBKR_DATA_*) so
        # bar harvesting can run on a SEPARATE IBKR user + Gateway, isolated
        # from the trading account (own session + per-account pacing budget →
        # heavy historical fetches can't pace-violate or lag live execution).
        # Falls back to the shared TRADEPRO_IBKR_* vars → DEFAULT BEHAVIOUR
        # UNCHANGED until the data Gateway exists; isolating is a one-env flip.
        resolved_host = host or os.environ.get("TRADEPRO_IBKR_DATA_HOST") \
            or os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
        resolved_port = port or int(os.environ.get("TRADEPRO_IBKR_DATA_PORT")
            or os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
        resolved_cid  = client_id or int(os.environ.get("TRADEPRO_IBKR_DATA_CLIENT_ID")
            or os.environ.get("TRADEPRO_IBKR_CLIENT_ID", "18"))

        self._conn = _IBConnection(
            host=resolved_host,
            port=resolved_port,
            client_id=resolved_cid,
            timeout_seconds=timeout_seconds,
        )
        self._use_rth = use_rth
        self._what_to_show_override = what_to_show
        self._fetch_fn = _fetch_fn   # None in production

    # ── Provider ABC ─────────────────────────────────────────────

    def supports_resolution(self, resolution: str) -> bool:
        return resolution in _RESOLUTION_MAP

    def max_history(self, resolution: str) -> timedelta | None:
        if resolution not in _RESOLUTION_MAP:
            return timedelta(0)
        _, _, max_days = _RESOLUTION_MAP[resolution]
        if max_days is None:
            return None  # unlimited
        return timedelta(days=max_days)

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
                message=f"resolution {resolution!r} not supported by IBKR provider",
            )

        bar_size, chunk_days, _ = _RESOLUTION_MAP[resolution]
        what_to_show = (
            self._what_to_show_override
            or _WHAT_TO_SHOW.get(asset_class, _DEFAULT_WHAT_TO_SHOW)
        )

        start_utc = _ensure_utc(start)
        end_utc   = _ensure_utc(end)
        total_span_days = max(1, (end_utc - start_utc).days)

        # Split the range into IBKR-max-chunk-size windows, each
        # expressed as (chunk_end, durationStr) pairs. Iterate from
        # the newest chunk backwards so we can break early on errors.
        chunks = _build_chunks(start_utc, end_utc, chunk_days)
        _log.debug(
            "ibkr_provider: fetching %s %s/%s in %d chunk(s)",
            canonical, resolution, bar_size, len(chunks),
        )

        all_bars: list[dict] = []
        t0 = time.monotonic()
        for i, (chunk_end, duration_str) in enumerate(chunks):
            if i > 0:
                time.sleep(_INTER_CHUNK_SLEEP_SECONDS)
            try:
                bars = self._fetch_chunk(
                    canonical, bar_size, chunk_end, duration_str,
                    what_to_show, self._use_rth,
                )
            except ProviderRateLimitError:
                raise
            except ProviderNetworkError:
                raise
            except ProviderParseError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ProviderNetworkError(
                    provider=self.name,
                    canonical=canonical,
                    message=f"IBKR reqHistoricalData error on chunk {i}: {exc}",
                ) from exc
            all_bars.extend(bars)

        elapsed = time.monotonic() - t0
        if not all_bars:
            return pd.DataFrame(), {
                "provider_version": "ibkr/ib_insync",
                "rows": 0,
                "bar_size": bar_size,
                "total_span_days": total_span_days,
                "elapsed_s": round(elapsed, 2),
            }

        df = self._normalise(all_bars, canonical)
        # Clip to the exact [start, end] range requested — chunks can
        # slightly over-fetch because IBKR aligns to bar boundaries.
        df = df.loc[
            (df.index >= start_utc) & (df.index <= end_utc)
        ]
        df = df[~df.index.duplicated(keep="last")].sort_index()

        metadata = {
            "provider_version": "ibkr/ib_insync",
            "rows": int(len(df)),
            "bar_size": bar_size,
            "what_to_show": what_to_show,
            "use_rth": self._use_rth,
            "total_span_days": total_span_days,
            "chunks": len(chunks),
            "elapsed_s": round(elapsed, 2),
        }
        _log.debug(
            "ibkr_provider: %s %s → %d rows in %.1fs",
            canonical, resolution, len(df), elapsed,
        )
        return df, metadata

    # ── Internal helpers ─────────────────────────────────────────

    def _fetch_chunk(
        self,
        symbol: str,
        bar_size: str,
        end_dt: datetime,
        duration_str: str,
        what_to_show: str,
        use_rth: bool,
    ) -> list[dict]:
        """Call reqHistoricalData for one chunk. Returns a list of dicts
        with keys: timestamp, open, high, low, close, volume."""
        if self._fetch_fn is not None:
            return self._fetch_fn(
                symbol, bar_size, end_dt, duration_str, what_to_show, use_rth,
            )

        ib_insync = _lazy_import_ib_insync()
        if ib_insync is None:
            raise ProviderNetworkError(
                provider=self.name,
                canonical=symbol,
                message="ib_insync not installed",
            )

        try:
            ib = self._conn.get()
        except Exception as exc:  # noqa: BLE001
            raise ProviderNetworkError(
                provider=self.name,
                canonical=symbol,
                message=f"IBKR connection failed: {exc}",
            ) from exc

        contract = ib_insync.Stock(symbol, "SMART", "USD")
        # endDateTime: IBKR format "YYYYMMDD HH:MM:SS timezone" or ""
        # for "now". We always pass the explicit end.
        end_str = end_dt.strftime("%Y%m%d %H:%M:%S UTC")

        try:
            raw_bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=duration_str,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,    # return datetime objects, not epoch ints
                keepUpToDate=False,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # IBKR surfaces pacing violations as plain exceptions with
            # "pacing" or "too many requests" in the message.
            if "pacing" in msg.lower() or "too many" in msg.lower():
                raise ProviderRateLimitError(
                    provider=self.name,
                    canonical=symbol,
                    message=f"IBKR pacing violation: {msg}",
                ) from exc
            raise ProviderNetworkError(
                provider=self.name,
                canonical=symbol,
                message=f"reqHistoricalData failed: {msg}",
            ) from exc

        if not raw_bars:
            return []

        result = []
        for b in raw_bars:
            try:
                # ib_insync BarData attributes: date (datetime), open,
                # high, low, close, volume, barCount, average.
                ts = b.date
                if not isinstance(ts, datetime):
                    # Sometimes returns a date object for daily bars
                    ts = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
                elif ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                result.append({
                    "timestamp": ts,
                    "open":   float(b.open),
                    "high":   float(b.high),
                    "low":    float(b.low),
                    "close":  float(b.close),
                    "volume": int(getattr(b, "volume", 0) or 0),
                })
            except Exception as exc:  # noqa: BLE001
                _log.warning("ibkr_provider: skipping malformed bar %s: %s", b, exc)
        return result

    @staticmethod
    def _normalise(bars: list[dict], canonical: str) -> pd.DataFrame:
        """Convert list-of-dicts to a tz-aware UTC indexed DataFrame
        matching the bar_cache schema (lowercase columns, adj_factor,
        source)."""
        df = pd.DataFrame(bars)
        if df.empty:
            return df
        try:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        except Exception as exc:  # noqa: BLE001
            raise ProviderParseError(
                provider="ibkr",
                canonical=canonical,
                message=f"could not parse IBKR timestamps: {exc}",
            ) from exc
        df = df.set_index("timestamp")
        df.index.name = "timestamp"
        # IBKR returns split+dividend adjusted prices (like yfinance with
        # auto_adjust=True) so adj_factor is implicitly 1.0.
        df["adj_factor"] = 1.0
        df["source"]     = "ibkr"
        return df


# ── Chunk builder ──────────────────────────────────────────────────────

def _build_chunks(
    start_utc: datetime,
    end_utc: datetime,
    chunk_days: int,
) -> list[tuple[datetime, str]]:
    """Split [start_utc, end_utc] into a list of (chunk_end, durationStr)
    pairs suitable for IBKR's reqHistoricalData. Order: oldest→newest so
    the caller can concatenate in chronological order.

    IBKR durationStr format: "N D" (days) or "N W" (weeks) or
    "N M" (months) or "N Y" (years). We always use "N D" for chunks
    ≤365 days and "N Y" for whole-year requests to keep the string
    simple and avoid IBKR's rounding quirks on M/W."""
    span_days = (end_utc - start_utc).total_seconds() / 86400
    n_chunks   = max(1, math.ceil(span_days / chunk_days))
    chunk_td   = timedelta(days=chunk_days)

    chunks: list[tuple[datetime, str]] = []
    cursor = start_utc
    for _ in range(n_chunks):
        chunk_end  = min(cursor + chunk_td, end_utc)
        actual_days = max(1, math.ceil((chunk_end - cursor).total_seconds() / 86400))
        if actual_days >= 365:
            duration_str = f"{actual_days // 365} Y"
        else:
            duration_str = f"{actual_days} D"
        chunks.append((chunk_end, duration_str))
        cursor = chunk_end
        if cursor >= end_utc:
            break
    return chunks


# ── Helpers ────────────────────────────────────────────────────────────

def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


# ── Auto-registration ──────────────────────────────────────────────────
# Register the default singleton at import time so "ibkr" is resolvable
# by the BarStore chain without any operator setup. Fetches raise
# ProviderNetworkError (no TWS running) until the gateway is live —
# the chain falls through cleanly to yfinance / IG. Operators add "ibkr"
# to their data_source_preferences to use it.
register_provider(IBKRProvider())
