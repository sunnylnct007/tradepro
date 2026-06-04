"""Crypto asset class plugin.

Covers spot crypto pairs (BTC-USD, ETH-USD, SOL-USD, ...). yfinance
accepts these as ``BTC-USD``; Binance has its own naming
(``BTCUSDT``). Two material differences from equity / FX:

  * **24/7 calendar.** Crypto exchanges never close. Every calendar
    day is a session, weekends included. No holidays. A full 1m
    session is 1440 bars; the manifest can flag any actual outage.

  * **Volume IS meaningful.** Unlike FX, crypto exchanges publish
    real volume from their order books. Schema keeps volume in the
    required-column set (same shape as equity).

Today's TradePro doesn't trade crypto live (per operator note —
"not trading in crypto at the moment"). This plugin ships so the
infrastructure is in place when / if crypto strategies join the
universe — the BinanceProvider.cs stub on the .NET side already
exists; consumer-side wiring is the only missing piece.

Schema mirrors ``us_equity_v1`` exactly because Binance OHLC returns
match the equity shape. Versioned as ``crypto_v1`` so a future change
(e.g. funding rate column for perpetuals) can bump without forcing
equity to follow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from ..asset_class import AssetClassPlugin, BarSchema, register_asset_class
from ..errors import ProviderParseError


_CRYPTO_SCHEMA = BarSchema(
    schema_version="crypto_v1",
    column_order=("open", "high", "low", "close", "volume", "adj_factor", "source"),
    required_columns=frozenset({"open", "high", "low", "close", "volume", "source"}),
    nullable_columns=frozenset({"adj_factor"}),
)


@dataclass
class CryptoPlugin(AssetClassPlugin):
    """Spot crypto plugin. 24/7 calendar; equity-style OHLCV schema."""

    name: str = "crypto"
    display_name: str = "Crypto (spot)"
    schema: BarSchema = _CRYPTO_SCHEMA

    def supported_resolutions(self) -> tuple[str, ...]:
        # Same ladder as equity for symmetry. Binance natively
        # supports a much wider set (1s, 3m, etc.) — extend the
        # tuple when a consumer needs the extra granularity.
        return ("1m", "5m", "15m", "30m", "1h", "1d")

    def partition_key(self, ts: datetime) -> str:
        return f"{ts.year:04d}-{ts.month:02d}"

    def expected_session_dates(
        self, start: datetime, end: datetime,
    ) -> list[date]:
        """Every calendar day is a session — 24/7 market, no
        weekend skip."""
        sd = start.date() if isinstance(start, datetime) else start
        ed = end.date() if isinstance(end, datetime) else end
        out: list[date] = []
        cur = sd
        while cur <= ed:
            out.append(cur)
            cur = cur + timedelta(days=1)
        return out

    def expected_bar_count(self, resolution: str, session_date: date) -> int:
        bars_per_24h = {
            "1m":  1440,
            "5m":  288,
            "15m": 96,
            "30m": 48,
            "1h":  24,
            "1d":  1,
        }.get(resolution)
        if bars_per_24h is None:
            raise ValueError(
                f"unsupported resolution {resolution!r} for crypto"
            )
        return bars_per_24h

    def validate_frame(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        cols = set(c.lower() for c in df.columns)
        missing = self.schema.required_columns - cols
        if missing:
            raise ProviderParseError(
                provider="crypto_validator",
                canonical="<unknown>",
                message=(
                    f"crypto provider returned frame missing required "
                    f"columns: {sorted(missing)} (got {sorted(cols)})"
                ),
            )
        for col in self.schema.required_columns - self.schema.nullable_columns:
            if col in df.columns and df[col].isna().any():
                raise ProviderParseError(
                    provider="crypto_validator",
                    canonical="<unknown>",
                    message=(
                        f"crypto provider returned NaN in non-nullable "
                        f"column {col!r}"
                    ),
                )


register_asset_class(CryptoPlugin())

__all__ = ["CryptoPlugin"]
