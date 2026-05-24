"""ETF X-Ray — Track 2 module ⑥.

Per TradePro_Roadmap_May2026.docx §Track 2 module 6. Surfaces what's
actually inside an ETF and how much overlap there is between two
ETFs the user holds together.

The docx motivating example:
  "Your VTI + QQQ holdings are 68% overlapping — consider consolidating"

The overlap signal matters because users routinely buy "VTI + QQQ +
SCHD" believing they're diversifying, when in fact 50%+ of the VTI
weight is the same large-caps QQQ already concentrates in. ETF X-Ray
catches that without requiring portfolio-construction expertise.

Outputs:

  EtfXray   per-ETF summary: top_holdings, sector_weights,
            expense_ratio_pct, drip_yield_pct (current yield), AUM
  EtfOverlapReport   pairwise overlap_pct + per-symbol contribution

Overlap algorithm: weight-intersection (Jaccard-like). For each
symbol present in BOTH ETFs' top-N holdings, the contribution is
min(weight_a, weight_b). Total overlap = sum of contributions.

Limitations (v1):
  - yfinance only exposes top 10 holdings via funds_data; the true
    overlap of broad-market ETFs is much higher than top-10 overlap
    suggests. v2 needs paid ETF holdings data (Polygon / ETF.com) for
    full underlying coverage.
  - Sector weights from funds_data — same top-10 caveat.
  - DRIP projection assumes constant yield + zero price change (floor
    case). Compound math is correct; the assumption is the limit.

Pure function. Accepts TopHolding lists from the existing
fundamentals.py — no new network calls in this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

_log = logging.getLogger("tradepro.core_portfolio.etf_xray")


@dataclass
class EtfXray:
    """Per-ETF summary block — what's inside the wrapper."""
    symbol: str
    name: str | None
    top_holdings: list[dict] = field(default_factory=list)   # [{symbol, name, weight_pct}, ...]
    sector_weights: dict[str, float] = field(default_factory=dict)
    expense_ratio_pct: float | None = None
    aum_usd: float | None = None
    current_yield_pct: float | None = None
    holding_count: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol":            self.symbol,
            "name":              self.name,
            "top_holdings":      list(self.top_holdings),
            "sector_weights":    {k: round(v, 3) for k, v in self.sector_weights.items()},
            "expense_ratio_pct": (round(self.expense_ratio_pct, 3)
                                  if self.expense_ratio_pct is not None else None),
            "aum_usd":           self.aum_usd,
            "current_yield_pct": (round(self.current_yield_pct, 3)
                                  if self.current_yield_pct is not None else None),
            "holding_count":     self.holding_count,
        }


@dataclass
class OverlapContribution:
    """One symbol's contribution to ETF-pair overlap."""
    symbol: str
    name: str | None
    weight_a_pct: float
    weight_b_pct: float
    overlap_weight_pct: float            # min(weight_a, weight_b)

    def to_dict(self) -> dict:
        return {
            "symbol":              self.symbol,
            "name":                self.name,
            "weight_a_pct":        round(self.weight_a_pct, 3),
            "weight_b_pct":        round(self.weight_b_pct, 3),
            "overlap_weight_pct":  round(self.overlap_weight_pct, 3),
        }


@dataclass
class EtfOverlapReport:
    """Pairwise overlap analysis between two ETFs."""
    etf_a: str
    etf_b: str
    overlap_pct: float                   # 0-100 — sum of per-symbol contributions
    shared_count: int
    contributions: list[OverlapContribution] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "etf_a":          self.etf_a,
            "etf_b":          self.etf_b,
            "overlap_pct":    round(self.overlap_pct, 2),
            "shared_count":   self.shared_count,
            "contributions":  [c.to_dict() for c in self.contributions],
            "rationale":      self.rationale,
        }


# ─────────── helpers ───────────


def _normalise_holdings(holdings: Iterable[dict]) -> list[dict]:
    """Coerce an iterable of TopHolding-shaped dicts into a clean list
    of {symbol, name, weight_pct} where symbol is uppercase and
    weight_pct is a float in [0, 100]. Drops entries with no symbol
    OR no weight (can't contribute to overlap without both)."""
    out: list[dict] = []
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        sym = (h.get("symbol") or "").upper().strip()
        if not sym:
            continue
        w = h.get("weight_pct")
        try:
            wf = float(w) if w is not None else None
        except (TypeError, ValueError):
            wf = None
        if wf is None or wf < 0:
            continue
        if wf > 0 and wf <= 1.0:
            # Heuristic: weight expressed as fraction not percent (e.g. 0.05 for 5%).
            wf = wf * 100.0
        out.append({
            "symbol":     sym,
            "name":       h.get("name"),
            "weight_pct": wf,
        })
    return out


def compute_etf_xray(
    symbol: str,
    *,
    name: str | None = None,
    top_holdings: Iterable[dict] | None = None,
    sector_weights: dict[str, float] | None = None,
    expense_ratio_pct: float | None = None,
    aum_usd: float | None = None,
    current_yield_pct: float | None = None,
) -> EtfXray:
    """Build the per-ETF X-Ray summary. Pure transformation — caller
    supplies whatever fields are available (typically from the existing
    Fundamentals dataclass)."""
    norm = _normalise_holdings(top_holdings or [])
    return EtfXray(
        symbol=symbol.upper(),
        name=name,
        top_holdings=norm,
        sector_weights=dict(sector_weights or {}),
        expense_ratio_pct=expense_ratio_pct,
        aum_usd=aum_usd,
        current_yield_pct=current_yield_pct,
        holding_count=len(norm),
    )


def compute_overlap(
    etf_a_symbol: str,
    etf_a_holdings: Iterable[dict],
    etf_b_symbol: str,
    etf_b_holdings: Iterable[dict],
) -> EtfOverlapReport:
    """Compute the weight-overlap between two ETFs' top-N holdings.

    Returns an EtfOverlapReport with per-symbol contributions sorted by
    overlap_weight_pct desc, so the UI shows the largest shared
    positions first.

    Caveat: yfinance only exposes top 10 holdings; true overlap of
    broad-market ETFs is higher than this score suggests. Surface it
    in the rationale so the user doesn't take 30% overlap as "fine,
    they're mostly different" when in reality VTI extends past those
    top-10 into thousands of shared names."""
    a = _normalise_holdings(etf_a_holdings)
    b = _normalise_holdings(etf_b_holdings)
    b_by_symbol = {h["symbol"]: h for h in b}

    contribs: list[OverlapContribution] = []
    total_overlap = 0.0
    for ha in a:
        hb = b_by_symbol.get(ha["symbol"])
        if hb is None:
            continue
        weight_a = ha["weight_pct"]
        weight_b = hb["weight_pct"]
        overlap = min(weight_a, weight_b)
        contribs.append(OverlapContribution(
            symbol=ha["symbol"],
            name=ha.get("name") or hb.get("name"),
            weight_a_pct=weight_a,
            weight_b_pct=weight_b,
            overlap_weight_pct=overlap,
        ))
        total_overlap += overlap

    contribs.sort(key=lambda c: c.overlap_weight_pct, reverse=True)

    if total_overlap >= 50:
        rationale = (
            f"{total_overlap:.0f}% top-10 overlap between {etf_a_symbol.upper()} "
            f"and {etf_b_symbol.upper()} — holding both adds little "
            f"diversification. Consider consolidating into one."
        )
    elif total_overlap >= 30:
        rationale = (
            f"{total_overlap:.0f}% top-10 overlap — meaningful concentration "
            f"in shared large-caps; check whether the second ETF earns its "
            f"slot vs simply doubling-up."
        )
    elif total_overlap >= 10:
        rationale = (
            f"{total_overlap:.0f}% top-10 overlap — modest. Likely "
            f"complementary exposures with some shared mega-caps."
        )
    elif total_overlap > 0:
        rationale = (
            f"{total_overlap:.0f}% top-10 overlap — minimal. ETFs look "
            f"largely independent."
        )
    else:
        rationale = (
            f"0% top-10 overlap between {etf_a_symbol.upper()} and "
            f"{etf_b_symbol.upper()} — no shared names in the top holdings."
        )

    return EtfOverlapReport(
        etf_a=etf_a_symbol.upper(),
        etf_b=etf_b_symbol.upper(),
        overlap_pct=total_overlap,
        shared_count=len(contribs),
        contributions=contribs,
        rationale=rationale,
    )


def project_drip_value(
    *,
    current_value_gbp: float,
    yield_pct: float,
    years: int = 10,
    annual_price_change_pct: float = 0.0,
) -> dict:
    """Project the future value of a position assuming dividends are
    reinvested (DRIP). Floor case = `annual_price_change_pct=0` (income
    only). The math is straight compounding; the assumption is the
    limit — calling this on a real holding still requires the user to
    interpret the "no price change" or whatever growth assumption they
    chose.

    Returns:
      {
        "start_value_gbp":   ...,
        "years":             ...,
        "yield_pct":         ...,
        "annual_price_change_pct": ...,
        "end_value_gbp":     ...,
        "total_dividends_reinvested_gbp": ...,
      }
    """
    try:
        value = float(current_value_gbp)
        y_pct = float(yield_pct)
        n = int(years)
        gr_pct = float(annual_price_change_pct)
    except (TypeError, ValueError):
        return {
            "start_value_gbp": None,
            "years": years,
            "yield_pct": yield_pct,
            "annual_price_change_pct": annual_price_change_pct,
            "end_value_gbp": None,
            "total_dividends_reinvested_gbp": None,
        }
    # Annual compounding rate = (1 + yield) × (1 + price_growth) − 1
    annual_total_return = (1 + y_pct / 100.0) * (1 + gr_pct / 100.0) - 1.0
    end_value = value * ((1 + annual_total_return) ** n)
    # Dividend portion only = end-value minus price-only growth path
    price_only_end = value * ((1 + gr_pct / 100.0) ** n)
    div_contribution = end_value - price_only_end
    return {
        "start_value_gbp":              round(value, 2),
        "years":                        n,
        "yield_pct":                    round(y_pct, 3),
        "annual_price_change_pct":      round(gr_pct, 3),
        "end_value_gbp":                round(end_value, 2),
        "total_dividends_reinvested_gbp": round(div_contribution, 2),
    }
