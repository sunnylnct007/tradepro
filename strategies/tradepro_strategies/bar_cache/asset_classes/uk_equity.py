"""UK Equity asset class plugin.

Covers London Stock Exchange listings (FTSE 100 / 250 / AIM names) —
``BARC.L``, ``VOD.L``, ``HSBA.L``, etc. yfinance accepts the ``.L``
suffix natively. The LSE calendar differs from the NYSE in three
ways that the cache layer must honour:

  * **Session hours.** LSE regular session is 08:00–16:30 London time
    (8.5 hours), vs NYSE 09:30–16:00 ET (6.5 hours). 1m bars per
    session: 510 (LSE) vs 390 (NYSE).

  * **Half-day closes.** LSE half-days close at 12:30 London time
    (4.5 hours). The list is short — Christmas Eve, New Year's Eve —
    and matches the ICAP / LSE published calendar.

  * **Holidays.** UK bank holidays (early May, spring/summer bank
    holidays, Boxing Day) instead of US federal holidays.

The bar schema is identical to the US equity plugin (OHLCV +
adj_factor + source), because yfinance returns the same shape for
LSE tickers as for NYSE tickers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from ..asset_class import AssetClassPlugin, register_asset_class
from ..errors import ProviderParseError
from .us_etf import _US_EQUITY_SCHEMA


# LSE-recognised holidays where the exchange is fully closed. Sourced
# from the LSE's official annual holiday calendar. Maintained yearly
# alongside the NYSE list in us_etf.py.
_LSE_HOLIDAYS = frozenset({
    # 2024
    date(2024, 1, 1),    # New Year's Day
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 1),    # Easter Monday
    date(2024, 5, 6),    # Early May bank holiday
    date(2024, 5, 27),   # Spring bank holiday
    date(2024, 8, 26),   # Summer bank holiday
    date(2024, 12, 25),  # Christmas Day
    date(2024, 12, 26),  # Boxing Day
    # 2025
    date(2025, 1, 1),
    date(2025, 4, 18),   # Good Friday
    date(2025, 4, 21),   # Easter Monday
    date(2025, 5, 5),
    date(2025, 5, 26),
    date(2025, 8, 25),
    date(2025, 12, 25),
    date(2025, 12, 26),
    # 2026
    date(2026, 1, 1),
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 6),    # Easter Monday
    date(2026, 5, 4),
    date(2026, 5, 25),
    date(2026, 8, 31),
    date(2026, 12, 25),
    date(2026, 12, 28),  # Boxing Day observed (26 is Saturday)
})

# LSE half-day closes (12:30 London time). Christmas Eve + New Year's
# Eve when they fall on weekdays. Short list, manually maintained.
_LSE_HALF_DAYS = frozenset({
    date(2024, 12, 24),
    date(2024, 12, 31),
    date(2025, 12, 24),
    date(2025, 12, 31),
    date(2026, 12, 24),
    date(2026, 12, 31),
})


@dataclass
class UkEquityPlugin(AssetClassPlugin):
    """LSE-listed equity plugin. Stateless; safe to register once."""

    name: str = "uk_equity"
    display_name: str = "UK Equity (LSE)"
    schema = _US_EQUITY_SCHEMA  # same OHLCV shape

    def supported_resolutions(self) -> tuple[str, ...]:
        return ("1m", "5m", "15m", "30m", "1h", "1d")

    def partition_key(self, ts: datetime) -> str:
        # Year-month, like us_etf — the label is a string, not a
        # tz-sensitive boundary, so DST shifts don't bite.
        return f"{ts.year:04d}-{ts.month:02d}"

    def expected_session_dates(
        self, start: datetime, end: datetime,
    ) -> list[date]:
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
        """LSE bars per session at each resolution. Full session =
        510 minutes (08:00–16:30); half-day = 270 minutes
        (08:00–12:30)."""
        is_half = session_date in _LSE_HALF_DAYS
        bars_per_full = {
            "1m":  510,
            "5m":  102,
            "15m": 34,
            "30m": 17,
            # 1h: 9 hourly bins (8, 9, 10, 11, 12, 13, 14, 15, 16) —
            # the 16:30 close is a partial bin we still count.
            "1h":  9,
            "1d":  1,
        }.get(resolution)
        if bars_per_full is None:
            raise ValueError(
                f"unsupported resolution {resolution!r} for uk_equity"
            )
        if is_half:
            # 4.5 / 8.5 ratio for the half-day fraction.
            return max(1, int(round(bars_per_full * 4.5 / 8.5)))
        return bars_per_full

    def validate_frame(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        cols = set(c.lower() for c in df.columns)
        missing = self.schema.required_columns - cols
        if missing:
            raise ProviderParseError(
                provider="uk_equity_validator",
                canonical="<unknown>",
                message=(
                    f"provider returned frame missing required "
                    f"columns: {sorted(missing)} (got {sorted(cols)})"
                ),
            )
        for col in self.schema.required_columns - self.schema.nullable_columns:
            if col in df.columns and df[col].isna().any():
                raise ProviderParseError(
                    provider="uk_equity_validator",
                    canonical="<unknown>",
                    message=(
                        f"provider returned NaN in non-nullable column "
                        f"{col!r}"
                    ),
                )

    @staticmethod
    def _is_session(d: date) -> bool:
        if d.weekday() >= 5:
            return False
        if d in _LSE_HOLIDAYS:
            return False
        return True


register_asset_class(UkEquityPlugin())

__all__ = ["UkEquityPlugin"]
