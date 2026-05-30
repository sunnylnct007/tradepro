"""US Equity ETF asset class plugin — first concrete plugin.

Covers liquid US-listed ETFs (SPY, QQQ, IWM, DIA, XLF, etc.). Uses
the NYSE calendar (NASDAQ has the same regular session hours and
the same holidays; treating them identically is fine for ETF/equity
bar caching).

Schema (``us_equity_v1``):
  timestamp     tz-aware UTC, the bar START
  open          float
  high          float
  low           float
  close         float
  volume        int64 (yfinance returns int; we don't lose precision)
  adj_factor    float — 1.0 when source already adjusts (yfinance
                auto_adjust); otherwise the split/div factor at that bar
  source        str  — the provider that supplied the row

Partition strategy: one Parquet per month per resolution. A year of
1-minute SPY is 12 files; the read path only ever touches the months
the request crosses.

Integrity rules:
  * Full US session = 09:30–16:00 ET = 390 1-minute bars.
  * Half-day (around major holidays) = 09:30–13:00 ET = 210 1-minute
    bars. Half-days are exposed via a small hardcoded list — there's
    no live IPC to the NYSE calendar service, but the half-days are
    well-known and don't change often.
  * Holidays produce zero bars. The expected_session_dates() method
    excludes them so the manifest doesn't flag them as missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from ..asset_class import AssetClassPlugin, BarSchema, register_asset_class
from ..errors import ProviderParseError


# NYSE holidays that produce a fully closed session. Maintained by
# hand — calendar additions happen yearly and the list is small.
# Sourced from the official NYSE holiday schedule. Dates here apply
# in 2024 + 2025 (we'll extend forward as years close).
_NYSE_HOLIDAYS = frozenset({
    # 2024
    date(2024, 1, 1),   # New Year's Day
    date(2024, 1, 15),  # MLK Day
    date(2024, 2, 19),  # Presidents Day
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth
    date(2024, 7, 4),   # Independence Day
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 20),  # MLK
    date(2025, 2, 17),
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),   # July 4 is Saturday — observed Friday
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
})

# Half-day session ends (1pm ET close). Lists are small and known.
_NYSE_HALF_DAYS = frozenset({
    date(2024, 7, 3),   # day before Independence Day
    date(2024, 11, 29), # Black Friday
    date(2024, 12, 24), # Christmas Eve
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 11, 27),
    date(2026, 12, 24),
})


_US_EQUITY_SCHEMA = BarSchema(
    schema_version="us_equity_v1",
    column_order=("open", "high", "low", "close", "volume", "adj_factor", "source"),
    required_columns=frozenset({"open", "high", "low", "close", "volume", "source"}),
    nullable_columns=frozenset({"adj_factor"}),
)


@dataclass
class UsEtfPlugin(AssetClassPlugin):
    """US Equity ETF plugin. Stateless; safe to register a single
    instance and reuse across calls."""

    name: str = "us_etf"
    display_name: str = "US Equity ETF"
    schema: BarSchema = _US_EQUITY_SCHEMA

    def supported_resolutions(self) -> tuple[str, ...]:
        return ("1m", "5m", "15m", "30m", "1h", "1d")

    def partition_key(self, ts: datetime) -> str:
        """Year-month string ('2024-12'). Naive year/month: monthly
        partitions across DST changes are fine because the partition
        is a label, not a timezone-sensitive boundary."""
        return f"{ts.year:04d}-{ts.month:02d}"

    def expected_session_dates(
        self, start: datetime, end: datetime,
    ) -> list[date]:
        """Trading sessions between ``start`` and ``end`` inclusive.
        Excludes weekends + holidays. Half-days ARE sessions, just
        with fewer bars (see ``expected_bar_count``)."""
        sd = start.date() if isinstance(start, datetime) else start
        ed = end.date() if isinstance(end, datetime) else end
        out: list[date] = []
        cur = sd
        while cur <= ed:
            if self._is_session(cur):
                out.append(cur)
            cur = cur + timedelta(days=1)
        return out

    def expected_bar_count(self, resolution: str, session_date: date) -> int:
        is_half = session_date in _NYSE_HALF_DAYS
        # Bars per session at each resolution. Half-days are 3.5h
        # (13:00 ET close); full sessions are 6.5h (16:00 ET close).
        bars_per_full = {
            "1m":  390,
            "5m":  78,
            "15m": 26,
            "30m": 13,
            "1h":  7,   # 9:30, 10:30, ..., 15:30 — 7 hourly bins per session
            "1d":  1,
        }.get(resolution)
        if bars_per_full is None:
            raise ValueError(
                f"unsupported resolution {resolution!r} for us_etf"
            )
        if is_half:
            return int(round(bars_per_full * 3.5 / 6.5))
        return bars_per_full

    def validate_frame(self, df: pd.DataFrame) -> None:
        """Check the provider gave us the columns we expect + no
        nulls in required columns. Catches drift early — a yfinance
        release that renames Open→openPrice would fail here, not
        deep inside the cache write."""
        if df.empty:
            return   # empty frame is OK (no data in range)
        cols = set(c.lower() for c in df.columns)
        missing = self.schema.required_columns - cols
        if missing:
            raise ProviderParseError(
                provider="us_etf_validator",
                canonical="<unknown>",
                message=(
                    f"provider returned frame missing required "
                    f"columns: {sorted(missing)} (got {sorted(cols)})"
                ),
            )
        # NaN check on required columns — non-null contract.
        for col in self.schema.required_columns - self.schema.nullable_columns:
            if col in df.columns and df[col].isna().any():
                raise ProviderParseError(
                    provider="us_etf_validator",
                    canonical="<unknown>",
                    message=(
                        f"provider returned NaN in non-nullable column "
                        f"{col!r}"
                    ),
                )

    # ── Internal helpers ────────────────────────────────────────────

    @staticmethod
    def _is_session(d: date) -> bool:
        if d.weekday() >= 5:  # Sat / Sun
            return False
        if d in _NYSE_HOLIDAYS:
            return False
        return True


# Auto-register at import time.
register_asset_class(UsEtfPlugin())
