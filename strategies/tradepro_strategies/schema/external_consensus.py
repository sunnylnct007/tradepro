"""Wall Street analyst consensus snapshot (Yahoo)."""
from __future__ import annotations

from ._base import TPModel


class ExternalConsensus(TPModel):
    symbol: str
    fetched_at: str
    rating_key: str | None = None        # raw recommendationKey
    rating_label: str | None = None      # normalised display label
    rating_mean: float | None = None     # 1.0 (strong buy) to 5.0 (strong sell)
    n_analysts: int | None = None
    target_mean: float | None = None
    target_median: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    current_price: float | None = None
    target_vs_current_pct: float | None = None
    source: str = "yahoo"
