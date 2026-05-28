"""Risk rating per recommendation — TRADEPRO Phase R.

Every BUY / WAIT / AVOID signal already tells the user *what* the
engine thinks. None of them tell the user *how risky doing it is*.
A BUY on USMV (low-vol factor ETF, 12% annualised vol, recovers
quickly) and a BUY on TSLA (45% vol, multi-year recoveries) both
render as "BUY" — but the position size the reader should take is
wildly different.

This module produces a transparent rating per row:

    LOW       vol < 15% AND no escalating signals
    MEDIUM    vol 15-25% OR LOW with one escalator
    HIGH      vol 25-40% OR MEDIUM with one escalator
    EXTREME   vol > 40% OR HIGH with two escalators

Volatility is the primary driver — it sets the *baseline* tier.
Then we apply ±tier modifiers from a small set of escalators:

  +1 tier (each, capped at ±2 from baseline):
    - max-DD recovery > 3 years (slow recoverer — historical pattern)
    - ≥3 material-negative headlines in last 7d (active news risk)
    - range_position_pct ≥ 80 on a BUY (mean-reversion risk near highs)
    - cross-basket z-score absolute > 2.5 (outlier vs peers)

The output carries every contributing factor as a string list so the
UI / email / PDF / MCP can all render the same auditable rationale —
no black-box rating. A reader who sees "Risk: HIGH — vol 32%, ≥3
material-negative headlines" knows exactly which inputs drove the
verdict and which to argue with.

Optional follow-up (Phase R+): combine rating with portfolio size to
recommend a per-position cap. Not in this module — kept separate so
the rating itself stays a pure function of the row's inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Tier ladder, ordered low → high.
_TIERS: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "EXTREME")
_TIER_INDEX = {t: i for i, t in enumerate(_TIERS)}


@dataclass
class RiskRating:
    rating: str                     # LOW / MEDIUM / HIGH / EXTREME
    baseline: str                   # rating before escalators (vol-only)
    escalators: int                 # number of +1 bumps applied (capped at +2)
    factors: list[str] = field(default_factory=list)  # human-readable inputs

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating": self.rating,
            "baseline": self.baseline,
            "escalators": self.escalators,
            "factors": list(self.factors),
        }


def _baseline_from_vol(vol_30d_annual_pct: float | None) -> tuple[str, str]:
    """Vol → baseline tier + factor string. None vol falls back to MEDIUM
    (we'd rather over-warn than silently default to LOW)."""
    if vol_30d_annual_pct is None:
        return "MEDIUM", "vol unknown — treating as MEDIUM until data lands"
    v = float(vol_30d_annual_pct)
    if v < 15:
        return "LOW", f"30d annualised vol {v:.0f}% (< 15%)"
    if v < 25:
        return "MEDIUM", f"30d annualised vol {v:.0f}% (15-25%)"
    if v < 40:
        return "HIGH", f"30d annualised vol {v:.0f}% (25-40%)"
    return "EXTREME", f"30d annualised vol {v:.0f}% (≥ 40%)"


def _bump(tier: str, by: int) -> str:
    """Move a tier up or down the ladder, clamped to [LOW, EXTREME]."""
    idx = _TIER_INDEX.get(tier, 1)
    new_idx = max(0, min(len(_TIERS) - 1, idx + by))
    return _TIERS[new_idx]


def compute_risk_rating(row: dict, *, max_escalators: int = 2) -> RiskRating:
    """Score one compare row and return its risk rating + audit trail.

    Inputs read off the row (all optional — missing fields just skip
    the corresponding escalator):
      market_state.vol_30d_annual_pct   — baseline driver
      max_drawdown_recovery_days         — slow-recovery escalator
      max_drawdown_still_recovering      — same; treated as "very slow"
      sentiment_summary.material_negative_count — news-risk escalator
      bucket + market_state.range_position_pct  — near-highs escalator
      cross_sectional_momentum.zscore    — outlier escalator
    """
    ms = row.get("market_state") or {}
    stats = row.get("stats") or {}
    sentiment = row.get("sentiment_summary") or {}
    cs = row.get("cross_sectional_momentum") or {}

    baseline, vol_factor = _baseline_from_vol(ms.get("vol_30d_annual_pct"))
    factors: list[str] = [vol_factor]
    bumps = 0

    # Escalator 1: slow historical recovery from drawdowns. Captures the
    # "this thing has fallen 50% before and taken 4 years to come back"
    # case — not just the magnitude of past drawdowns but the patience
    # needed to stomach them.
    rec_days = stats.get("max_drawdown_recovery_days")
    still_recovering = bool(stats.get("max_drawdown_still_recovering"))
    if still_recovering:
        bumps += 1
        factors.append("still recovering from worst historical drawdown")
    elif isinstance(rec_days, (int, float)) and rec_days > 365 * 3:
        bumps += 1
        factors.append(
            f"slow historical recovery: {int(rec_days / 365)}y to recover from worst DD"
        )

    # Escalator 2: active news risk. Three+ material-negative headlines
    # in 7d means something is genuinely going wrong — separate from the
    # sentiment-demotion rule which already adjusts the bucket.
    mat_neg = sentiment.get("material_negative_count")
    if isinstance(mat_neg, (int, float)) and mat_neg >= 3:
        bumps += 1
        factors.append(f"{int(mat_neg)} material-negative headlines in last 7d")

    # Escalator 3: BUY near 52w highs. Mean-reversion risk — the bucket
    # may say BUY but the entry timing is asymmetric. Range guard handles
    # the price-verdict path; this catches the case where the bucket
    # promotes to BUY through strategy consensus on a high-pctile name.
    bucket = (row.get("bucket") or "").upper()
    rp = ms.get("range_position_pct")
    if bucket == "BUY" and isinstance(rp, (int, float)) and rp >= 80:
        bumps += 1
        factors.append(f"BUY at {rp:.0f}th percentile of 52w range")

    # Escalator 4: cross-basket outlier. |z| > 2.5 means the symbol is
    # ~2.5σ away from its peer group on momentum — real outlier risk,
    # both directions (extreme positive z = momentum-reversal risk;
    # extreme negative = falling-knife risk).
    z = cs.get("zscore")
    if isinstance(z, (int, float)) and abs(z) > 2.5:
        bumps += 1
        factors.append(f"cross-basket momentum outlier (z = {z:+.1f})")

    # Cap total escalation at +2 tiers from baseline so the output
    # always stays close to the volatility signal.
    capped_bumps = min(bumps, max_escalators)
    rating = _bump(baseline, capped_bumps)

    return RiskRating(
        rating=rating,
        baseline=baseline,
        escalators=capped_bumps,
        factors=factors,
    )


# ---------------------------------------------------------------------------
# Recommended position-sizing per rating (advisory, Phase R+ extension).
# Kept here so callers can opt in via `position_cap_pct(rating)` rather than
# re-deriving the heuristic each surface. Honest about being a heuristic.
# ---------------------------------------------------------------------------

_POSITION_CAPS = {
    "LOW": 25.0,
    "MEDIUM": 15.0,
    "HIGH": 8.0,
    "EXTREME": 4.0,
}


def position_cap_pct(rating: str) -> float:
    """Recommended max % of portfolio for a single position at this
    rating. Heuristic — adjust to risk appetite. Returns 0 for unknown
    ratings rather than raising."""
    return _POSITION_CAPS.get(rating, 0.0)
