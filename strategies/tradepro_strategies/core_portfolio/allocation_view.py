"""Allocation View (Core Sleeve) — Track 2 module ④.

Per TradePro_Roadmap_May2026.docx §Track 2 module 4. The ring-fenced
~25% sleeve tracker — separates Compounder positions from swing /
intraday positions so the user can see "am I overweight or
underweight my core sleeve target?" at a glance.

A Core Sleeve holding is one tagged for the compounder workflow
(quality scorecard / valuation / dividend) — the user maintains the
membership list. The Allocation View aggregates:

  - Total core-sleeve invested value (£)
  - Current core-sleeve market value (£)
  - Unrealised gain/loss (absolute + percent)
  - Weighted portfolio yield (sum(weight × yield_pct))
  - Projected annual dividend income (sum of per-position projections)
  - Sleeve vs portfolio percent: actual vs target (default 25%)
  - Per-position breakdown: weight, contribution to yield, dividend

DCA scheduler hooks: each holding can carry a planned_monthly_gbp
amount; the view summarises monthly inflow + projects 12-month value
assuming current yield + zero price change (the floor income case).

Pure function. Accepts a list of CoreSleevePosition dicts and an
optional total_portfolio_value (for sleeve-percent vs target). No
network calls — fits cleanly into a per-request render path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

_log = logging.getLogger("tradepro.core_portfolio.allocation_view")


SleeveStatus = Literal["UNDERWEIGHT", "ON_TARGET", "OVERWEIGHT", "UNKNOWN"]


@dataclass
class CoreSleevePosition:
    """One holding inside the core sleeve. The user supplies these;
    quantity / cost_basis are tracked via T212 + manual entry."""
    symbol: str
    quantity: float
    cost_basis_gbp: float          # what was paid (£)
    current_price_gbp: float       # latest price (£, FX-normalised by caller)
    yield_pct: float | None = None
    planned_monthly_gbp: float = 0.0


@dataclass
class PositionBreakdown:
    """Per-position projection on the allocation view."""
    symbol: str
    weight_pct: float                       # % of sleeve value
    market_value_gbp: float
    cost_basis_gbp: float
    unrealised_gain_gbp: float
    unrealised_gain_pct: float
    yield_pct: float | None
    projected_annual_income_gbp: float | None
    planned_monthly_gbp: float

    def to_dict(self) -> dict:
        return {
            "symbol":                       self.symbol,
            "weight_pct":                   round(self.weight_pct, 2),
            "market_value_gbp":             round(self.market_value_gbp, 2),
            "cost_basis_gbp":               round(self.cost_basis_gbp, 2),
            "unrealised_gain_gbp":          round(self.unrealised_gain_gbp, 2),
            "unrealised_gain_pct":          round(self.unrealised_gain_pct, 2),
            "yield_pct":                    (round(self.yield_pct, 3)
                                              if self.yield_pct is not None else None),
            "projected_annual_income_gbp":  (round(self.projected_annual_income_gbp, 2)
                                              if self.projected_annual_income_gbp is not None else None),
            "planned_monthly_gbp":          round(self.planned_monthly_gbp, 2),
        }


@dataclass
class AllocationView:
    """Aggregate view of the core sleeve."""
    sleeve_market_value_gbp: float
    sleeve_cost_basis_gbp: float
    sleeve_unrealised_gain_gbp: float
    sleeve_unrealised_gain_pct: float
    weighted_yield_pct: float | None
    projected_annual_income_gbp: float
    planned_monthly_inflow_gbp: float
    sleeve_pct_of_portfolio: float | None
    target_sleeve_pct: float
    status: SleeveStatus
    positions: list[PositionBreakdown] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sleeve_market_value_gbp":      round(self.sleeve_market_value_gbp, 2),
            "sleeve_cost_basis_gbp":        round(self.sleeve_cost_basis_gbp, 2),
            "sleeve_unrealised_gain_gbp":   round(self.sleeve_unrealised_gain_gbp, 2),
            "sleeve_unrealised_gain_pct":   round(self.sleeve_unrealised_gain_pct, 2),
            "weighted_yield_pct":           (round(self.weighted_yield_pct, 3)
                                              if self.weighted_yield_pct is not None else None),
            "projected_annual_income_gbp":  round(self.projected_annual_income_gbp, 2),
            "planned_monthly_inflow_gbp":   round(self.planned_monthly_inflow_gbp, 2),
            "sleeve_pct_of_portfolio":      (round(self.sleeve_pct_of_portfolio, 2)
                                              if self.sleeve_pct_of_portfolio is not None else None),
            "target_sleeve_pct":            round(self.target_sleeve_pct, 2),
            "status":                       self.status,
            "positions":                    [p.to_dict() for p in self.positions],
        }


def _sleeve_status(
    sleeve_pct: float | None,
    target_pct: float,
    *,
    tolerance_pct: float = 2.5,
) -> SleeveStatus:
    """Bucket the actual sleeve percent vs target. Default tolerance
    ±2.5% — so a 25% target with actual in [22.5, 27.5] reads
    ON_TARGET. Outside that band, UNDER / OVER."""
    if sleeve_pct is None:
        return "UNKNOWN"
    if sleeve_pct < target_pct - tolerance_pct:
        return "UNDERWEIGHT"
    if sleeve_pct > target_pct + tolerance_pct:
        return "OVERWEIGHT"
    return "ON_TARGET"


def compute_allocation_view(
    positions: list[CoreSleevePosition],
    *,
    total_portfolio_value_gbp: float | None = None,
    target_sleeve_pct: float = 25.0,
    tolerance_pct: float = 2.5,
) -> AllocationView:
    """Build the allocation view. Returns an empty AllocationView when
    `positions` is empty (no zero-divide risk; UI renders the empty
    state)."""
    if not positions:
        return AllocationView(
            sleeve_market_value_gbp=0.0,
            sleeve_cost_basis_gbp=0.0,
            sleeve_unrealised_gain_gbp=0.0,
            sleeve_unrealised_gain_pct=0.0,
            weighted_yield_pct=None,
            projected_annual_income_gbp=0.0,
            planned_monthly_inflow_gbp=0.0,
            sleeve_pct_of_portfolio=(
                0.0 if total_portfolio_value_gbp and total_portfolio_value_gbp > 0 else None
            ),
            target_sleeve_pct=target_sleeve_pct,
            status="UNKNOWN" if total_portfolio_value_gbp is None else "UNDERWEIGHT",
            positions=[],
        )

    breakdowns: list[PositionBreakdown] = []
    sleeve_mv = 0.0
    sleeve_cb = 0.0
    income = 0.0
    monthly_inflow = 0.0
    # First pass — totals so we can compute weights.
    for pos in positions:
        mv = pos.quantity * pos.current_price_gbp
        sleeve_mv += mv
        sleeve_cb += pos.cost_basis_gbp
        monthly_inflow += pos.planned_monthly_gbp

    # Second pass — per-position breakdowns + weighted yield.
    weighted_yield_num = 0.0
    weighted_yield_den = 0.0
    for pos in positions:
        mv = pos.quantity * pos.current_price_gbp
        weight = (mv / sleeve_mv * 100.0) if sleeve_mv > 0 else 0.0
        gain_abs = mv - pos.cost_basis_gbp
        gain_pct = (gain_abs / pos.cost_basis_gbp * 100.0) if pos.cost_basis_gbp > 0 else 0.0
        if pos.yield_pct is not None:
            proj_income = mv * (pos.yield_pct / 100.0)
            weighted_yield_num += pos.yield_pct * mv
            weighted_yield_den += mv
        else:
            proj_income = None
        income += proj_income or 0.0
        breakdowns.append(PositionBreakdown(
            symbol=pos.symbol.upper(),
            weight_pct=weight,
            market_value_gbp=mv,
            cost_basis_gbp=pos.cost_basis_gbp,
            unrealised_gain_gbp=gain_abs,
            unrealised_gain_pct=gain_pct,
            yield_pct=pos.yield_pct,
            projected_annual_income_gbp=proj_income,
            planned_monthly_gbp=pos.planned_monthly_gbp,
        ))

    weighted_yield = (
        weighted_yield_num / weighted_yield_den
        if weighted_yield_den > 0 else None
    )
    gain_abs = sleeve_mv - sleeve_cb
    gain_pct = (gain_abs / sleeve_cb * 100.0) if sleeve_cb > 0 else 0.0

    sleeve_pct = None
    if total_portfolio_value_gbp is not None and total_portfolio_value_gbp > 0:
        sleeve_pct = (sleeve_mv / total_portfolio_value_gbp) * 100.0
    status = _sleeve_status(sleeve_pct, target_sleeve_pct, tolerance_pct=tolerance_pct)

    return AllocationView(
        sleeve_market_value_gbp=sleeve_mv,
        sleeve_cost_basis_gbp=sleeve_cb,
        sleeve_unrealised_gain_gbp=gain_abs,
        sleeve_unrealised_gain_pct=gain_pct,
        weighted_yield_pct=weighted_yield,
        projected_annual_income_gbp=income,
        planned_monthly_inflow_gbp=monthly_inflow,
        sleeve_pct_of_portfolio=sleeve_pct,
        target_sleeve_pct=target_sleeve_pct,
        status=status,
        positions=breakdowns,
    )
