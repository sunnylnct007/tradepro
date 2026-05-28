"""COMPASS — Continuous Multi-factor Alpha Scoring for Portfolio Positioning.

COMPASS assigns every symbol a daily composite score (0–100) by combining
six independent signal families. Models read this score to decide:
  - whether to initiate a position (score > 70 = candidate)
  - how much to size it (score > 80 = 1.5× base)
  - when to trim (score < 50 = reduce; score < 35 = exit)

Design principles:
  1. Every factor is independently interpretable — no hidden weights.
  2. Score is stable enough not to flip daily (each factor is a regime,
     not a noise spike) but reactive enough to catch turning points.
  3. Missing data degrades to neutral (5/10), never crashes the scorer.
  4. The macro regime gate (Sprint 1) gates EXECUTION, not the score.
     A stock can score 85/100 during risk_mode=3 — the score is the
     truth about the stock; the mode controls whether you act.

Factor stack (weights sum to 100%):
  ┌─────────────────────────────────────┬────────┐
  │ Factor                              │ Weight │
  ├─────────────────────────────────────┼────────┤
  │ 1. Price momentum (3m + peer rank)  │  20%   │
  │ 2. Earnings revision (90d EPS Δ)    │  20%   │
  │ 3. Quality (FCF yield + Sharpe)     │  15%   │
  │ 4. Relative strength vs sector ETF  │  15%   │
  │ 5. Analyst conviction (bull score)  │  15%   │
  │ 6. News sentiment (7d material)     │  10%   │
  │ 7. Valuation (fwd P/E)              │   5%   │
  └─────────────────────────────────────┴────────┘

Each factor returns 0–10.  Final score = Σ(weight × factor) × 10.

Signal thresholds:
  score ≥ 72  → BUY     (top quartile of expected quality)
  score ≥ 55  → WATCH   (above average, wait for better entry)
  score ≥ 40  → HOLD    (don't add; hold if already in)
  score < 40  → TRIM    (reduce / exit)

Conviction tiers:
  score ≥ 78  → HIGH
  score ≥ 60  → MEDIUM
  score < 60  → LOW
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

# Module-level import so BDD steps can patch
# tradepro_strategies.compass_scorer.macro_regime.get_risk_mode
try:
    from . import macro_regime  # noqa: F401  (re-exported for test patching)
except ImportError:
    macro_regime = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Factor weights — must sum to 1.0
# ---------------------------------------------------------------------------
_WEIGHTS: dict[str, float] = {
    "momentum":          0.20,
    "earnings_revision": 0.20,
    "quality":           0.15,
    "sector_rs":         0.15,
    "analyst":           0.15,
    "sentiment":         0.10,
    "valuation":         0.05,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1.0"


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

@dataclass
class FactorDetail:
    name: str
    score: int            # 0-10
    weight: float         # contribution weight
    contribution: float   # score × weight (raw, before ×10)
    evidence: str         # one-line explainer

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompassResult:
    symbol: str
    score: float                      # 0–100
    signal: str                       # BUY | WATCH | HOLD | TRIM
    conviction: str                   # HIGH | MEDIUM | LOW
    factors: list[FactorDetail] = field(default_factory=list)
    macro_gated: bool = False         # True when risk_mode == 3 (RED)
    macro_mode: int = 1               # 1/2/3 at time of scoring
    entry_note: str = ""              # human summary for UI / MCP

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 1),
            "signal": self.signal,
            "conviction": self.conviction,
            "macro_gated": self.macro_gated,
            "macro_mode": self.macro_mode,
            "entry_note": self.entry_note,
            "factors": [f.to_dict() for f in self.factors],
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_compass_score(
    symbol: str,
    row: dict[str, Any],
    *,
    sector_rs_result: dict | None = None,
    eps_revision: dict | None = None,
) -> CompassResult:
    """Compute COMPASS score from an assembled compare row.

    Parameters
    ----------
    symbol
        Ticker symbol (for logging; also read from row if omitted).
    row
        A single assembled compare row from `compare.py`. Must contain
        at least `market_state`, `external_consensus`, `fundamentals`,
        `sentiment_summary`, and optionally `cross_sectional_momentum`.
    sector_rs_result
        Output of `sector_rs.compute_sector_rs(symbol)`.  When None, the
        sector RS factor is scored as neutral (5).
    eps_revision
        Output of `eps_tracker.get_eps_revision(symbol)`.  When None, the
        earnings revision factor is scored as neutral (5).

    Returns
    -------
    CompassResult with score 0–100, signal, conviction, and per-factor breakdown.
    """
    sym = symbol.upper()

    factors: list[FactorDetail] = []
    factors.append(_factor_momentum(row))
    factors.append(_factor_earnings_revision(eps_revision))
    factors.append(_factor_quality(row))
    factors.append(_factor_sector_rs(sector_rs_result))
    factors.append(_factor_analyst(row))
    factors.append(_factor_sentiment(row))
    factors.append(_factor_valuation(row))

    raw = sum(f.score * f.weight for f in factors)
    score = round(raw * 10.0, 1)  # scale to 0-100

    signal = _score_to_signal(score, row)
    conviction = _score_to_conviction(score)

    # Macro gate — uses the module-level `macro_regime` import so BDD tests
    # can patch tradepro_strategies.compass_scorer.macro_regime.get_risk_mode
    macro_mode = 1
    macro_gated = False
    try:
        if macro_regime is not None:
            macro_mode = macro_regime.get_risk_mode()
            macro_gated = (macro_mode == 3)
    except Exception:  # noqa: BLE001
        pass

    # Dampen signal label when AMBER
    if macro_mode == 2 and signal == "BUY":
        signal = "WATCH"

    entry_note = _build_entry_note(score, signal, macro_gated, macro_mode, row)

    return CompassResult(
        symbol=sym,
        score=score,
        signal=signal,
        conviction=conviction,
        factors=factors,
        macro_gated=macro_gated,
        macro_mode=macro_mode,
        entry_note=entry_note,
    )


# ---------------------------------------------------------------------------
# Factor scorers — each returns a FactorDetail(score 0-10)
# ---------------------------------------------------------------------------

def _factor_momentum(row: dict) -> FactorDetail:
    """Price momentum: 3-month return + cross-sectional peer rank."""
    ms = row.get("market_state") or {}
    m3 = ms.get("momentum_3m_pct")
    xsm = row.get("cross_sectional_momentum") or {}
    rank_pct = xsm.get("rank_pct")  # 1.0 = top, 0.0 = bottom

    # Base score from 3m momentum
    if m3 is None:
        base = 5
        evidence = "momentum data unavailable"
    elif m3 >= 20:
        base = 9
        evidence = f"3m +{m3:.1f}% — strong momentum"
    elif m3 >= 10:
        base = 7
        evidence = f"3m +{m3:.1f}% — positive momentum"
    elif m3 >= 2:
        base = 6
        evidence = f"3m +{m3:.1f}% — mild positive"
    elif m3 >= -3:
        base = 5
        evidence = f"3m {m3:.1f}% — flat"
    elif m3 >= -10:
        base = 3
        evidence = f"3m {m3:.1f}% — negative"
    else:
        base = 1
        evidence = f"3m {m3:.1f}% — sharp decline"

    # Peer rank bonus/penalty
    bonus = 0
    if rank_pct is not None:
        if rank_pct >= 0.75:
            bonus = 1
            evidence += f"; top-quartile peer (rank {rank_pct:.0%})"
        elif rank_pct <= 0.25:
            bonus = -1
            evidence += f"; bottom-quartile peer (rank {rank_pct:.0%})"

    score = max(0, min(10, base + bonus))
    return FactorDetail("momentum", score, _WEIGHTS["momentum"],
                        score * _WEIGHTS["momentum"], evidence)


def _factor_earnings_revision(eps_revision: dict | None) -> FactorDetail:
    """Earnings revision: 90-day EPS estimate delta direction + magnitude."""
    if not eps_revision or eps_revision.get("direction") == "insufficient_data":
        return FactorDetail(
            "earnings_revision", 5, _WEIGHTS["earnings_revision"],
            5 * _WEIGHTS["earnings_revision"],
            "no EPS revision data yet — run weekly eps snapshot first",
        )

    direction = eps_revision.get("direction", "flat")
    rev_pct = eps_revision.get("revision_pct")

    if direction == "up":
        if rev_pct is not None and rev_pct >= 10:
            score, note = 10, f"estimates raised +{rev_pct:.1f}% — strong bullish revision"
        elif rev_pct is not None and rev_pct >= 5:
            score, note = 8, f"estimates raised +{rev_pct:.1f}% — positive revision"
        else:
            score, note = 7, "estimates trending up"
    elif direction == "flat":
        score, note = 5, "estimates stable — no revision pressure"
    else:  # down
        if rev_pct is not None and rev_pct <= -10:
            score, note = 1, f"estimates cut {rev_pct:.1f}% — severe negative revision"
        elif rev_pct is not None and rev_pct <= -5:
            score, note = 2, f"estimates cut {rev_pct:.1f}% — negative revision"
        else:
            score, note = 3, "estimates drifting lower"

    return FactorDetail("earnings_revision", score, _WEIGHTS["earnings_revision"],
                        score * _WEIGHTS["earnings_revision"], note)


def _factor_quality(row: dict) -> FactorDetail:
    """Quality: FCF positivity + Sharpe ratio of best strategy."""
    fund = row.get("fundamentals") or {}
    stats = row.get("stats") or {}
    fcf = fund.get("free_cashflow")
    sharpe = _safe_float(stats.get("sharpe"))
    legal = (fund.get("legal_type") or "").upper()
    is_etf = "ETF" in legal or "FUND" in legal

    if is_etf:
        # ETFs don't have FCF — use Sharpe as the sole quality proxy
        if sharpe is None:
            score, note = 5, "ETF — no quality data"
        elif sharpe >= 1.0:
            score, note = 9, f"Sharpe {sharpe:.2f} — high quality ETF"
        elif sharpe >= 0.7:
            score, note = 7, f"Sharpe {sharpe:.2f} — good quality"
        elif sharpe >= 0.4:
            score, note = 5, f"Sharpe {sharpe:.2f} — average quality"
        else:
            score, note = 3, f"Sharpe {sharpe:.2f} — weak quality"
    else:
        # Stock: FCF is the primary; Sharpe is a tiebreaker
        if fcf is not None and fcf > 0:
            base = 7
            note = f"FCF ${fcf/1e9:.1f}B positive"
            if sharpe is not None and sharpe >= 0.8:
                base = 9
                note += f"; Sharpe {sharpe:.2f}"
            elif sharpe is not None and sharpe >= 0.5:
                base = 7
                note += f"; Sharpe {sharpe:.2f}"
        elif fcf is not None and fcf <= 0:
            base = 2
            note = f"FCF ${fcf/1e9:.1f}B — cash-burning"
            if sharpe is not None and sharpe >= 0.8:
                base = 4   # strong strategy despite FCF burn (growth phase)
                note += f"; offset by Sharpe {sharpe:.2f}"
        else:
            base = 5
            note = "FCF not available"
            if sharpe is not None:
                note += f"; Sharpe {sharpe:.2f}"
        score = base

    score = max(0, min(10, score))
    return FactorDetail("quality", score, _WEIGHTS["quality"],
                        score * _WEIGHTS["quality"], note)


def _factor_sector_rs(sector_rs_result: dict | None) -> FactorDetail:
    """Relative strength vs sector ETF."""
    if not sector_rs_result or sector_rs_result.get("error") and sector_rs_result.get("rs_score") == 5:
        return FactorDetail(
            "sector_rs", 5, _WEIGHTS["sector_rs"],
            5 * _WEIGHTS["sector_rs"],
            "sector RS unavailable — defaulting to neutral",
        )

    rs = sector_rs_result.get("rs_12w_pct")
    score = sector_rs_result.get("rs_score", 5)
    etf = sector_rs_result.get("sector_etf", "SPY")
    fallback = sector_rs_result.get("fallback", False)
    tag = " (broad market fallback)" if fallback else f" vs {etf}"

    if rs is not None:
        note = f"RS {rs:+.1f}%{tag} over 12w"
    else:
        note = f"RS unavailable{tag}"

    return FactorDetail("sector_rs", int(score), _WEIGHTS["sector_rs"],
                        int(score) * _WEIGHTS["sector_rs"], note)


def _factor_analyst(row: dict) -> FactorDetail:
    """Analyst conviction: bull score + month-over-month upgrade momentum."""
    ec = row.get("external_consensus") or {}
    bull = _safe_float(ec.get("bullScoreLatest") or ec.get("bull_score"))
    mom = ec.get("momChange")

    if bull is None:
        return FactorDetail(
            "analyst", 5, _WEIGHTS["analyst"],
            5 * _WEIGHTS["analyst"],
            "no analyst coverage data",
        )

    # bull score is the count of buy+strong_buy vs total analysts (0–N)
    # Normalise to 0-10: bull_pct = bull / total analysts
    total = (
        (ec.get("strongBuy") or ec.get("strong_buy") or 0) +
        (ec.get("buy") or 0) +
        (ec.get("hold") or 0) +
        (ec.get("sell") or 0) +
        (ec.get("strongSell") or ec.get("strong_sell") or 0)
    )
    bull_pct = (bull / total * 100) if total > 0 else bull  # bull is already a count

    if bull_pct >= 85:
        base, note = 9, f"bull score {bull_pct:.0f}% — near-unanimous buy"
    elif bull_pct >= 70:
        base, note = 7, f"bull score {bull_pct:.0f}% — strong buy consensus"
    elif bull_pct >= 55:
        base, note = 6, f"bull score {bull_pct:.0f}% — moderate buy bias"
    elif bull_pct >= 40:
        base, note = 5, f"bull score {bull_pct:.0f}% — mixed"
    else:
        base, note = 3, f"bull score {bull_pct:.0f}% — bearish consensus"

    # Momentum bonus: analysts turning more bullish this month
    bonus = 0
    if isinstance(mom, (int, float)):
        if mom > 0:
            bonus = 1
            note += f"; turning bullish (momChange +{mom})"
        elif mom < 0:
            bonus = -1
            note += f"; turning bearish (momChange {mom})"

    score = max(0, min(10, base + bonus))
    return FactorDetail("analyst", score, _WEIGHTS["analyst"],
                        score * _WEIGHTS["analyst"], note)


def _factor_sentiment(row: dict) -> FactorDetail:
    """News sentiment: 7-day material-only mean score."""
    ss = row.get("sentiment_summary") or {}
    mean = ss.get("mean_sentiment")
    mat_neg = ss.get("material_negative_count", 0)

    if mean is None:
        return FactorDetail(
            "sentiment", 5, _WEIGHTS["sentiment"],
            5 * _WEIGHTS["sentiment"],
            "no news coverage — neutral",
        )

    if mean >= 0.5:
        score, note = 9, f"sentiment {mean:+.2f} — strongly positive news flow"
    elif mean >= 0.2:
        score, note = 7, f"sentiment {mean:+.2f} — positive"
    elif mean >= -0.1:
        score, note = 5, f"sentiment {mean:+.2f} — neutral"
    elif mean >= -0.3:
        score, note = 3, f"sentiment {mean:+.2f} — cautious"
    else:
        score, note = 1, f"sentiment {mean:+.2f} — negative news flow"

    # Material negative articles downgrade further
    if mat_neg >= 2:
        score = max(0, score - 1)
        note += f"; {mat_neg} material negatives"

    return FactorDetail("sentiment", score, _WEIGHTS["sentiment"],
                        score * _WEIGHTS["sentiment"], note)


def _factor_valuation(row: dict) -> FactorDetail:
    """Valuation: forward P/E relative check. Cheap = bullish, expensive = cautious."""
    fund = row.get("fundamentals") or {}
    fwd_pe = _safe_float(fund.get("forward_pe"))
    legal = (fund.get("legal_type") or "").upper()
    is_etf = "ETF" in legal or "FUND" in legal

    if fwd_pe is None:
        return FactorDetail(
            "valuation", 5, _WEIGHTS["valuation"],
            5 * _WEIGHTS["valuation"],
            "no forward P/E available — neutral" if not is_etf else "ETF — no P/E",
        )

    # Different thresholds for ETFs vs stocks
    if is_etf:
        # ETF P/E is basket-weighted; typically 18-25 for broad market
        if fwd_pe < 16:
            score, note = 8, f"fwd P/E {fwd_pe:.1f} — cheap ETF"
        elif fwd_pe < 22:
            score, note = 6, f"fwd P/E {fwd_pe:.1f} — fair value"
        else:
            score, note = 4, f"fwd P/E {fwd_pe:.1f} — rich ETF"
    else:
        # Single stock — growth names deserve a premium
        if fwd_pe < 12:
            score, note = 9, f"fwd P/E {fwd_pe:.1f} — deep value"
        elif fwd_pe < 18:
            score, note = 8, f"fwd P/E {fwd_pe:.1f} — cheap"
        elif fwd_pe < 28:
            score, note = 6, f"fwd P/E {fwd_pe:.1f} — fair"
        elif fwd_pe < 45:
            score, note = 4, f"fwd P/E {fwd_pe:.1f} — premium"
        else:
            score, note = 2, f"fwd P/E {fwd_pe:.1f} — expensive"

    return FactorDetail("valuation", score, _WEIGHTS["valuation"],
                        score * _WEIGHTS["valuation"], note)


# ---------------------------------------------------------------------------
# Signal / conviction derivation
# ---------------------------------------------------------------------------

def _score_to_signal(score: float, row: dict) -> str:
    """Map COMPASS score to a trading signal, gated by range position."""
    ms = row.get("market_state") or {}
    range_pct = ms.get("range_pct") or ms.get("range_position_pct")

    if score >= 72:
        # Don't fire BUY at the very top of the 52w range
        if range_pct is not None and range_pct > 80:
            return "WATCH"  # strong stock but extended — wait for pullback
        return "BUY"
    if score >= 55:
        return "WATCH"
    if score >= 40:
        return "HOLD"
    return "TRIM"


def _score_to_conviction(score: float) -> str:
    if score >= 78:
        return "HIGH"
    if score >= 60:
        return "MEDIUM"
    return "LOW"


def _build_entry_note(
    score: float,
    signal: str,
    macro_gated: bool,
    macro_mode: int,
    row: dict,
) -> str:
    ms = row.get("market_state") or {}
    range_pct = ms.get("range_pct") or ms.get("range_position_pct")
    rsi = ms.get("rsi_14")

    parts = [f"COMPASS {score:.0f}/100"]
    if signal == "BUY":
        if range_pct is not None:
            parts.append(f"range {range_pct:.0f}th pctile")
        if rsi is not None:
            parts.append(f"RSI {rsi:.0f}")
    if macro_gated:
        parts.append("⚠ macro RED — paper only")
    elif macro_mode == 2:
        parts.append("⚠ macro AMBER — 60% size")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


__all__ = ["compute_compass_score", "CompassResult", "FactorDetail"]
