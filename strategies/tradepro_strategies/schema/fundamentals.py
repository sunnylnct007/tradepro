"""Fund-level fundamentals (ETF expense ratio, AUM, top holdings)."""
from __future__ import annotations

from ._base import TPModel


class TopHolding(TPModel):
    symbol: str | None = None
    name: str
    weight_pct: float | None = None


class Fundamentals(TPModel):
    symbol: str
    fetched_at: str
    fund_family: str | None = None
    category: str | None = None
    legal_type: str | None = None
    inception_date: str | None = None
    expense_ratio_pct: float | None = None
    aum_usd: float | None = None
    dividend_yield_pct: float | None = None
    distribution_yield_pct: float | None = None
    ytd_return_pct: float | None = None
    three_year_return_pct: float | None = None
    five_year_return_pct: float | None = None
    yield_to_maturity_pct: float | None = None
    duration_years: float | None = None
    top_holdings: list[TopHolding] = []
    sector_weights: dict[str, float] = {}
    summary: str | None = None
    source: str = "yahoo"
