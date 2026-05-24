"""Entry Timing Assist — Track 2 module ⑤.

Per TradePro_Roadmap_May2026.docx §Track 2 module 5. Explicitly NOT
a buy signal — a dip-accumulation alert. The docx example:

  "MSFT is 12% below its 52-week high and trading at a P/E 18%
   below its 5-year average — historically a strong accumulation
   zone."

Combines three already-computed Track 2 / market signals:

  1. Quality Scorecard (module ①) — only ★★★★+ names qualify.
     Accumulating into low-quality on weakness is a value trap.
  2. Valuation Layer (module ②) — ATTRACTIVE means the price tag
     genuinely shrank relative to the asset.
  3. Drawdown from 52-week high — ≥10% is a meaningful dip, not
     intraday noise. Read straight from the existing market_state
     row, which carries `last_price` and `peak_price` (or
     `pct_off_52w_high_pct`).

Output verdict (Compounder-mode vocabulary, distinct from BUY/SELL):

  ACCUMULATE   all three signals agree — strong accumulation zone
  WATCH        2 of 3 — dip is real but valuation or quality slips;
               keep watching but don't add yet
  NEUTRAL      ≤1 signal — no edge over normal DCA timing
  INSUFFICIENT one of the three inputs is missing — surface so the
               UI doesn't pretend the absence is a NEUTRAL

Pure function. Caller passes the QualityScorecard, ValuationLayer,
and a market_state dict (or explicit drawdown_pct + last_price).
No network calls — wires into compare.py where those three blocks
are already computed.

Future v2:
  - 5y average P/E comparison (needs Polygon timeseries) — would
    replace the absolute-threshold valuation check with a tighter
    "P/E 18% below 5y avg" rule per the docx example
  - Catalyst-aware overlay — suppress accumulate when there's a
    fresh negative catalyst (already half-built via the existing
    sentiment_demotion + earnings suppressor)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from .quality_scorecard import QualityScorecard
from .valuation_layer import ValuationLayer

_log = logging.getLogger("tradepro.core_portfolio.entry_timing")


Verdict = Literal["ACCUMULATE", "WATCH", "NEUTRAL", "INSUFFICIENT"]


# Minimum quality stars to consider a compounder accumulation candidate.
# Below this, a price drawdown is a value-trap signal, not an opportunity.
MIN_QUALITY_STARS = 4

# Minimum drawdown from 52-week high to call a dip meaningful (vs noise).
# 10% is the conventional "correction" threshold; we use it as the lower
# bound of "real dip".
MIN_DRAWDOWN_PCT = 10.0


@dataclass
class EntryTimingAssist:
    """Output of compute_entry_timing(). Maps to the Track 2 dip-
    accumulation card."""
    symbol: str
    verdict: Verdict
    signals_passing: int                # 0-3
    quality_stars: int | None
    valuation_verdict: str | None       # ATTRACTIVE / FAIR / STRETCHED / UNKNOWN
    drawdown_from_52w_high_pct: float | None
    rationale: str

    def to_dict(self) -> dict:
        return {
            "symbol":                       self.symbol,
            "verdict":                      self.verdict,
            "signals_passing":              self.signals_passing,
            "quality_stars":                self.quality_stars,
            "valuation_verdict":            self.valuation_verdict,
            "drawdown_from_52w_high_pct":   (round(self.drawdown_from_52w_high_pct, 2)
                                              if self.drawdown_from_52w_high_pct is not None else None),
            "rationale":                    self.rationale,
        }


def _safe_float(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _resolve_drawdown(
    *,
    market_state: dict | None,
    explicit_drawdown_pct: float | None,
) -> float | None:
    """Resolve drawdown-from-52w-high from either an explicit value or
    a market_state row. Returns None when neither is available."""
    if explicit_drawdown_pct is not None:
        return _safe_float(explicit_drawdown_pct)
    if not market_state:
        return None
    # market_state.pct_off_52w_high_pct is the canonical field that
    # compare.py / market_state.py populates. It's stored as a positive
    # percent (e.g. 12.3 means price is 12.3% below the 52w high).
    direct = _safe_float(market_state.get("pct_off_52w_high_pct"))
    if direct is not None:
        return direct
    # Fall back: derive from last_price + 52w high if both present.
    last = _safe_float(market_state.get("last_price"))
    high = _safe_float(market_state.get("pct_off_52w_high_price")
                       or market_state.get("peak_price"))
    if last is not None and high is not None and high > 0:
        return ((high - last) / high) * 100.0
    return None


def compute_entry_timing(
    symbol: str,
    *,
    quality: QualityScorecard | None,
    valuation: ValuationLayer | None,
    market_state: dict | None = None,
    drawdown_pct: float | None = None,
    min_quality_stars: int = MIN_QUALITY_STARS,
    min_drawdown_pct: float = MIN_DRAWDOWN_PCT,
) -> EntryTimingAssist:
    """Compute the Entry Timing Assist verdict.

    Inputs:
      symbol            — ticker (for the result envelope)
      quality           — output of compute_quality_scorecard, or None
                          if not available (treated as missing signal)
      valuation         — output of compute_valuation_layer, or None
      market_state      — compare.py row's market_state dict (looks up
                          pct_off_52w_high_pct), OR
      drawdown_pct      — explicit drawdown override (test path)

    Returns ACCUMULATE only when ALL THREE signals pass:
      - quality stars >= min_quality_stars (default 4)
      - valuation overall_verdict == ATTRACTIVE
      - drawdown_from_52w_high >= min_drawdown_pct (default 10%)

    WATCH when 2 of 3 pass. NEUTRAL when ≤1 passes. INSUFFICIENT when
    any input is missing — surface so the UI can render "data thin"
    rather than silently pretending the dip is unattractive.
    """
    drawdown = _resolve_drawdown(
        market_state=market_state,
        explicit_drawdown_pct=drawdown_pct,
    )

    quality_stars = quality.stars if quality is not None else None
    valuation_verdict = valuation.overall_verdict if valuation is not None else None

    # Detect missing inputs FIRST — INSUFFICIENT is honest; pretending
    # the dip isn't real because we don't have data is misleading.
    missing: list[str] = []
    if quality_stars is None:
        missing.append("quality")
    if valuation_verdict is None or valuation_verdict == "UNKNOWN":
        missing.append("valuation")
    if drawdown is None:
        missing.append("drawdown")

    if missing:
        return EntryTimingAssist(
            symbol=symbol.upper(),
            verdict="INSUFFICIENT",
            signals_passing=0,
            quality_stars=quality_stars,
            valuation_verdict=valuation_verdict,
            drawdown_from_52w_high_pct=drawdown,
            rationale=f"insufficient data: {', '.join(missing)} missing",
        )

    quality_pass = quality_stars >= min_quality_stars
    valuation_pass = valuation_verdict == "ATTRACTIVE"
    drawdown_pass = drawdown >= min_drawdown_pct

    signals_passing = sum([quality_pass, valuation_pass, drawdown_pass])

    if signals_passing == 3:
        verdict = "ACCUMULATE"
        rationale = (
            f"All three signals agree: {quality_stars}★ quality, "
            f"valuation {valuation_verdict}, {drawdown:.1f}% off 52w high. "
            f"Historical accumulation zone — opportunistic DCA candidate."
        )
    elif signals_passing == 2:
        verdict = "WATCH"
        missing_signal = (
            "quality" if not quality_pass
            else "valuation" if not valuation_pass
            else "drawdown"
        )
        rationale = (
            f"2 of 3 signals: {quality_stars}★ quality, valuation "
            f"{valuation_verdict}, {drawdown:.1f}% off 52w high. "
            f"Missing {missing_signal} confluence — keep watching."
        )
    else:
        verdict = "NEUTRAL"
        rationale = (
            f"Only {signals_passing} of 3 signals: {quality_stars}★ "
            f"quality, valuation {valuation_verdict}, {drawdown:.1f}% off "
            f"52w high. No edge over routine DCA — accumulate on schedule."
        )

    return EntryTimingAssist(
        symbol=symbol.upper(),
        verdict=verdict,
        signals_passing=signals_passing,
        quality_stars=quality_stars,
        valuation_verdict=valuation_verdict,
        drawdown_from_52w_high_pct=drawdown,
        rationale=rationale,
    )
