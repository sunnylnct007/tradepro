"""Per-regime backtest result + the catalog spec."""
from __future__ import annotations

from typing import Literal

from ._base import TPModel


class RegimeRow(TPModel):
    """How a strategy performed inside one named historical window."""
    key: str
    name: str
    kind: Literal["crash", "drawdown", "recovery"]
    bars: int = 0
    return_pct: float | None = None
    max_drawdown_pct: float | None = None


class RegimeSpec(TPModel):
    """Catalog entry — describes the window itself, not a result."""
    key: str
    name: str
    kind: Literal["crash", "drawdown", "recovery"]
    start: str
    end: str
    description: str
