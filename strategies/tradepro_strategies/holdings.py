"""Phase-2 starter: portfolio-aware action engine.

Combines a holding's state (qty + avg cost + current price + days
held) with today's per-symbol verdict (bucket + swing composite +
market_state) to produce a concrete action recommendation:

    BUY_MORE   the position is below cost, the structural thesis
               is intact, and momentum/RSI shape an average-down
               opportunity. Includes the new average cost basis if
               the user adds a tranche of equal size.

    TRIM       the position is in profit AND either the structural
               thesis is fading (bucket flipped to AVOID, swing
               composite ≤ 1) OR the symbol is in a take-profit
               zone (RSI overbought + well above cost).

    HOLD       no fresh edge to act. Default when neither
               average-down nor take-profit conditions fire.

Each recommendation carries a narrative string a user can read
straight off the email digest, plus an `evidence` list of citable
factors so the rationale layer / verifier can prove the prose
came from the same data.

Caveat: this is the FIRST cut. Horizon-weighted strategy mix
(6mo weights MACD/RSI heavily; 5y weights buy-and-hold +
Sharpe heavily) is parked for the next iteration once T212
positions carry a horizon flag (today they don't).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Zone thresholds — calibrated for the default 1y horizon. The
# horizon profile dict below shifts these tighter (short horizon →
# react faster) or looser (long horizon → ride out noise) so a 5y
# investor doesn't get TRIM signals on a single overbought RSI day
# while a 6mo trader doesn't sit through every drawdown.
AVG_DOWN_LOSS_PCT = -3.0      # position must be down at least 3% before "average down" makes sense
TAKE_PROFIT_GAIN_PCT = 15.0   # only call "take profits" when up 15%+


# Per-horizon threshold profile. Picked at evaluate-time from the
# horizon arg. Keys:
#   trim_rsi_min  — RSI threshold for the take-profit zone
#   avg_down_rsi  — RSI threshold for the average-down zone
#   intact_swing  — minimum swing total for "structurally intact"
#   broken_swing  — maximum swing total for "structurally broken"
#   tolerate_wait — when True, WAIT bucket on a position doesn't
#                   block average-down (long-term holders look
#                   through short-term WAITs)
HORIZON_PROFILES: dict[str, dict] = {
    "6mo": {
        "trim_rsi_min": 60.0,
        "avg_down_rsi": 35.0,
        "intact_swing": 5,         # need a stronger composite to add aggressively
        "broken_swing": 2,         # quicker to trim
        "tolerate_wait": False,
    },
    "1y": {
        "trim_rsi_min": 65.0,
        "avg_down_rsi": 35.0,
        "intact_swing": 4,
        "broken_swing": 1,
        "tolerate_wait": False,
    },
    "3y": {
        "trim_rsi_min": 75.0,
        "avg_down_rsi": 30.0,
        "intact_swing": 3,
        "broken_swing": 1,
        "tolerate_wait": True,
    },
    "5y": {
        "trim_rsi_min": 80.0,
        "avg_down_rsi": 30.0,
        "intact_swing": 2,
        "broken_swing": 0,
        "tolerate_wait": True,
    },
}
DEFAULT_HORIZON = "1y"


@dataclass
class HoldingRecommendation:
    symbol: str
    action: str                       # BUY_MORE / HOLD / TRIM
    narrative: str                    # plain-English headline (≤2 sentences)
    horizon: str = DEFAULT_HORIZON    # which profile drove the decision
    evidence: list[str] = field(default_factory=list)
    avg_cost_after_equal_tranche: float | None = None  # only set when BUY_MORE

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "narrative": self.narrative,
            "horizon": self.horizon,
            "evidence": list(self.evidence),
            "avg_cost_after_equal_tranche": self.avg_cost_after_equal_tranche,
        }


def analyse_holding(
    holding: dict,
    row: dict | None,
    *,
    horizon: str = DEFAULT_HORIZON,
) -> HoldingRecommendation:
    """Combine position state with today's verdict.

    `holding` shape (matches the T212 positions endpoint output):
        ticker / instrumentName / quantity / averagePricePaid /
        currentPrice / unrealisedPct / unrealisedAbs / yahooSymbol

    `row` shape: best-rank compare row for the symbol, or None when
    the symbol isn't in any tracked universe.
    """
    sym = (holding.get("yahooSymbol")
           or holding.get("ticker")
           or holding.get("instrumentName")
           or "—")
    upct = _safe(holding.get("unrealisedPct"))
    avg_cost = _safe(holding.get("averagePricePaid"))
    current = _safe(holding.get("currentPrice"))
    qty = _safe(holding.get("quantity"))

    # Resolve horizon profile — unknown values fall back to the
    # default so a stray flag doesn't break analysis.
    profile = HORIZON_PROFILES.get(horizon) or HORIZON_PROFILES[DEFAULT_HORIZON]
    horizon_used = horizon if horizon in HORIZON_PROFILES else DEFAULT_HORIZON

    # When there's no current verdict at all, fall back to neutral —
    # the email already shows a "run evaluate_symbols" prompt for these.
    if row is None:
        return HoldingRecommendation(
            symbol=sym,
            action="HOLD",
            horizon=horizon_used,
            narrative=(
                "Not in any tracked universe — no action recommendation; "
                "run evaluate_symbols(\"{}\") for an ad-hoc verdict."
            ).format(sym),
            evidence=[],
        )

    bucket = (row.get("bucket") or "").upper()
    swing = (row.get("swing_score") or {})
    swing_total = swing.get("total")
    ms = row.get("market_state") or {}
    rsi = _safe(ms.get("rsi_14"))
    above_sma = ms.get("above_sma_200")

    # ---- Zone detection (horizon-weighted) ----
    in_avg_down_zone = (
        upct is not None and upct <= AVG_DOWN_LOSS_PCT
        and rsi is not None and rsi <= profile["avg_down_rsi"]
    )
    in_take_profit_zone = (
        upct is not None and upct >= TAKE_PROFIT_GAIN_PCT
        and rsi is not None and rsi >= profile["trim_rsi_min"]
    )
    structurally_intact = (
        bucket in ("BUY",)
        or (profile["tolerate_wait"] and bucket == "WAIT")
        or (swing_total is not None and swing_total >= profile["intact_swing"])
    )
    structurally_broken = (
        bucket == "AVOID"
        or (swing_total is not None and swing_total <= profile["broken_swing"])
    )

    evidence: list[str] = [f"horizon {horizon_used}"]
    if upct is not None:
        evidence.append(f"position {upct:+.2f}% vs cost")
    if rsi is not None:
        evidence.append(f"RSI {rsi:.0f}")
    if above_sma is True:
        evidence.append("above 200d SMA")
    elif above_sma is False:
        evidence.append("below 200d SMA")
    if bucket:
        evidence.append(f"bucket {bucket}")
    if swing_total is not None:
        evidence.append(
            f"swing {swing_total}/8 ({swing.get('verdict', '?')})"
        )

    # ---- Action selection (priority order matters) ----
    # 1. Structurally broken + in profit → TRIM (lock in gains before trend gives back)
    if structurally_broken and upct is not None and upct > 0:
        narrative = (
            f"{sym} is in profit ({upct:+.1f}%) but the system says "
            f"AVOID / swing {swing_total}/8 — consider trimming before "
            f"the trend gives back what you've earned."
        )
        return HoldingRecommendation(
            symbol=sym, action="TRIM", narrative=narrative,
            horizon=horizon_used, evidence=evidence,
        )

    # 2. Take-profit zone → TRIM regardless of structural state
    if in_take_profit_zone:
        narrative = (
            f"{sym} is up {upct:+.1f}% with RSI {rsi:.0f} — both signals "
            f"suggest momentum is overextended. Consider trimming a partial "
            f"position to lock in gains."
        )
        return HoldingRecommendation(
            symbol=sym, action="TRIM", narrative=narrative,
            horizon=horizon_used, evidence=evidence,
        )

    # 3. Average-down zone + thesis intact → BUY_MORE
    if in_avg_down_zone and structurally_intact:
        avg_after = None
        if (avg_cost is not None and avg_cost > 0
                and current is not None and current > 0):
            # Add an equal-size tranche at current price → straight average.
            avg_after = (avg_cost + current) / 2.0
        narrative_parts = [
            f"{sym} is down {upct:+.1f}% with RSI {rsi:.0f} (oversold zone) "
            f"and the structural thesis is intact (bucket {bucket}, swing "
            f"{swing_total}/8). Classic average-down opportunity."
        ]
        if avg_after is not None and avg_cost is not None:
            ccy = holding.get("currency") or ""
            narrative_parts.append(
                f"Adding an equal tranche at {current:.2f} {ccy} would bring "
                f"your cost basis from {avg_cost:.2f} to {avg_after:.2f}."
            )
        return HoldingRecommendation(
            symbol=sym, action="BUY_MORE",
            narrative=" ".join(narrative_parts),
            horizon=horizon_used,
            evidence=evidence,
            avg_cost_after_equal_tranche=avg_after,
        )

    # 4. Default → HOLD
    qual = ""
    if bucket == "BUY" and upct is not None and upct > 0:
        qual = "in profit and the system still rates BUY — let it run."
    elif bucket == "WAIT":
        qual = "system says WAIT — don't add until trend confirms."
    elif bucket == "AVOID" and upct is not None and upct <= 0:
        qual = (
            "system says AVOID and you're at break-even or worse — "
            "consider whether the structural thesis still holds before "
            "averaging into a damaged position."
        )
    else:
        qual = "no fresh edge in either direction; hold the line."
    narrative = f"{sym}: {qual}"
    return HoldingRecommendation(
        symbol=sym, action="HOLD", narrative=narrative,
        horizon=horizon_used, evidence=evidence,
    )


def _safe(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math
    if math.isnan(f) or math.isinf(f):
        return None
    return f
