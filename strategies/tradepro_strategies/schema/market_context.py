"""VIX / 10Y / S&P-drawdown / active-stress-regime macro snapshot."""
from __future__ import annotations

from typing import Literal

from ._base import TPModel


class MarketContext(TPModel):
    as_of: str | None = None
    vix: float | None = None
    vix_regime: Literal["calm", "normal", "stressed"] | None = None
    tnx: float | None = None
    tnx_change_30d: float | None = None
    tnx_trend: Literal["rising", "falling", "flat"] | None = None
    spy_drawdown_pct: float | None = None
    active_stress_regimes: list[str] = []
    summary: str = ""
