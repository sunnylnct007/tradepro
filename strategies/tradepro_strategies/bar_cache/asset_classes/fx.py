"""FX (foreign-exchange) asset class plugin.

Covers spot FX pairs (EUR/USD, GBP/USD, USD/JPY, ...). yfinance
accepts these as ``EURUSD=X`` and IG as the corresponding epic
(``CS.D.EURUSD.MINI.IP``). Two material differences from the
equity plugins:

  * **24/5 calendar.** FX is continuous from Sunday 22:00 UTC (when
    Sydney opens) to Friday 22:00 UTC (when New York closes). No
    intraday breaks, no half-days, no nationally observed holidays.
    A handful of major-cross holidays (Christmas Day, New Year's
    Day) do see liquidity collapse but the market is technically
    open, so we treat them as sessions and let the bar count flag
    any actual gap.

  * **Volume is not meaningful.** FX is decentralised; the "volume"
    a single broker reports is its own flow, not market-wide. Both
    yfinance and IG report 0 (or omit it). The schema makes volume
    nullable so a missing column doesn't trip the validator.

Bar counts assume 24-hour sessions Mon–Fri (so 1440 1m bars per
session, 24 hourly bars per session, etc.). Sunday and Friday are
partial sessions and surface as their own row counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from ..asset_class import AssetClassPlugin, BarSchema, register_asset_class
from ..errors import ProviderParseError


# Major FX-affecting holidays where liquidity essentially evaporates
# even though the market is technically "open". We don't exclude them
# from the session list (the broker still streams bars) — they're
# documented here so a future audit can correlate gaps.
_FX_LOW_LIQUIDITY = frozenset({
    date(2024, 12, 25), date(2024, 1, 1),
    date(2025, 12, 25), date(2025, 1, 1),
    date(2026, 12, 25), date(2026, 1, 1),
})


# FX schema — same OHLC as equity but volume nullable. Source still
# required so we can tell yfinance bars from IG bars when the chain
# fell back.
_FX_SCHEMA = BarSchema(
    schema_version="fx_v1",
    column_order=("open", "high", "low", "close", "volume", "adj_factor", "source"),
    required_columns=frozenset({"open", "high", "low", "close", "source"}),
    nullable_columns=frozenset({"adj_factor", "volume"}),
)


@dataclass
class FxPlugin(AssetClassPlugin):
    """Spot FX plugin. 24/5 calendar; OHLC schema with nullable
    volume (FX has no centralised volume)."""

    name: str = "fx_spot"
    display_name: str = "FX (spot)"
    schema: BarSchema = _FX_SCHEMA

    def supported_resolutions(self) -> tuple[str, ...]:
        # FX brokers commonly expose down to 1m via IG / Oanda. We
        # keep the same ladder as equity for symmetry — that way a
        # consumer iterating asset classes doesn't have to special-
        # case FX in resolution mapping logic.
        return ("1m", "5m", "15m", "30m", "1h", "1d")

    def partition_key(self, ts: datetime) -> str:
        return f"{ts.year:04d}-{ts.month:02d}"

    def expected_session_dates(
        self, start: datetime, end: datetime,
    ) -> list[date]:
        """Mon–Fri are full sessions. Saturday is closed. Sunday is
        a partial session (markets open at 22:00 UTC). We treat
        Sundays as sessions for partition purposes — the bar count
        on a Sunday is small but non-zero, and excluding them would
        leave a manifest gap each week."""
        sd = start.date() if isinstance(start, datetime) else start
        ed = end.date() if isinstance(end, datetime) else end
        out: list[date] = []
        cur = sd
        while cur <= ed:
            # weekday(): 0=Mon, 6=Sun. Skip Saturday only.
            if cur.weekday() != 5:
                out.append(cur)
            cur = cur + timedelta(days=1)
        return out

    def expected_bar_count(self, resolution: str, session_date: date) -> int:
        """Mon–Thu = full 24h sessions. Sunday = ~2 hours (22:00 UTC
        open). Friday = ~22 hours (closes 22:00 UTC). The numbers are
        heuristic — actual gaps land in the manifest, this is the
        denominator for `coverage_complete`."""
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
                f"unsupported resolution {resolution!r} for fx"
            )
        dow = session_date.weekday()
        if dow == 6:  # Sunday — ~2h window
            return max(1, int(round(bars_per_24h * 2 / 24)))
        if dow == 4:  # Friday — closes 22:00 UTC ≈ 22h
            return int(round(bars_per_24h * 22 / 24))
        return bars_per_24h  # Mon / Tue / Wed / Thu

    def validate_frame(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        cols = set(c.lower() for c in df.columns)
        missing = self.schema.required_columns - cols
        if missing:
            raise ProviderParseError(
                provider="fx_validator",
                canonical="<unknown>",
                message=(
                    f"FX provider returned frame missing required "
                    f"columns: {sorted(missing)} (got {sorted(cols)})"
                ),
            )
        # Non-nullable columns can't carry NaN. Volume IS in nullable
        # set for FX so this happily passes on a no-volume frame.
        for col in self.schema.required_columns - self.schema.nullable_columns:
            if col in df.columns and df[col].isna().any():
                raise ProviderParseError(
                    provider="fx_validator",
                    canonical="<unknown>",
                    message=(
                        f"FX provider returned NaN in non-nullable "
                        f"column {col!r}"
                    ),
                )


register_asset_class(FxPlugin())

__all__ = ["FxPlugin"]
