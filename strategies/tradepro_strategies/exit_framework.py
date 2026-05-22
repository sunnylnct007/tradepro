"""Intraday + swing exit framework.

Implements the mandatory exit triad from
IMPROVEMENT_SUGGESTIONS_v1.md §3 + SIGNAL_CARD_SPEC_v1.md §3.4:

  stop_loss   — P × (1 − stop_pct) or P − (ATR_14 × multiplier)
  take_profit — P × (1 + target_pct), with RR ≥ 2.0 as the floor
  time_exit   — market_close − 15min (intraday only; not enforced yet)

Plus the position-sizing helper that converts a stop distance into a
suggested share count + notional, in both USD and GBP (the user's
home currency).

ATR_14 is already on `market_state` so no new data feed needed — the
helpers read it through the row payload. When ATR isn't available
the helpers fall back to fixed-percent stops per strategy_type.

Pure functions — no side effects, no row mutation. Tested in
features/exit_framework.feature so the contract is auditable
independent of the compare flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# Defaults per strategy_type from SIGNAL_CARD_SPEC_v1.md §3.1. Used
# only when ATR isn't available — ATR-adjusted is preferred because
# 1.5% on JNJ is too wide while the same 1.5% on APLD is too tight.
_FIXED_PCT_DEFAULTS: dict[str, tuple[float, float]] = {
    # strategy_type → (stop_pct, target_pct)
    "momentum":       (0.015, 0.030),   # 1.5% / 3.0%
    "mean_reversion": (0.010, 0.020),   # 1.0% / 2.0% — RSI-tight
    "event_driven":   (0.020, 0.040),   # 2.0% / 4.0% — catalyst can shock
    "relative_value": (0.015, 0.030),   # mirror momentum
    "index_rebalance":(0.015, 0.030),
    "liquidity_hunt": (0.025, 0.050),   # high-vol names
}


# Minimum reward/risk ratio — the floor from §3.1. Any strategy with a
# worse than 1:2 ratio needs a >50% win rate to be profitable, so we
# refuse to surface it as an entry recommendation.
RR_FLOOR = 2.0


# Default ATR multiplier for stop distance per SIGNAL_CARD_SPEC §3.2.
DEFAULT_ATR_MULTIPLIER = 1.5


@dataclass
class ExitLevels:
    """The output of compute_exit_levels(). Maps 1:1 to the `exit`
    block on the signal card (SIGNAL_CARD_SPEC §3) with `stop_loss`
    and `take_profit` sub-objects."""
    stop_loss: float
    stop_distance_pct: float
    take_profit: float
    target_distance_pct: float
    rr_ratio: float
    method: Literal["ATR_ADJUSTED", "FIXED_PCT"]
    atr_14: float | None
    atr_multiplier: float | None

    def to_dict(self) -> dict:
        sl: dict = {
            "price": self.stop_loss,
            "distance_pct": round(self.stop_distance_pct * 100, 3),
            "method": self.method,
            "type": "STOP",
            "tif": "GTC",
        }
        tp: dict = {
            "price": self.take_profit,
            "distance_pct": round(self.target_distance_pct * 100, 3),
            "method": self.method,
            "rr_ratio": round(self.rr_ratio, 3),
            "type": "LIMIT",
            "tif": "GTC",
        }
        if self.method == "ATR_ADJUSTED":
            sl["atr_14"] = self.atr_14
            sl["atr_multiplier"] = self.atr_multiplier
        return {
            "stop_loss": sl,
            "take_profit": tp,
            "trailing_stop": None,
            "time_exit": None,
        }


def compute_exit_levels(
    *,
    entry_price: float,
    atr_14: float | None,
    strategy_type: str | None,
    atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
    rr_ratio: float = RR_FLOOR,
) -> ExitLevels | None:
    """Compute stop_loss + take_profit for a long entry. Returns None
    if entry_price isn't a positive number (no signal to size against).

    ATR-adjusted is preferred — ATR naturally scales the stop to the
    instrument's realised volatility (per SIGNAL_CARD_SPEC §3.2). When
    ATR isn't available, fall back to a fixed-percent stop selected
    by strategy_type. Unknown strategy_type falls back to momentum
    defaults, since 5 of the 7 in-tree strategies are momentum.

    rr_ratio is the take_profit / stop multiple. Default 2.0 — the
    enforced floor; lower values are still computed but the row
    should fail gate_check_rr() before display.
    """
    try:
        entry = float(entry_price)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None

    if atr_14 is not None and atr_14 > 0:
        stop_distance = atr_14 * atr_multiplier
        target_distance = stop_distance * rr_ratio
        return ExitLevels(
            stop_loss=round(entry - stop_distance, 4),
            stop_distance_pct=stop_distance / entry,
            take_profit=round(entry + target_distance, 4),
            target_distance_pct=target_distance / entry,
            rr_ratio=rr_ratio,
            method="ATR_ADJUSTED",
            atr_14=atr_14,
            atr_multiplier=atr_multiplier,
        )

    stop_pct, target_pct = _FIXED_PCT_DEFAULTS.get(
        strategy_type or "", _FIXED_PCT_DEFAULTS["momentum"]
    )
    return ExitLevels(
        stop_loss=round(entry * (1 - stop_pct), 4),
        stop_distance_pct=stop_pct,
        take_profit=round(entry * (1 + target_pct), 4),
        target_distance_pct=target_pct,
        rr_ratio=target_pct / stop_pct if stop_pct > 0 else 0.0,
        method="FIXED_PCT",
        atr_14=None,
        atr_multiplier=None,
    )


def gate_check_rr(exit_levels: ExitLevels | None, *, floor: float = RR_FLOOR) -> tuple[bool, str | None]:
    """Pre-trade gate: pass only if RR ≥ floor (default 2.0). Returns
    (passed, reason_if_failed). The intraday pre-trade gate calls this
    before allowing auto-place; failing rows should still show up but
    flagged as below-floor."""
    if exit_levels is None:
        return False, "no exit levels computed"
    if exit_levels.rr_ratio < floor:
        return False, (
            f"RR {exit_levels.rr_ratio:.2f}× below floor {floor:.1f}× — "
            f"need >50% win rate to be net positive at this ratio."
        )
    return True, None


@dataclass
class PositionSizing:
    """Output of compute_position_sizing(). Maps to the `sizing` block
    on the signal card (SIGNAL_CARD_SPEC §3)."""
    suggested_shares: int
    suggested_notional_usd: float
    suggested_notional_gbp: float
    max_loss_gbp: float
    stop_distance_usd: float
    stop_distance_gbp: float
    account_size_gbp: float
    risk_per_trade_pct: float
    fx_rate_gbpusd: float

    def to_dict(self) -> dict:
        return {
            "account_size_gbp": round(self.account_size_gbp, 2),
            "risk_per_trade_pct": round(self.risk_per_trade_pct * 100, 3),
            "max_loss_gbp": round(self.max_loss_gbp, 2),
            "stop_distance_usd": round(self.stop_distance_usd, 4),
            "stop_distance_gbp": round(self.stop_distance_gbp, 4),
            "fx_rate_gbpusd": round(self.fx_rate_gbpusd, 4),
            "suggested_shares": self.suggested_shares,
            "suggested_notional_usd": round(self.suggested_notional_usd, 2),
            "suggested_notional_gbp": round(self.suggested_notional_gbp, 2),
        }


def compute_position_sizing(
    *,
    entry_price_usd: float,
    stop_distance_usd: float,
    account_size_gbp: float,
    risk_per_trade_pct: float = 0.01,
    fx_rate_gbpusd: float = 1.27,
) -> PositionSizing | None:
    """Derive position size from the stop distance, never the reverse.

      max_loss_gbp     = account_size_gbp × risk_per_trade_pct
      stop_distance_gbp = stop_distance_usd / fx_rate_gbpusd
      suggested_shares = floor(max_loss_gbp / stop_distance_gbp)

    Per SIGNAL_CARD_SPEC §3.3: "Compute and show suggested_shares +
    suggested_notional with every signal — never ask the user to do
    the math." Returns None if inputs are unworkable (zero stop
    distance, zero account, etc.) — caller surfaces sizing=None on
    the card."""
    try:
        entry = float(entry_price_usd)
        stop_dist = float(stop_distance_usd)
        account = float(account_size_gbp)
        risk_pct = float(risk_per_trade_pct)
        fx = float(fx_rate_gbpusd)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or stop_dist <= 0 or account <= 0 or risk_pct <= 0 or fx <= 0:
        return None

    max_loss_gbp = account * risk_pct
    stop_distance_gbp = stop_dist / fx
    shares = int(max_loss_gbp // stop_distance_gbp)
    if shares <= 0:
        return None
    notional_usd = shares * entry
    notional_gbp = notional_usd / fx
    return PositionSizing(
        suggested_shares=shares,
        suggested_notional_usd=notional_usd,
        suggested_notional_gbp=notional_gbp,
        max_loss_gbp=max_loss_gbp,
        stop_distance_usd=stop_dist,
        stop_distance_gbp=stop_distance_gbp,
        account_size_gbp=account,
        risk_per_trade_pct=risk_pct,
        fx_rate_gbpusd=fx,
    )
