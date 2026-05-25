"""Core Portfolio / Compounder mode — Track 2 of TradePro.

Fundamentals-driven view for the ~25% "core sleeve" — high-quality
assets that compound quietly over years. Different paradigm from the
momentum-driven swing/intraday engine: never asks "should I buy
today?", asks "is this a quality asset, fairly valued, and
compounding my wealth?".

Signal vocabulary differs from Track 1 (Swing/Intraday):
  - Quality:    ★ to ★★★★★ (component scores 0-10)
  - Valuation:  ATTRACTIVE / FAIR / STRETCHED (not BUY / WAIT / AVOID)
  - Dividend:   sustainability + 5y growth trend
  - Entry:      dip-accumulation alert (not a buy signal)

Modules (per TradePro_Roadmap_May2026.docx §Track 2):
  1. quality_scorecard       — ROE / FCF / D/E / CAGR per equity
  2. valuation_layer         — P/E vs 5y avg, P/FCF, EV/EBITDA
  3. dividend_dashboard      — yield, CAGR, payout ratio
  4. allocation_view         — core sleeve tracker
  5. entry_timing_assist     — dip-accumulation alert combiner
  6. etf_xray                — holdings overlap detector
  7. manual_mf_sleeve        — UK/Indian/offshore MF NAV entry

Each module is a pure-Python helper that produces a JSON-serialisable
dict; compare.py / new endpoints attach them per-symbol. MCP tools
(per task #85) wrap each one for LLM analysis.
"""
from __future__ import annotations

from .allocation_view import (
    AllocationView, CoreSleevePosition, PositionBreakdown,
    compute_allocation_view,
)
from .dividend_dashboard import DividendDashboard, compute_dividend_dashboard
from .entry_timing import EntryTimingAssist, compute_entry_timing
from .etf_xray import (
    EtfOverlapReport, EtfXray, OverlapContribution,
    compute_etf_xray, compute_overlap, project_drip_value,
)
from .manual_mf_sleeve import (
    MFHoldingProjection, MFSleeve, ManualMFHolding, compute_mf_sleeve,
)
from .quality_scorecard import QualityScorecard, compute_quality_scorecard
from .symbol_analysis_card import (
    HorizonRecommendation, SymbolAnalysisCard, build_symbol_analysis_card,
)
from .valuation_layer import ValuationLayer, compute_valuation_layer

__all__ = [
    "QualityScorecard", "compute_quality_scorecard",
    "ValuationLayer", "compute_valuation_layer",
    "DividendDashboard", "compute_dividend_dashboard",
    "AllocationView", "CoreSleevePosition", "PositionBreakdown",
    "compute_allocation_view",
    "EntryTimingAssist", "compute_entry_timing",
    "EtfXray", "EtfOverlapReport", "OverlapContribution",
    "compute_etf_xray", "compute_overlap", "project_drip_value",
    "ManualMFHolding", "MFSleeve", "MFHoldingProjection", "compute_mf_sleeve",
    "SymbolAnalysisCard", "HorizonRecommendation", "build_symbol_analysis_card",
]
