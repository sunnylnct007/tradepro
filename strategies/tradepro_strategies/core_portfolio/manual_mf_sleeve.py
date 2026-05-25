"""Manual MF Sleeve — Track 2 module ⑦.

Per TradePro_Roadmap_May2026.docx §Track 2 module 7. UK ISA, Indian
mutual fund, and offshore-MF holdings live outside the live-pricing
universe — no Yahoo ticker, no T212 instrument, NAV only published
end-of-day by the AMC. This module is the manual-entry counterpart to
``allocation_view.py`` for those wrappers.

The user enters NAV + date + units + fund-currency cost basis; the
sleeve computes GBP-normalised market value, unrealised gain, regional
and asset-class mix, distribution-income projection, NAV freshness,
and the sleeve-vs-target percent that the Core Portfolio view uses to
flag UNDERWEIGHT / ON_TARGET / OVERWEIGHT.

NAV freshness drives a trust signal — a 31-day-old Indian MF NAV is
likely fine for fund value at month-end but stale for a Tuesday-morning
allocation decision. The view surfaces both per-holding and sleeve-
level freshness so the UI can warn before quoting numbers.

Pure function, no network. FX rates are supplied by the caller (lazy
fetch from currency_helpers happens at the API layer, not here, so
this module stays unit-testable without an internet round-trip).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

_log = logging.getLogger("tradepro.core_portfolio.manual_mf_sleeve")


SleeveStatus = Literal["UNDERWEIGHT", "ON_TARGET", "OVERWEIGHT", "UNKNOWN"]
NavStatus = Literal["FRESH", "STALE", "VERY_STALE", "UNKNOWN"]
FreshnessSummary = Literal["ALL_FRESH", "SOME_STALE", "MANY_STALE", "EMPTY"]


@dataclass
class ManualMFHolding:
    """One manually-entered mutual fund holding. The user maintains
    units + last_nav + last_nav_date; the rest is derived."""
    fund_name: str
    units: float
    last_nav: float
    last_nav_date: str                       # ISO date "YYYY-MM-DD"
    currency: str                            # "GBP" / "INR" / "USD" / "EUR"
    cost_basis_local: float                  # what was paid, in fund currency
    fund_type: str = "equity"                # equity / debt / hybrid / index / international / commodity / ELSS
    region: str | None = None                # "IN" / "UK" / "US" / "OFFSHORE" / "EU"
    isin: str | None = None
    distribution_yield_pct: float | None = None
    monthly_sip_local: float = 0.0


@dataclass
class MFHoldingProjection:
    """Per-holding row on the sleeve report."""
    fund_name: str
    isin: str | None
    fund_type: str
    region: str | None
    currency: str
    units: float
    last_nav: float
    last_nav_date: str
    market_value_local: float
    market_value_gbp: float
    cost_basis_gbp: float
    unrealised_gain_gbp: float
    unrealised_gain_pct: float
    weight_pct: float
    nav_age_days: int | None
    nav_status: NavStatus
    distribution_yield_pct: float | None
    projected_annual_income_gbp: float | None
    monthly_sip_gbp: float

    def to_dict(self) -> dict:
        return {
            "fund_name":                   self.fund_name,
            "isin":                        self.isin,
            "fund_type":                   self.fund_type,
            "region":                      self.region,
            "currency":                    self.currency,
            "units":                       round(self.units, 4),
            "last_nav":                    round(self.last_nav, 4),
            "last_nav_date":               self.last_nav_date,
            "market_value_local":          round(self.market_value_local, 2),
            "market_value_gbp":            round(self.market_value_gbp, 2),
            "cost_basis_gbp":              round(self.cost_basis_gbp, 2),
            "unrealised_gain_gbp":         round(self.unrealised_gain_gbp, 2),
            "unrealised_gain_pct":         round(self.unrealised_gain_pct, 2),
            "weight_pct":                  round(self.weight_pct, 2),
            "nav_age_days":                self.nav_age_days,
            "nav_status":                  self.nav_status,
            "distribution_yield_pct":      (round(self.distribution_yield_pct, 3)
                                              if self.distribution_yield_pct is not None else None),
            "projected_annual_income_gbp": (round(self.projected_annual_income_gbp, 2)
                                              if self.projected_annual_income_gbp is not None else None),
            "monthly_sip_gbp":             round(self.monthly_sip_gbp, 2),
        }


@dataclass
class MFSleeve:
    """Aggregate MF-sleeve report."""
    sleeve_market_value_gbp: float
    sleeve_cost_basis_gbp: float
    sleeve_unrealised_gain_gbp: float
    sleeve_unrealised_gain_pct: float
    weighted_yield_pct: float | None
    projected_annual_income_gbp: float
    planned_monthly_sip_gbp: float
    sleeve_pct_of_portfolio: float | None
    target_sleeve_pct: float
    status: SleeveStatus
    region_mix_pct: dict[str, float]
    type_mix_pct: dict[str, float]
    nav_freshness: FreshnessSummary
    stale_count: int
    holdings: list[MFHoldingProjection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sleeve_market_value_gbp":      round(self.sleeve_market_value_gbp, 2),
            "sleeve_cost_basis_gbp":        round(self.sleeve_cost_basis_gbp, 2),
            "sleeve_unrealised_gain_gbp":   round(self.sleeve_unrealised_gain_gbp, 2),
            "sleeve_unrealised_gain_pct":   round(self.sleeve_unrealised_gain_pct, 2),
            "weighted_yield_pct":           (round(self.weighted_yield_pct, 3)
                                              if self.weighted_yield_pct is not None else None),
            "projected_annual_income_gbp":  round(self.projected_annual_income_gbp, 2),
            "planned_monthly_sip_gbp":      round(self.planned_monthly_sip_gbp, 2),
            "sleeve_pct_of_portfolio":      (round(self.sleeve_pct_of_portfolio, 2)
                                              if self.sleeve_pct_of_portfolio is not None else None),
            "target_sleeve_pct":            round(self.target_sleeve_pct, 2),
            "status":                       self.status,
            "region_mix_pct":               {k: round(v, 2) for k, v in self.region_mix_pct.items()},
            "type_mix_pct":                 {k: round(v, 2) for k, v in self.type_mix_pct.items()},
            "nav_freshness":                self.nav_freshness,
            "stale_count":                  self.stale_count,
            "holdings":                     [h.to_dict() for h in self.holdings],
            "warnings":                     list(self.warnings),
        }


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _nav_age_days(last_nav_date: str, today: date) -> int | None:
    d = _parse_date(last_nav_date)
    if d is None:
        return None
    return (today - d).days


def _nav_status(
    age_days: int | None,
    *,
    stale_threshold: int,
    very_stale_threshold: int,
) -> NavStatus:
    if age_days is None:
        return "UNKNOWN"
    if age_days < 0:
        # Future-dated NAV — treat as fresh, but the warnings list
        # surfaces it separately.
        return "FRESH"
    if age_days >= very_stale_threshold:
        return "VERY_STALE"
    if age_days >= stale_threshold:
        return "STALE"
    return "FRESH"


def _sleeve_status(
    sleeve_pct: float | None,
    target_pct: float,
    *,
    tolerance_pct: float,
) -> SleeveStatus:
    if sleeve_pct is None:
        return "UNKNOWN"
    if sleeve_pct < target_pct - tolerance_pct:
        return "UNDERWEIGHT"
    if sleeve_pct > target_pct + tolerance_pct:
        return "OVERWEIGHT"
    return "ON_TARGET"


def _freshness_summary(stale_count: int, total: int) -> FreshnessSummary:
    if total == 0:
        return "EMPTY"
    if stale_count == 0:
        return "ALL_FRESH"
    if stale_count * 2 >= total:
        # Half or more of the sleeve is stale — caller should warn
        # before quoting a sleeve total.
        return "MANY_STALE"
    return "SOME_STALE"


def compute_mf_sleeve(
    holdings: list[ManualMFHolding],
    *,
    fx_to_gbp: dict[str, float] | None = None,
    total_portfolio_value_gbp: float | None = None,
    target_sleeve_pct: float = 25.0,
    tolerance_pct: float = 2.5,
    today: str | None = None,
    stale_threshold_days: int = 7,
    very_stale_threshold_days: int = 30,
) -> MFSleeve:
    """Build the MF-sleeve report.

    Args:
      holdings: user-entered ManualMFHolding rows.
      fx_to_gbp: currency -> GBP multiplier (e.g. {"INR": 0.0095,
        "USD": 0.79, "GBP": 1.0}). GBP is always treated as 1.0
        whether or not the caller supplies it. Missing currencies add
        a warning and the holding's GBP figures fall back to 0.
      total_portfolio_value_gbp: optional total-portfolio denominator
        for the sleeve-vs-target percent.
      target_sleeve_pct: target MF allocation (% of total portfolio).
        Default 25.
      tolerance_pct: +/- band around target for ON_TARGET. Default 2.5.
      today: ISO date "YYYY-MM-DD" for NAV-age maths. Defaults to today.
      stale_threshold_days: NAV ≥ this many days old reads STALE.
      very_stale_threshold_days: NAV ≥ this reads VERY_STALE.

    Returns:
      MFSleeve dataclass — empty-shape when `holdings` is empty so the
      UI renders a 0-position state without divide-by-zero risk.
    """
    fx = dict(fx_to_gbp or {})
    fx.setdefault("GBP", 1.0)
    today_d = _parse_date(today) if today else date.today()
    warnings: list[str] = []

    if not holdings:
        return MFSleeve(
            sleeve_market_value_gbp=0.0,
            sleeve_cost_basis_gbp=0.0,
            sleeve_unrealised_gain_gbp=0.0,
            sleeve_unrealised_gain_pct=0.0,
            weighted_yield_pct=None,
            projected_annual_income_gbp=0.0,
            planned_monthly_sip_gbp=0.0,
            sleeve_pct_of_portfolio=(
                0.0 if total_portfolio_value_gbp and total_portfolio_value_gbp > 0 else None
            ),
            target_sleeve_pct=target_sleeve_pct,
            status="UNKNOWN" if total_portfolio_value_gbp is None else "UNDERWEIGHT",
            region_mix_pct={},
            type_mix_pct={},
            nav_freshness="EMPTY",
            stale_count=0,
            holdings=[],
            warnings=warnings,
        )

    # First pass — convert to GBP, compute MV, accumulate totals.
    rows: list[MFHoldingProjection] = []
    sleeve_mv_gbp = 0.0
    sleeve_cb_gbp = 0.0
    monthly_sip_gbp = 0.0
    income_gbp = 0.0
    weighted_yield_num = 0.0
    weighted_yield_den = 0.0
    stale_count = 0
    region_value: dict[str, float] = {}
    type_value: dict[str, float] = {}

    for h in holdings:
        cur = (h.currency or "GBP").upper()
        rate = fx.get(cur)
        if rate is None:
            warnings.append(
                f"missing FX rate for {cur} ({h.fund_name}) — sleeve totals exclude this holding"
            )
            rate = 0.0  # excluded from sleeve totals, still appears as a row

        mv_local = h.units * h.last_nav
        mv_gbp = mv_local * rate
        cb_gbp = h.cost_basis_local * rate
        sip_gbp = h.monthly_sip_local * rate

        age = _nav_age_days(h.last_nav_date, today_d)
        if age is not None and age < 0:
            warnings.append(
                f"future-dated NAV for {h.fund_name} ({h.last_nav_date}) — check entry"
            )
        nav_status = _nav_status(
            age,
            stale_threshold=stale_threshold_days,
            very_stale_threshold=very_stale_threshold_days,
        )
        if nav_status in ("STALE", "VERY_STALE"):
            stale_count += 1

        gain_gbp = mv_gbp - cb_gbp
        gain_pct = (gain_gbp / cb_gbp * 100.0) if cb_gbp > 0 else 0.0

        if h.distribution_yield_pct is not None and mv_gbp > 0:
            proj_income_gbp: float | None = mv_gbp * (h.distribution_yield_pct / 100.0)
            weighted_yield_num += h.distribution_yield_pct * mv_gbp
            weighted_yield_den += mv_gbp
            income_gbp += proj_income_gbp
        else:
            proj_income_gbp = None

        sleeve_mv_gbp += mv_gbp
        sleeve_cb_gbp += cb_gbp
        monthly_sip_gbp += sip_gbp

        ft = (h.fund_type or "equity").lower()
        type_value[ft] = type_value.get(ft, 0.0) + mv_gbp
        if h.region:
            rg = h.region.upper()
            region_value[rg] = region_value.get(rg, 0.0) + mv_gbp

        rows.append(MFHoldingProjection(
            fund_name=h.fund_name,
            isin=h.isin,
            fund_type=ft,
            region=h.region.upper() if h.region else None,
            currency=cur,
            units=h.units,
            last_nav=h.last_nav,
            last_nav_date=h.last_nav_date,
            market_value_local=mv_local,
            market_value_gbp=mv_gbp,
            cost_basis_gbp=cb_gbp,
            unrealised_gain_gbp=gain_gbp,
            unrealised_gain_pct=gain_pct,
            weight_pct=0.0,         # filled below
            nav_age_days=age,
            nav_status=nav_status,
            distribution_yield_pct=h.distribution_yield_pct,
            projected_annual_income_gbp=proj_income_gbp,
            monthly_sip_gbp=sip_gbp,
        ))

    # Second pass — assign weights now that the sleeve total is known.
    if sleeve_mv_gbp > 0:
        for r in rows:
            r.weight_pct = r.market_value_gbp / sleeve_mv_gbp * 100.0

    region_mix = (
        {k: v / sleeve_mv_gbp * 100.0 for k, v in region_value.items()}
        if sleeve_mv_gbp > 0 else {}
    )
    type_mix = (
        {k: v / sleeve_mv_gbp * 100.0 for k, v in type_value.items()}
        if sleeve_mv_gbp > 0 else {}
    )

    weighted_yield = (
        weighted_yield_num / weighted_yield_den
        if weighted_yield_den > 0 else None
    )
    gain_gbp = sleeve_mv_gbp - sleeve_cb_gbp
    gain_pct = (gain_gbp / sleeve_cb_gbp * 100.0) if sleeve_cb_gbp > 0 else 0.0

    sleeve_pct = None
    if total_portfolio_value_gbp is not None and total_portfolio_value_gbp > 0:
        sleeve_pct = sleeve_mv_gbp / total_portfolio_value_gbp * 100.0
    status = _sleeve_status(sleeve_pct, target_sleeve_pct, tolerance_pct=tolerance_pct)
    freshness = _freshness_summary(stale_count, len(rows))
    if freshness == "MANY_STALE":
        warnings.append(
            f"{stale_count} of {len(rows)} NAVs are stale — sleeve totals may not "
            "reflect current value; refresh the NAV entries before deciding."
        )

    return MFSleeve(
        sleeve_market_value_gbp=sleeve_mv_gbp,
        sleeve_cost_basis_gbp=sleeve_cb_gbp,
        sleeve_unrealised_gain_gbp=gain_gbp,
        sleeve_unrealised_gain_pct=gain_pct,
        weighted_yield_pct=weighted_yield,
        projected_annual_income_gbp=income_gbp,
        planned_monthly_sip_gbp=monthly_sip_gbp,
        sleeve_pct_of_portfolio=sleeve_pct,
        target_sleeve_pct=target_sleeve_pct,
        status=status,
        region_mix_pct=region_mix,
        type_mix_pct=type_mix,
        nav_freshness=freshness,
        stale_count=stale_count,
        holdings=rows,
        warnings=warnings,
    )
