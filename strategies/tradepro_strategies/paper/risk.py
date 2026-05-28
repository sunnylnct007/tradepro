"""Pre-trade and continuous risk checks.

Two scopes of risk live here:

  Pre-trade  — does this candidate order fit the strategy's risk
               envelope? Position-size cap, max open positions,
               max position % of capital, short-allowed flag. The
               engine calls `RiskLimits.check_order(order, ctx)`
               between strategy.on_bar() and OrderRouter.submit().

  Continuous — has the strategy busted its daily-loss or drawdown
               cap? When it has, the engine sets `halted=True` and
               stops calling `on_bar` until session_end (or operator
               intervention in live mode).

What's intentionally NOT here yet:
  - Cross-strategy risk (correlation, concentration). That belongs
    in a Ledger-level check across all strategies' positions.
  - Margin / buying-power. IBKR enforces this at the broker; we
    surface their reject as a fill failure, not duplicate the math.
  - Volatility-scaled sizing. A later refinement — for now the
    strategy decides quantity and risk just gates it.

RiskCheckResult is a structured (ok, reason) rather than a bool +
exception because order rejections need to land in the audit trail
with a human reason. "Rejected: max_position_value 50000 < 62500"
makes a daily review usable; an exception trace doesn't.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .strategy import Order, Position


@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of a risk gate. `ok=True` means the order passes;
    `ok=False` carries a one-line `reason` for the audit log.
    `code` is a short machine-readable tag callers can pivot on."""
    ok: bool
    reason: str = ""
    code: str = ""

    @classmethod
    def pass_(cls) -> "RiskCheckResult":
        return cls(ok=True)

    @classmethod
    def fail(cls, code: str, reason: str) -> "RiskCheckResult":
        return cls(ok=False, code=code, reason=reason)


@dataclass
class RiskLimits:
    """Per-strategy risk envelope. All limits are off (None) by
    default — engine treats None as "no limit on this dimension"
    so a half-configured RiskLimits still works.

    Loss caps are recorded against `daily_pnl` which the engine
    pushes in after every fill. Strategies don't manipulate this
    field directly."""

    # Position sizing ------------------------------------------------
    max_position_value_usd: float | None = None
    """Hard cap on |position_value| at submission. A 100-share order
    on a $500 stock = $50k; max_position_value_usd=40_000 rejects."""

    max_position_pct_of_capital: float | None = None
    """Soft cap as a fraction of the strategy's allocated capital.
    `0.15` = no single position above 15% of the sub-account.
    Engine passes capital in via `RiskContext` so this stays
    portable across paper sub-accounts of different sizes."""

    max_open_positions: int | None = None
    """How many concurrent symbols the strategy is allowed to be
    in. Prevents the "10-symbol scalper accidentally went into 50
    symbols at once" failure mode."""

    allow_short: bool = False
    """When False, any order that would open or extend a short
    position rejects. v1 default = long-only."""

    # Loss caps -------------------------------------------------------
    max_daily_loss_usd: float | None = None
    """Halt the strategy for the rest of the session when realised
    + unrealised P&L is below `-max_daily_loss_usd`. Engine resets
    daily_pnl at session_start."""

    max_drawdown_pct: float | None = None
    """Halt when the strategy's equity falls more than this fraction
    below its peak equity since session_start. Applied to live
    equity, not just realised. 0.04 = 4% drawdown."""

    # Internal state --------------------------------------------------
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    halted_at: datetime | None = None


@dataclass
class RiskContext:
    """What the engine knows when it calls a risk check.

    `current_positions` is the strategy's open positions BEFORE
    applying the new order; the gate adds the candidate order's
    effect itself."""
    strategy_capital_usd: float
    mark_price: float
    current_positions: dict[str, "Position"] = field(default_factory=dict)
    now: datetime | None = None


def check_order(
    order: "Order",
    limits: RiskLimits,
    ctx: RiskContext,
) -> RiskCheckResult:
    """Single entry-point a strategy's order goes through. Returns
    pass / structured failure. Pure — no side effects.

    Order of checks matters: halt first (cheapest, terminates fast),
    then sizing (most common rejection), then concurrency. Each
    check returns on first failure so the reason string is unambiguous.
    """
    from .strategy import OrderSide  # local import to avoid circularity at module load

    if limits.halted:
        return RiskCheckResult.fail(
            "halted",
            f"Strategy is halted: {limits.halt_reason}",
        )

    # Sizing — only meaningful for opening / extending orders, not
    # for flattening (a flatten is always allowed; the whole point
    # is to reduce risk).
    pos = ctx.current_positions.get(order.symbol)
    new_qty_signed = _projected_qty(pos, order)
    new_position_value = abs(new_qty_signed) * ctx.mark_price

    if limits.max_position_value_usd is not None:
        if new_position_value > limits.max_position_value_usd:
            return RiskCheckResult.fail(
                "max_position_value",
                f"max_position_value_usd {limits.max_position_value_usd:.0f} "
                f"< projected position value {new_position_value:.0f}",
            )

    if limits.max_position_pct_of_capital is not None:
        pct = new_position_value / max(1.0, ctx.strategy_capital_usd)
        if pct > limits.max_position_pct_of_capital:
            return RiskCheckResult.fail(
                "max_position_pct",
                f"max_position_pct_of_capital {limits.max_position_pct_of_capital:.2%} "
                f"< projected {pct:.2%}",
            )

    # Concurrency — count strategies' OPEN positions (post-order).
    if limits.max_open_positions is not None:
        post_open = _projected_open_count(ctx.current_positions, order, new_qty_signed)
        if post_open > limits.max_open_positions:
            return RiskCheckResult.fail(
                "max_open_positions",
                f"max_open_positions {limits.max_open_positions} "
                f"< projected open positions {post_open}",
            )

    # Long-only guard.
    if not limits.allow_short and new_qty_signed < 0:
        return RiskCheckResult.fail(
            "short_disallowed",
            f"allow_short=False; this {order.side.value} would open / extend "
            f"a short of {new_qty_signed} shares",
        )

    return RiskCheckResult.pass_()


def update_pnl_and_check_halt(
    limits: RiskLimits,
    realised_pnl_delta: float,
    unrealised_pnl: float,
    now: datetime,
) -> None:
    """Engine calls this after every fill + on a heartbeat between
    bars. Updates the running P&L tally and trips halt flags when
    a cap is busted.

    Equity tracking is simple: peak_equity ratchets up only,
    drawdown is (peak - current) / peak. Resets at session_start."""
    limits.daily_pnl += realised_pnl_delta
    current_equity = limits.daily_pnl + unrealised_pnl
    if current_equity > limits.peak_equity:
        limits.peak_equity = current_equity

    if limits.halted:
        return
    if (
        limits.max_daily_loss_usd is not None
        and current_equity < -limits.max_daily_loss_usd
    ):
        limits.halted = True
        limits.halt_reason = (
            f"daily P&L {current_equity:.2f} < -max_daily_loss_usd "
            f"{limits.max_daily_loss_usd:.2f}"
        )
        limits.halted_at = now
        return
    if (
        limits.max_drawdown_pct is not None
        and limits.peak_equity > 0
        and (limits.peak_equity - current_equity) / limits.peak_equity
        > limits.max_drawdown_pct
    ):
        limits.halted = True
        limits.halt_reason = (
            f"drawdown "
            f"{(limits.peak_equity - current_equity) / limits.peak_equity:.2%} "
            f"> max_drawdown_pct {limits.max_drawdown_pct:.2%}"
        )
        limits.halted_at = now


def reset_for_new_session(limits: RiskLimits) -> None:
    """Engine calls at on_session_start. Loss caps are intraday by
    design — overnight losses are tracked by the Ledger, not by the
    per-session RiskLimits."""
    limits.daily_pnl = 0.0
    limits.peak_equity = 0.0
    limits.halted = False
    limits.halt_reason = ""
    limits.halted_at = None


# ---- Internal helpers ----------------------------------------------

def _projected_qty(pos: "Position | None", order: "Order") -> int:
    """Signed quantity after the candidate order fills. BUY adds,
    SELL subtracts. Handles the no-position-yet case as zero."""
    from .strategy import OrderSide
    current = pos.quantity if pos else 0
    delta = order.quantity if order.side == OrderSide.BUY else -order.quantity
    return current + delta


def _projected_open_count(
    current_positions: dict[str, "Position"],
    order: "Order",
    new_qty_signed: int,
) -> int:
    """How many symbols would be non-flat after this order fills.
    Excludes the order's symbol from current_positions (using the
    projected new qty for it instead) so we don't double-count."""
    other_open = sum(
        1
        for s, p in current_positions.items()
        if s != order.symbol and p.quantity != 0
    )
    return other_open + (1 if new_qty_signed != 0 else 0)
