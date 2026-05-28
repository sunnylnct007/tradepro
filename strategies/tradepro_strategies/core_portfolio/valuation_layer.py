"""Valuation Layer — Track 2 module ②.

Per TradePro_Roadmap_May2026.docx Track 2 module 2. The key
Compounder-mode differentiator: signal is ATTRACTIVE / FAIR /
STRETCHED, NOT BUY / SELL. Prevents buying MSFT at 40x forward
earnings when 28x is available six months later.

Vocabulary intentionally diverges from Track 1's BUY/WAIT/AVOID —
valuation is a HOLDING decision, not an entry timing call. A
STRETCHED quality compounder still belongs in the sleeve; it just
shouldn't be added to today.

Metrics consulted (v1 — yfinance.info absolute thresholds; v2 will
add 5y-average and sector-median comparisons once we license a
fundamentals timeseries feed):

  trailingPE          < 12  ATTRACTIVE · 12-25 FAIR · > 25 STRETCHED
  forwardPE           < 12  ATTRACTIVE · 12-22 FAIR · > 22 STRETCHED
  priceToBook         < 1.5 ATTRACTIVE · 1.5-3.5 FAIR · > 3.5 STRETCHED
  enterpriseToEbitda  < 10  ATTRACTIVE · 10-20 FAIR · > 20 STRETCHED
  pegRatio            < 1.0 ATTRACTIVE · 1.0-2.0 FAIR · > 2.0 STRETCHED

These are absolute thresholds calibrated against the S&P 500 long-
term median. They're necessarily coarse — a 25x P/E is appropriate
for a software compounder and stretched for a utility. v2 with
sector-relative scoring will resolve that. The v1 thresholds are
conservative enough that anything STRETCHED genuinely is.

Final signal is the MAJORITY VERDICT across available metrics with
a tiebreaker favouring the more conservative call:
  - 2+ ATTRACTIVE and no STRETCHED → ATTRACTIVE
  - 2+ STRETCHED and no ATTRACTIVE → STRETCHED
  - any mix → FAIR
  - all missing → UNKNOWN (rendered as a missing-data badge)

Deferred to v2 (needs paid data):
  - P/E vs 5-year average — needs price + EPS timeseries (Polygon)
  - EV/EBITDA sector-relative percentile — needs sector dataset
  - ETF basket P/E weighted from holdings — needs ETF X-Ray (module 6)
  - ETF NAV premium/discount — needs exchange data feed
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

_log = logging.getLogger("tradepro.core_portfolio.valuation_layer")


Verdict = Literal["ATTRACTIVE", "FAIR", "STRETCHED", "UNKNOWN"]


@dataclass
class MetricVerdict:
    """One valuation metric's raw value + verdict + display label."""
    name: str
    raw: float | None
    verdict: Verdict
    display: str
    threshold_attractive: float | None = None
    threshold_stretched: float | None = None

    def to_dict(self) -> dict:
        return {
            "name":                  self.name,
            "raw":                   self.raw,
            "verdict":               self.verdict,
            "display":               self.display,
            "threshold_attractive":  self.threshold_attractive,
            "threshold_stretched":   self.threshold_stretched,
        }


@dataclass
class ValuationLayer:
    """Symbol-level valuation view. Maps 1:1 to the Track 2 valuation
    block on the Compounder card."""
    symbol: str
    overall_verdict: Verdict
    metrics: list[MetricVerdict] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)
    source: str = "yfinance"
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "overall_verdict": self.overall_verdict,
            "metrics":         [m.to_dict() for m in self.metrics],
            "missing_metrics": list(self.missing_metrics),
            "source":          self.source,
            "rationale":       self.rationale,
        }


# Threshold tuples: (attractive_below, stretched_above). A value
# below `attractive_below` reads ATTRACTIVE; above `stretched_above`
# reads STRETCHED; in between reads FAIR.
_THRESHOLDS: dict[str, tuple[float, float]] = {
    "trailing_pe":          (12.0, 25.0),
    "forward_pe":           (12.0, 22.0),
    "price_to_book":        (1.5, 3.5),
    "enterprise_to_ebitda": (10.0, 20.0),
    "peg_ratio":            (1.0, 2.0),
}


_YF_KEY_MAP = {
    "trailing_pe":          "trailingPE",
    "forward_pe":           "forwardPE",
    "price_to_book":        "priceToBook",
    "enterprise_to_ebitda": "enterpriseToEbitda",
    "peg_ratio":            "pegRatio",
}


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    # Negative or zero multiples mean negative earnings → metric is
    # undefined here, not "attractively cheap". Treat as missing.
    if v <= 0:
        return None
    return v


def _verdict_for(value: float | None, thresholds: tuple[float, float]) -> Verdict:
    """Lower than `attractive_below` → ATTRACTIVE; higher than
    `stretched_above` → STRETCHED; in between → FAIR; missing → UNKNOWN."""
    if value is None:
        return "UNKNOWN"
    attractive_below, stretched_above = thresholds
    if value < attractive_below:
        return "ATTRACTIVE"
    if value > stretched_above:
        return "STRETCHED"
    return "FAIR"


def _format_multiple(value: float | None, digits: int = 1) -> str:
    return f"{value:.{digits}f}x" if value is not None else "—"


def _extract_metrics(info: dict[str, Any]) -> dict[str, float | None]:
    """Pull each metric from a yfinance `info` dict and normalise."""
    out: dict[str, float | None] = {}
    for name, yf_key in _YF_KEY_MAP.items():
        out[name] = _safe_float(info.get(yf_key))
    return out


def _fetch_info(symbol: str) -> dict[str, Any] | None:
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


def _aggregate(verdicts: list[Verdict]) -> tuple[Verdict, str]:
    """Combine per-metric verdicts into one overall call.

    Rules (conservative; ties favour the safer signal):
      - ≥ 2 ATTRACTIVE and 0 STRETCHED → ATTRACTIVE
      - ≥ 2 STRETCHED and 0 ATTRACTIVE → STRETCHED
      - mix of ATTRACTIVE + STRETCHED → FAIR (signal is unclear)
      - mostly FAIR with at most 1 outlier → FAIR
      - all UNKNOWN → UNKNOWN
    """
    present = [v for v in verdicts if v != "UNKNOWN"]
    if not present:
        return "UNKNOWN", "all valuation metrics missing"
    n_attractive = present.count("ATTRACTIVE")
    n_stretched  = present.count("STRETCHED")
    n_fair       = present.count("FAIR")
    if n_attractive >= 2 and n_stretched == 0:
        return ("ATTRACTIVE",
                f"{n_attractive} attractive metric(s), {n_fair} fair, 0 stretched")
    if n_stretched >= 2 and n_attractive == 0:
        return ("STRETCHED",
                f"{n_stretched} stretched metric(s), {n_fair} fair, 0 attractive")
    if n_attractive > 0 and n_stretched > 0:
        return ("FAIR",
                f"mixed: {n_attractive} attractive vs {n_stretched} stretched "
                f"({n_fair} fair) — signal unclear")
    return ("FAIR",
            f"{n_fair} fair, {n_attractive} attractive, {n_stretched} stretched")


def compute_valuation_layer(
    symbol: str,
    *,
    info: dict[str, Any] | None = None,
) -> ValuationLayer:
    """Build the valuation layer for `symbol`. Pass an explicit `info`
    dict for tests / offline runs; otherwise yfinance is consulted
    live. Returns a ValuationLayer with overall_verdict=UNKNOWN and an
    empty metrics list when the fetch fails."""
    if info is None:
        info = _fetch_info(symbol)
    if not info:
        return ValuationLayer(
            symbol=symbol.upper(),
            overall_verdict="UNKNOWN",
            metrics=[],
            missing_metrics=list(_YF_KEY_MAP.keys()),
            source="yfinance",
            rationale="yfinance fetch failed or returned empty",
        )

    raw = _extract_metrics(info)
    metrics: list[MetricVerdict] = []
    for name, value in raw.items():
        thresholds = _THRESHOLDS[name]
        metrics.append(MetricVerdict(
            name=name,
            raw=value,
            verdict=_verdict_for(value, thresholds),
            display=_format_multiple(value),
            threshold_attractive=thresholds[0],
            threshold_stretched=thresholds[1],
        ))
    overall, rationale = _aggregate([m.verdict for m in metrics])
    missing = [m.name for m in metrics if m.raw is None]
    return ValuationLayer(
        symbol=symbol.upper(),
        overall_verdict=overall,
        metrics=metrics,
        missing_metrics=missing,
        source="yfinance",
        rationale=rationale,
    )
