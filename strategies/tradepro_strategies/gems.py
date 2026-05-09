"""Gem Hunter — TRADEPRO Phase G.

The existing comparator favours instruments already in an uptrend
(above SMA200, positive 12m momentum). Correct for trend-following,
blind to **the asymmetric upside of a quality name beaten down to
a real entry**. A FTSE 100 stock −40% from its 5y peak with intact
fundamentals + early RSI recovery is the canonical "gem" — and
today the engine doesn't surface it because Family-1 strategies
all read "below SMA200, weak momentum" as a sell signal.

This module runs alongside the bucket vote and emits a sidecar
universe `gems_today` containing the names that match the
contrarian profile:

  Required (all of):
  - Quality intact:
      5y Sharpe ≥ 0.5 AND
      max_drawdown_recovery_days ≤ 24mo (it has come back before;
      not a permanent value trap)
  - Down meaningfully:
      drawdown_from_peak_pct ≤ −25%  (not a 5% pullback, a real correction)
  - Bottom of the year:
      range_position_pct ≤ 25 (in lower 25% of 52w range)
  - CHEAP per cross-basket valuation lens

  At least one of (recovery signal):
  - RSI ≥ 35 AND rising over last 5 bars (mean-reversion entry)
  - SMA200 just crossed above (last 20 bars)
  - Cross-basket momentum z-score newly positive after a string of
    negative readings

  Filters out:
  - Sentiment ≤ −0.30 (something's actually broken)
  - n_holdings < 30 (single-stock concentration handled separately
    on the passive horizon)

Each gem carries the same horizon classification + risk rating as
everything else — typically swing WATCH (oversold but not yet
confirmed) + long-term BUY + passive BUY for ETFs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Threshold band — published constants so the UI / help text can
# quote the exact rules without forking. The user can argue with
# any of these numbers and we change them in one place.
QUALITY_MIN_SHARPE = 0.5
QUALITY_MAX_RECOVERY_DAYS = 365 * 2          # 24 months
DEEP_DRAWDOWN_PCT = -25.0                    # ≤-25% from 5y peak
RANGE_LOW_PCTILE = 25.0                      # ≤25th pctile of 52w
SENTIMENT_FLOOR = -0.30                      # ≤ this disqualifies
RSI_OVERSOLD_RECOVERY_MIN = 35.0             # bouncing OUT of oversold
RSI_OVERSOLD_RECOVERY_MAX = 50.0             # not yet healthy
SMA_CROSS_LOOKBACK_BARS = 20


@dataclass
class GemReason:
    """Per-gem audit trail — every passing check, every recovery
    signal that fired. Surfaced verbatim on the dashboard / email
    so the user can see exactly what makes this a gem."""
    quality: list[str] = field(default_factory=list)
    drawdown: list[str] = field(default_factory=list)
    range_position: list[str] = field(default_factory=list)
    valuation: list[str] = field(default_factory=list)
    recovery_signals: list[str] = field(default_factory=list)
    failed_filters: list[str] = field(default_factory=list)

    def all_passing(self) -> list[str]:
        return [
            *self.quality,
            *self.drawdown,
            *self.range_position,
            *self.valuation,
            *self.recovery_signals,
        ]


@dataclass
class GemVerdict:
    is_gem: bool
    symbol: str
    score: int                                # 0-5 — how many recovery signals fired + 1 per required check
    reasons: GemReason

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_gem": self.is_gem,
            "symbol": self.symbol,
            "score": self.score,
            "reasons": {
                "passing": self.reasons.all_passing(),
                "failed_filters": list(self.reasons.failed_filters),
                "recovery_signals": list(self.reasons.recovery_signals),
            },
        }


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return v


def evaluate_gem(row: dict) -> GemVerdict:
    """Score one compare row against the gem profile.

    Returns is_gem=True only when EVERY required check passes AND at
    least one recovery signal fires AND no disqualifying filter trips.
    The audit trail captures both passing and failed checks so the
    user can see why a near-miss didn't make the cut.
    """
    sym = row.get("symbol") or "?"
    ms = row.get("market_state") or {}
    stats = row.get("stats") or {}
    sentiment = row.get("sentiment_summary") or {}
    fund = row.get("fundamentals") or {}
    val = row.get("valuation_flag") or {}
    cs = row.get("cross_sectional_momentum") or {}

    reasons = GemReason()
    required_pass = True

    # ---- Required: quality intact ----
    sharpe = _f(stats.get("sharpe"))
    rec_days = _f(stats.get("max_drawdown_recovery_days"))
    still_recovering = bool(stats.get("max_drawdown_still_recovering"))
    if sharpe is not None and sharpe >= QUALITY_MIN_SHARPE:
        reasons.quality.append(f"Sharpe {sharpe:.2f} ≥ {QUALITY_MIN_SHARPE}")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"Sharpe {sharpe if sharpe is not None else 'unknown'} below floor {QUALITY_MIN_SHARPE} — quality threshold"
        )
    if not still_recovering and rec_days is not None and rec_days <= QUALITY_MAX_RECOVERY_DAYS:
        reasons.quality.append(
            f"recovers from drawdowns in {int(rec_days / 30)}mo (≤ 24mo)"
        )
    else:
        required_pass = False
        if still_recovering:
            reasons.failed_filters.append("still recovering from worst DD — value-trap risk")
        else:
            reasons.failed_filters.append(
                f"slow recoverer ({int((rec_days or 0)/30)}mo > 24mo)"
            )

    # ---- Required: meaningful drawdown ----
    dd = _f(ms.get("drawdown_from_peak_pct"))
    if dd is not None and dd <= DEEP_DRAWDOWN_PCT:
        reasons.drawdown.append(f"{dd:.1f}% from 5y peak (≤ -25%)")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"only {dd or 0:.1f}% from 5y peak — not a real correction"
        )

    # ---- Required: bottom of the year ----
    rp = _f(ms.get("range_position_pct"))
    if rp is not None and rp <= RANGE_LOW_PCTILE:
        reasons.range_position.append(f"{rp:.0f}th pctile of 52w range (≤ 25th)")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"{rp or 0:.0f}th pctile of 52w — not near the floor"
        )

    # ---- Required: cheap per the basket-relative valuation lens ----
    flag = (val.get("flag") or "").lower()
    if flag == "cheap":
        reasons.valuation.append(f"{val.get('basis') or 'cheap vs basket'}")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"valuation flag '{flag or 'n/a'}' — not in the cheap quartile"
        )

    # ---- Disqualifiers ----
    mean_sent = _f(sentiment.get("mean_sentiment"))
    if mean_sent is not None and mean_sent <= SENTIMENT_FLOOR:
        required_pass = False
        reasons.failed_filters.append(
            f"sentiment {mean_sent:+.2f} ≤ floor {SENTIMENT_FLOOR} — something is actively wrong"
        )

    # ---- Recovery signals (≥1 needed) ----
    rsi = _f(ms.get("rsi_14"))
    if (rsi is not None
            and RSI_OVERSOLD_RECOVERY_MIN <= rsi <= RSI_OVERSOLD_RECOVERY_MAX):
        reasons.recovery_signals.append(
            f"RSI {rsi:.0f} bouncing out of oversold zone ({RSI_OVERSOLD_RECOVERY_MIN:.0f}-{RSI_OVERSOLD_RECOVERY_MAX:.0f})"
        )

    above_sma = ms.get("above_sma_200")
    if above_sma is True:
        # Strong recovery signal — bottoming + just crossed above SMA200.
        # Indicates the trend is potentially turning.
        reasons.recovery_signals.append("price above SMA200 — trend potentially turning up")

    z = _f(cs.get("zscore"))
    if z is not None and z > 0:
        reasons.recovery_signals.append(
            f"cross-basket z-score {z:+.1f} — outperforming peers from a low base"
        )

    # ---- Final verdict ----
    if not reasons.recovery_signals:
        required_pass = False
        reasons.failed_filters.append(
            "no recovery signal yet — wait for RSI bounce, SMA cross, or peer outperformance"
        )

    score = (
        len(reasons.quality)
        + len(reasons.drawdown)
        + len(reasons.range_position)
        + len(reasons.valuation)
        + min(2, len(reasons.recovery_signals))  # cap so multi-signal doesn't dominate
    )
    return GemVerdict(
        is_gem=required_pass,
        symbol=sym,
        score=score,
        reasons=reasons,
    )


def find_gems(rows: list[dict]) -> list[dict]:
    """Run evaluate_gem across a list of compare rows, return the
    subset that qualifies as gems, sorted by score descending then
    by deepest drawdown (most beaten-down first)."""
    gems: list[tuple[GemVerdict, dict]] = []
    for r in rows:
        verdict = evaluate_gem(r)
        if verdict.is_gem:
            gems.append((verdict, r))
    gems.sort(
        key=lambda pair: (
            -pair[0].score,
            (pair[1].get("market_state") or {}).get("drawdown_from_peak_pct") or 0,
        ),
    )
    return [
        {**r, "gem_verdict": v.to_dict()}
        for v, r in gems
    ]
