"""Per-symbol price/RSI/SMA snapshot + the rules that produced the
entry verdict. Mirrors `tradepro_strategies.market_state.MarketState`."""
from __future__ import annotations

from typing import Literal

from ._base import TPModel


class DecisionCheck(TPModel):
    """One entry in the price-rules ladder."""
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str


class MarketState(TPModel):
    symbol: str
    as_of: str | None = None
    last_price: float | None = None
    sma_200: float | None = None
    above_sma_200: bool | None = None
    pct_off_52w_high_pct: float | None = None
    drawdown_from_peak_pct: float | None = None
    rsi_14: float | None = None
    momentum_3m_pct: float | None = None
    momentum_12m_pct: float | None = None
    vol_30d_annual_pct: float | None = None
    entry_signal: Literal["BUY", "HOLD", "WAIT", "AVOID"]
    entry_reason: str
    decision_trace: list[DecisionCheck] = []
