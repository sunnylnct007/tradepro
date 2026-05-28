"""Quality Scorecard — equity-quality fundamentals view (Track 2 §1).

Per TradePro_Roadmap_May2026.docx Track 2 module 1: a per-holding
fundamentals view answering "is this a quality asset I can compound
in?". Distinct from the existing `fundamentals.py` which covers ETF
attributes (yield / AUM / expense ratio / top holdings).

The scorecard scores six metrics 0-10 each, averages them to a 0-5
star rating, and carries the raw values so the UI can show "ROE
24% (★★★★★)" without re-deriving.

Per metric scoring rationale (calibrated against S&P 500 distribution
medians + the docx's quality threshold examples):

  ROE                   < 5% → 0 · 5-10% → 3 · 10-15% → 5 ·
                        15-25% → 8 · ≥ 25% → 10
  ROA                   < 2% → 0 · 2-5% → 4 · 5-10% → 7 · ≥ 10% → 10
  FCF margin            < 0 → 0 · 0-5% → 3 · 5-15% → 6 · 15-25% → 8 · ≥ 25% → 10
  Debt / Equity         > 3.0 → 0 · 2-3 → 2 · 1-2 → 5 · 0.5-1 → 8 · < 0.5 → 10
  Profit margin         < 0 → 0 · 0-5% → 3 · 5-15% → 6 · 15-25% → 8 · ≥ 25% → 10
  Current ratio         < 1.0 → 0 · 1-1.5 → 4 · 1.5-2.5 → 8 · ≥ 2.5 → 10
                        (very high → still 10; super-liquid balance sheets
                        are fine for compounders, only the < 1.0 floor
                        signals balance-sheet stress.)

Star rating: floor(average_score / 2). So 9.0+ → ★★★★★ (4.5 stars
rounded up), 7.0-8.9 → ★★★★, etc. The docx visual uses 5-star
display so we land at the same vocabulary.

Future v2 (not in this commit):
  - ROIC (NOPAT / Invested Capital) — not directly in yfinance.info;
    derive from balance_sheet + income_statement DataFrames.
  - Interest Coverage (EBIT / Interest Expense) — same.
  - 5y Revenue + EPS CAGR — needs income_statement history (yfinance
    has ~4 quarters by default, full history is paid).
  - Moat tag (Wide / Narrow / None) — Morningstar paid; manual tag
    initially per the docx.

Pure function — accepts either an `info` dict (test path) or a symbol
that triggers a yfinance fetch (production path). Tested without
touching the network via fixture info dicts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("tradepro.core_portfolio.quality_scorecard")


# Each metric's name maps to the yfinance.Ticker.info key. yfinance
# returns ROE / margin fields as fractions (0.24 means 24%) and
# debtToEquity as a percentage (60 means 60%, i.e. ratio 0.6). The
# scoring tables below treat all inputs as natural ratios — extract()
# normalises yfinance shapes into that form.
_YF_KEY_MAP = {
    "roe":            "returnOnEquity",
    "roa":            "returnOnAssets",
    "fcf_margin":     None,                  # computed: freeCashflow / totalRevenue
    "debt_to_equity": "debtToEquity",
    "profit_margin":  "profitMargins",
    "current_ratio":  "currentRatio",
}


@dataclass
class MetricScore:
    """One metric's raw value + 0-10 score + human label.

    `raw` is the natural-unit value (ratio for margins, multiple for
    D/E). `display` is the formatted string the UI shows."""
    name: str
    raw: float | None
    score: int             # 0-10 (None inputs always score 0)
    display: str

    def to_dict(self) -> dict:
        return {
            "name":    self.name,
            "raw":     self.raw,
            "score":   self.score,
            "display": self.display,
        }


@dataclass
class QualityScorecard:
    """Symbol-level quality view. Maps 1:1 to the Track 2 scorecard
    block on the Compounder card."""
    symbol: str
    stars: int                          # 0..5
    overall_score: float                # 0..10 average
    metrics: list[MetricScore] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)
    source: str = "yfinance"

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "stars":            self.stars,
            "stars_display":    "★" * self.stars + "☆" * (5 - self.stars),
            "overall_score":    round(self.overall_score, 2),
            "metrics":          [m.to_dict() for m in self.metrics],
            "missing_metrics":  list(self.missing_metrics),
            "source":           self.source,
        }


# ─────────── per-metric scorers ───────────


def _bucket_score(value: float | None, breakpoints: list[tuple[float, int]]) -> int:
    """Walk `breakpoints` (sorted ascending by threshold) and return
    the score for the first threshold the value clears. Missing data
    → 0 (treated as worst-case; UI surfaces it as a missing-metric
    badge rather than silently averaging it as neutral).

    Example breakpoints for ROE:
      [(0.05, 0), (0.10, 3), (0.15, 5), (0.25, 8), (float("inf"), 10)]
    means ROE < 5% → 0; 5-10% → 3; 10-15% → 5; 15-25% → 8; ≥ 25% → 10.
    """
    if value is None:
        return 0
    for threshold, score in breakpoints:
        if value < threshold:
            return score
    return breakpoints[-1][1]


def _bucket_score_inverted(value: float | None, breakpoints: list[tuple[float, int]]) -> int:
    """Same as _bucket_score but for metrics where lower is better
    (e.g. debt-to-equity). Breakpoints are still in ascending threshold
    order; the first threshold the value clears gets the score
    (higher threshold = lower score)."""
    if value is None:
        return 0
    for threshold, score in breakpoints:
        if value < threshold:
            return score
    return breakpoints[-1][1]


_ROE_BREAKPOINTS = [(0.05, 0), (0.10, 3), (0.15, 5), (0.25, 8), (float("inf"), 10)]
_ROA_BREAKPOINTS = [(0.02, 0), (0.05, 4), (0.10, 7), (float("inf"), 10)]
_FCF_MARGIN_BREAKPOINTS = [(0.0, 0), (0.05, 3), (0.15, 6), (0.25, 8), (float("inf"), 10)]
_PROFIT_MARGIN_BREAKPOINTS = [(0.0, 0), (0.05, 3), (0.15, 6), (0.25, 8), (float("inf"), 10)]
_CURRENT_RATIO_BREAKPOINTS = [(1.0, 0), (1.5, 4), (2.5, 8), (float("inf"), 10)]
# D/E: lower is better; thresholds are ascending so a low value clears
# the first threshold and gets the high score.
_DE_BREAKPOINTS = [(0.5, 10), (1.0, 8), (2.0, 5), (3.0, 2), (float("inf"), 0)]


def _format_pct(value: float | None, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%" if value is not None else "—"


def _format_ratio(value: float | None, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if value is not None else "—"


# ─────────── extraction from yfinance ───────────


def _extract_metrics(info: dict[str, Any]) -> dict[str, float | None]:
    """Pull the six metrics out of a yfinance `info` dict and normalise.

    yfinance quirks:
      - debtToEquity is reported as a percent (e.g. 60 means 60% → ratio 0.60)
      - returnOnEquity / returnOnAssets / profitMargins are fractions
        (e.g. 0.24 means 24%)
      - currentRatio is a multiple (e.g. 2.1)
      - freeCashflow + totalRevenue are absolute dollars; we derive
        fcf_margin = freeCashflow / totalRevenue ourselves
    """
    def _safe_float(x: Any) -> float | None:
        if x is None:
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        if v != v:  # NaN
            return None
        return v

    out: dict[str, float | None] = {}
    out["roe"]            = _safe_float(info.get("returnOnEquity"))
    out["roa"]            = _safe_float(info.get("returnOnAssets"))
    out["profit_margin"]  = _safe_float(info.get("profitMargins"))
    out["current_ratio"]  = _safe_float(info.get("currentRatio"))

    de_raw = _safe_float(info.get("debtToEquity"))
    out["debt_to_equity"] = (de_raw / 100.0) if de_raw is not None else None

    fcf = _safe_float(info.get("freeCashflow"))
    rev = _safe_float(info.get("totalRevenue"))
    if fcf is not None and rev is not None and rev != 0:
        out["fcf_margin"] = fcf / rev
    else:
        out["fcf_margin"] = None
    return out


def _fetch_info(symbol: str) -> dict[str, Any] | None:
    """Best-effort yfinance fetch. Returns None on any failure so the
    caller can return an empty scorecard rather than crashing."""
    try:
        import yfinance as yf
    except ImportError:
        _log.warning("yfinance not installed; cannot fetch %s", symbol)
        return None
    try:
        return yf.Ticker(symbol).info or {}
    except Exception as e:  # noqa: BLE001
        _log.warning("yfinance fetch failed for %s: %s", symbol, e)
        return None


# ─────────── public API ───────────


def compute_quality_scorecard(
    symbol: str,
    *,
    info: dict[str, Any] | None = None,
) -> QualityScorecard:
    """Build the scorecard for `symbol`. Pass an explicit `info` dict
    for tests / offline runs; otherwise yfinance is consulted live.

    Returns a `QualityScorecard` with stars=0 and an empty metrics
    list when the fetch fails — callers can still attach it to the
    row and the UI can render "scorecard unavailable" cleanly."""
    if info is None:
        info = _fetch_info(symbol)
    if not info:
        return QualityScorecard(
            symbol=symbol.upper(),
            stars=0,
            overall_score=0.0,
            metrics=[],
            missing_metrics=list(_YF_KEY_MAP.keys()),
            source="yfinance",
        )

    raw = _extract_metrics(info)
    metric_scores: list[MetricScore] = [
        MetricScore(
            name="roe",
            raw=raw["roe"],
            score=_bucket_score(raw["roe"], _ROE_BREAKPOINTS),
            display=_format_pct(raw["roe"]),
        ),
        MetricScore(
            name="roa",
            raw=raw["roa"],
            score=_bucket_score(raw["roa"], _ROA_BREAKPOINTS),
            display=_format_pct(raw["roa"]),
        ),
        MetricScore(
            name="fcf_margin",
            raw=raw["fcf_margin"],
            score=_bucket_score(raw["fcf_margin"], _FCF_MARGIN_BREAKPOINTS),
            display=_format_pct(raw["fcf_margin"]),
        ),
        MetricScore(
            name="debt_to_equity",
            raw=raw["debt_to_equity"],
            score=_bucket_score_inverted(raw["debt_to_equity"], _DE_BREAKPOINTS),
            display=_format_ratio(raw["debt_to_equity"]),
        ),
        MetricScore(
            name="profit_margin",
            raw=raw["profit_margin"],
            score=_bucket_score(raw["profit_margin"], _PROFIT_MARGIN_BREAKPOINTS),
            display=_format_pct(raw["profit_margin"]),
        ),
        MetricScore(
            name="current_ratio",
            raw=raw["current_ratio"],
            score=_bucket_score(raw["current_ratio"], _CURRENT_RATIO_BREAKPOINTS),
            display=_format_ratio(raw["current_ratio"]),
        ),
    ]
    missing = [m.name for m in metric_scores if m.raw is None]
    # Average only the metrics we have a value for — averaging missing
    # ones as 0 would penalise small-cap names with thin coverage.
    present_scores = [m.score for m in metric_scores if m.raw is not None]
    if present_scores:
        overall = sum(present_scores) / len(present_scores)
    else:
        overall = 0.0
    stars = max(0, min(5, int(round(overall / 2.0))))
    return QualityScorecard(
        symbol=symbol.upper(),
        stars=stars,
        overall_score=overall,
        metrics=metric_scores,
        missing_metrics=missing,
        source="yfinance",
    )
