"""News-catalyst signal — surface EVENT-DRIVEN names the pure-technical engine
misses (the oil-breakout case: XOM/CVX read "extended, WAIT" on Ichimoku, but
the real move is a macro catalyst).

The pieces already in the pipeline: per-symbol news + sentiment + a news_context
block (article counts today vs 30d-avg, mean sentiment, trend). Sentiment only
ever DEMOTES (negative news). This adds the missing half — a positive/negative
CATALYST flag from a news-volume SPIKE + its sentiment direction, so a
technically-quiet name with a real event gets SURFACED for discretionary review.

Deliberately does NOT auto-flip the verdict to BUY (systematic about risk,
discretionary about entry) — it attaches a flag + strength the digest/scanner
can rank/show. Cheap: derived from data already computed (no extra LLM call).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalystConfig:
    spike_ratio: float = 3.0        # today's article count ≥ this × the 30d avg = a spike
    min_articles_today: int = 3     # …and at least this many articles (avoid 1-vs-0.1 noise)
    pos_sentiment: float = 0.15     # mean sentiment ≥ this → bullish catalyst
    neg_sentiment: float = -0.15    # mean sentiment ≤ this → bearish catalyst
    baseline_floor: float = 0.3     # floor the 30d-avg so a near-zero baseline can't explode the ratio

    @staticmethod
    def from_env() -> "CatalystConfig":
        def _f(k: str, d: float) -> float:
            try: return float(os.environ.get(k, d))
            except (TypeError, ValueError): return d
        def _i(k: str, d: int) -> int:
            try: return int(os.environ.get(k, d))
            except (TypeError, ValueError): return d
        return CatalystConfig(
            spike_ratio=_f("TRADEPRO_CATALYST_SPIKE_RATIO", 3.0),
            min_articles_today=_i("TRADEPRO_CATALYST_MIN_ARTICLES", 3),
            pos_sentiment=_f("TRADEPRO_CATALYST_POS_SENTIMENT", 0.15),
            neg_sentiment=_f("TRADEPRO_CATALYST_NEG_SENTIMENT", -0.15),
            baseline_floor=_f("TRADEPRO_CATALYST_BASELINE_FLOOR", 0.3),
        )


@dataclass(frozen=True)
class CatalystSignal:
    active: bool
    direction: str          # "bullish" | "bearish" | "neutral" | "none"
    strength: float         # 0..~ (spike_ratio × |sentiment|); 0 when inactive
    spike_ratio: float
    articles_today: int
    reason: str

    def to_dict(self) -> dict:
        return {
            "active": self.active, "direction": self.direction,
            "strength": round(self.strength, 2), "spike_ratio": round(self.spike_ratio, 1),
            "articles_today": self.articles_today, "reason": self.reason,
        }


def detect_news_catalyst(
    *,
    articles_today: int | None,
    articles_30d_avg: float | None,
    mean_sentiment: float | None,
    cfg: CatalystConfig,
) -> CatalystSignal:
    """A news-volume SPIKE + its sentiment direction = a catalyst. Inactive
    (direction='none') when there's no spike or the inputs are missing — never
    fabricate a catalyst from thin data."""
    at = int(articles_today or 0)
    # No REAL 30d baseline → we cannot tell a spike from normal coverage, so we
    # NEVER fabricate one (feedback_no_false_positives). A fabricated baseline
    # made every name with a few recent articles look like a catalyst. Inactive
    # until a genuine per-ticker news-volume history exists.
    if articles_30d_avg is None:
        return CatalystSignal(False, "none", 0.0, 0.0, at, "no news-volume baseline")
    base = max(float(articles_30d_avg or 0.0), cfg.baseline_floor)
    ratio = at / base if base > 0 else 0.0
    if at < cfg.min_articles_today or ratio < cfg.spike_ratio:
        return CatalystSignal(False, "none", 0.0, ratio, at, "no news spike")
    s = mean_sentiment if mean_sentiment is not None else 0.0
    if s >= cfg.pos_sentiment:
        direction = "bullish"
    elif s <= cfg.neg_sentiment:
        direction = "bearish"
    else:
        direction = "neutral"   # a spike with flat sentiment — event, unclear direction
    strength = ratio * (abs(s) if direction != "neutral" else 0.25)
    reason = (
        f"news spike: {at} articles today vs {base:.1f} 30d-avg ({ratio:.1f}×), "
        f"sentiment {s:+.2f} → {direction} catalyst"
    )
    return CatalystSignal(True, direction, strength, ratio, at, reason)
