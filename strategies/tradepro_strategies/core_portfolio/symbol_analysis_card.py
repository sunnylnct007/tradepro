"""Symbol Analysis Card — TradePro's unified technical + fundamental view.

The defining premise of TradePro (user, 2026-05-24):

  "the whole idea of tradepro is to have a platform to show technical
   and fundamental analysis"

A trader looking at AAPL or HDFCBANK.NS needs BOTH lenses on the same
screen, not two disjoint tabs. The Symbol Analysis Card orchestrates
every existing analysis surface in the project and returns one
composite block.

What goes on the card:

  TECHNICAL (from compare.py row when supplied; otherwise omitted)
    bucket, bucket_reason       — BUY / WAIT / AVOID + why
    conviction                  — HIGH / MEDIUM / LOW / INVALID (BUG-001 cap)
    coherence                   — bucket ↔ entry_signal agreement (BUG-002)
    horizon, strategy_type      — 3-axis classification per IMPROVEMENT_SUGGESTIONS §1
    exit                        — stop / target / RR / time_exit (SIGNAL_CARD §3)
    sizing                      — shares + notional in USD + GBP
    ibkr_order_instructions     — what to type into IBKR bracket order
    rr_gate                     — passed/failed against the 2:1 floor
    earnings_suppressed         — true when earnings within 7d
    news_context                — sentiment + headlines + suppress flag
    combined_verdict            — technical × catalyst × analyst fusion (Phase 17.5)
    catalysts                   — dated events extracted from headlines

  FUNDAMENTAL (computed here from yfinance fetches)
    quality_snapshot            — Track 2 ① — ★ rating from current ratios
    valuation                   — Track 2 ② — ATTRACTIVE / FAIR / STRETCHED
    dividend                    — Track 2 ③ — yield / CAGR / verdict
    entry_timing                — Track 2 ⑤ — ACCUMULATE / WATCH / NEUTRAL
    long_term_grade             — fundamental_analysis.py — A-F multi-year grade
    long_term_trends            — revenue CAGR, margin direction, ROE, FCF conv
    sector_template             — banking / technology / pharma / ... + yfinance_gaps

  META
    primary_horizon_recommendation — single-token answer to "is this a short /
                                     medium / long-term candidate?" derived from
                                     the strongest verdict in each layer

Single MCP entry point: ``get_symbol_analysis(symbol)``.

Pure orchestration. Each underlying module is responsible for its own
network calls and graceful degradation; this layer just composes their
outputs and surfaces a unified shape.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .dividend_dashboard import compute_dividend_dashboard
from .entry_timing import compute_entry_timing
from .quality_scorecard import compute_quality_scorecard
from .valuation_layer import compute_valuation_layer

_log = logging.getLogger("tradepro.core_portfolio.symbol_analysis_card")


HorizonRecommendation = Literal[
    "LONG_TERM_HOLD",     # quality + valuation + dividend support multi-year compounding
    "MEDIUM_TERM_ADD",    # ACCUMULATE entry signal or trend BUY with HIGH conviction
    "SHORT_TERM_TRADE",   # technical BUY/WAIT bucket with conviction-driven entry
    "AVOID",              # technical AVOID OR quality F + valuation STRETCHED
    "WATCH",              # mixed signals — no clear answer
    "INSUFFICIENT",       # not enough data either side
]


@dataclass
class SymbolAnalysisCard:
    """One card per symbol, fusing both analytical lenses."""
    symbol: str
    fetched_at: str
    technical: dict | None              # block from compare.py row, or None
    fundamental: dict                   # composed below
    primary_horizon_recommendation: HorizonRecommendation
    rationale: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol":                          self.symbol,
            "fetched_at":                      self.fetched_at,
            "technical":                       self.technical,
            "fundamental":                     self.fundamental,
            "primary_horizon_recommendation":  self.primary_horizon_recommendation,
            "rationale":                       self.rationale,
            "warnings":                        list(self.warnings),
        }


def _extract_technical(row: dict | None) -> dict | None:
    """Pull the technical-analysis blocks off a compare-row dict. None
    when no row supplied. Tolerant of missing fields — fresh installs
    or earlier-version rows don't carry every block yet."""
    if not row:
        return None
    return {
        "bucket":                    row.get("bucket"),
        "bucket_reason":             row.get("bucket_reason"),
        "conviction":                row.get("conviction"),
        "conviction_reason":         row.get("conviction_reason"),
        "coherence":                 row.get("coherence"),
        "horizon":                   row.get("horizon"),
        "strategy_type":             row.get("strategy_type"),
        "factor_type":               row.get("factor_type"),
        "exit":                      row.get("exit"),
        "rr_gate":                   row.get("rr_gate"),
        "sizing":                    row.get("sizing"),
        "ibkr_order_instructions":   row.get("ibkr_order_instructions"),
        "earnings_suppressed":       row.get("earnings_suppressed"),
        "earnings_proximity_days":   row.get("earnings_proximity_days"),
        "news_context":              row.get("news_context"),
        "combined_verdict":          row.get("combined_verdict"),
        "catalysts":                 row.get("catalysts"),
    }


def _compose_fundamental(
    symbol: str,
    *,
    info: dict | None,
    long_term_result: dict | None,
    drawdown_pct: float | None,
    market_state: dict | None,
    warnings: list[str],
) -> dict:
    """Build the fundamental block from existing Track 2 helpers +
    the other dev's fundamental_analysis output."""
    quality = compute_quality_scorecard(symbol, info=info)
    valuation = compute_valuation_layer(symbol, info=info)
    dividend = compute_dividend_dashboard(symbol, info=info, dividends_series=None)

    long_term_grade = None
    long_term_trends = None
    sector_template = None
    if long_term_result:
        quality_block = long_term_result.get("quality") or {}
        long_term_grade = {
            "grade":     quality_block.get("grade"),
            "score":     quality_block.get("score"),
            "positives": quality_block.get("positives", []),
            "negatives": quality_block.get("negatives", []),
        }
        long_term_trends = long_term_result.get("trends")
        sector_template = long_term_result.get("template")
        for w in long_term_result.get("warnings") or []:
            warnings.append(w)

    timing = compute_entry_timing(
        symbol,
        quality=quality,
        valuation=valuation,
        drawdown_pct=drawdown_pct,
        market_state=market_state,
    )

    return {
        "quality_snapshot":  quality.to_dict(),
        "valuation":         valuation.to_dict(),
        "dividend":          dividend.to_dict(),
        "entry_timing":      timing.to_dict(),
        "long_term_grade":   long_term_grade,
        "long_term_trends":  long_term_trends,
        "sector_template":   sector_template,
    }


def _recommend_horizon(
    technical: dict | None,
    fundamental: dict,
) -> tuple[HorizonRecommendation, str]:
    """Single-token answer to 'is this short / medium / long-term?'.

    Priority order (first match wins):
      AVOID             grade F  OR  technical AVOID with valuation STRETCHED
      LONG_TERM_HOLD    grade A or B AND valuation != STRETCHED
                        AND dividend in (STRONG, STEADY)
      MEDIUM_TERM_ADD   entry_timing.ACCUMULATE
      SHORT_TERM_TRADE  technical bucket=BUY with conviction HIGH or MEDIUM
                        AND rr_gate passed
      WATCH             technical WAIT  OR  fundamental quality < ★★★
      INSUFFICIENT      nothing else fit
    """
    fund = fundamental or {}
    qs = fund.get("quality_snapshot") or {}
    val = fund.get("valuation") or {}
    div = fund.get("dividend") or {}
    timing = fund.get("entry_timing") or {}
    lt = fund.get("long_term_grade") or {}

    grade = lt.get("grade")
    val_verdict = val.get("overall_verdict")
    div_verdict = div.get("verdict")
    timing_verdict = timing.get("verdict")
    stars = qs.get("stars") or 0

    tech_bucket = (technical or {}).get("bucket")
    tech_conviction = (technical or {}).get("conviction")
    tech_rr = ((technical or {}).get("rr_gate") or {}).get("passed")

    if grade == "F":
        return "AVOID", (
            "Long-term grade F — fundamentals don't support compounding; "
            "fundamental rejection overrides any short-term technical bid."
        )
    if tech_bucket == "AVOID" and val_verdict == "STRETCHED":
        return "AVOID", (
            "Technical AVOID + valuation STRETCHED — both lenses agree there's "
            "no edge here."
        )

    if grade in ("A", "B") and val_verdict in ("ATTRACTIVE", "FAIR") and div_verdict in ("STRONG", "STEADY"):
        return "LONG_TERM_HOLD", (
            f"Long-term grade {grade}, valuation {val_verdict}, dividend "
            f"{div_verdict} — compounder profile. SIPP / pension / 25%-sleeve "
            "candidate."
        )

    if timing_verdict == "ACCUMULATE":
        return "MEDIUM_TERM_ADD", (
            "Entry timing ACCUMULATE — quality + valuation + drawdown agree "
            "this is a dip-add zone over weeks-to-months."
        )

    if (tech_bucket == "BUY"
            and tech_conviction in ("HIGH", "MEDIUM")
            and tech_rr):
        return "SHORT_TERM_TRADE", (
            f"Technical BUY at conviction {tech_conviction}, RR gate passed — "
            "swing/intraday entry; size per the exit framework."
        )

    if tech_bucket == "WAIT" or stars < 3:
        return "WATCH", (
            "Mixed signals — neither lens shows a clear edge today. "
            "Re-check after the next earnings or signal cycle."
        )

    return "INSUFFICIENT", (
        "Not enough data on either lens to call a horizon. Check the "
        "warnings block."
    )


def build_symbol_analysis_card(
    symbol: str,
    *,
    info: dict | None = None,
    compare_row: dict | None = None,
    long_term_result: dict | None = None,
    long_term_years: int = 5,
    skip_long_term: bool = False,
    drawdown_pct: float | None = None,
    market_state: dict | None = None,
) -> SymbolAnalysisCard:
    """Compose the unified card for `symbol`.

    Inputs (all optional — supply what you have, the orchestrator
    degrades gracefully):
      info               — yfinance.Ticker.info dict (production) or fixture
                           (tests). Fetched live when None.
      compare_row        — full row from compare.py. When supplied, populates
                           the technical block. None → fundamental-only card.
      long_term_result   — pre-computed analyse_long_term output. When None
                           AND skip_long_term is False, fetched live.
      long_term_years    — passed through to analyse_long_term.
      skip_long_term     — set True for offline / test paths that don't want
                           to fetch the multi-year DataFrames.

    Returns a SymbolAnalysisCard. Never raises — all underlying helpers
    are wrapped so a partial data failure surfaces in `warnings` rather
    than aborting the card.
    """
    warnings: list[str] = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    if info is None:
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).info or {}
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"yfinance info fetch failed: {exc}")
            info = {}

    if long_term_result is None and not skip_long_term:
        try:
            from ..fundamental_analysis import analyse_long_term
            long_term_result = analyse_long_term(
                symbol, years=long_term_years, include_peers=False,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"long_term fetch failed: {exc}")
            long_term_result = None

    technical = _extract_technical(compare_row)
    fundamental = _compose_fundamental(
        symbol,
        info=info,
        long_term_result=long_term_result,
        drawdown_pct=drawdown_pct,
        market_state=market_state,
        warnings=warnings,
    )
    recommendation, rationale = _recommend_horizon(technical, fundamental)

    return SymbolAnalysisCard(
        symbol=symbol.upper(),
        fetched_at=fetched_at,
        technical=technical,
        fundamental=fundamental,
        primary_horizon_recommendation=recommendation,
        rationale=rationale,
        warnings=warnings,
    )
