"""Options Desk RISK ENGINE — the non-negotiable spine of the wheel.

Every options order MUST pass `evaluate()` before it can be routed. This is the
machine-readable form of the BRD's risk framework (§7 regime gating, §8
falling-knife block, §9 capital + pre-trade checks + drawdown brakes).

Two design rules, both load-bearing:

  1. NO FALSE POSITIVES. A gate whose INPUT is missing/None is treated as a
     BLOCK ("could-not-verify"), never silently passed. We do not sell premium
     on data we couldn't check. (feedback_no_false_positives.)
  2. NO-TRADE IS A VALID OUTCOME. `allowed=False` with a list of block reasons
     is a normal, logged decision — not an error. (BRD §3, §10.5.)

Pure + dependency-free (no DB, no network, no clock) so the whole risk contract
is unit-testable in isolation — the caller fetches the chain/regime/portfolio
numbers and hands them in. Mirrors shared_verdict / bar_cache.quality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Regime(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


class Structure(str, Enum):
    CASH_SECURED_PUT = "CASH_SECURED_PUT"      # wheel leg 1
    COVERED_CALL = "COVERED_CALL"              # wheel leg 2
    WHEEL = "WHEEL"                            # full wheel initiation
    BULL_PUT_SPREAD = "BULL_PUT_SPREAD"        # defined-risk short put
    PROTECTIVE_PUT = "PROTECTIVE_PUT"          # hedge owned stock
    COLLAR = "COLLAR"                          # hedge owned stock
    BEAR_PUT_SPREAD = "BEAR_PUT_SPREAD"        # bearish defined-risk


# §7 — permitted structures by regime. The PRIMARY control: wrong structure in
# the wrong regime is the #1 way a premium-seller blows up.
PERMITTED_BY_REGIME: dict[Regime, frozenset[Structure]] = {
    Regime.GREEN: frozenset({
        Structure.CASH_SECURED_PUT, Structure.COVERED_CALL,
        Structure.WHEEL, Structure.BULL_PUT_SPREAD,
    }),
    Regime.YELLOW: frozenset({
        Structure.CASH_SECURED_PUT, Structure.COVERED_CALL,  # reduced size (brake/size handles)
        Structure.BULL_PUT_SPREAD,
    }),
    Regime.ORANGE: frozenset({
        Structure.BULL_PUT_SPREAD, Structure.PROTECTIVE_PUT, Structure.COLLAR,
    }),
    Regime.RED: frozenset({
        Structure.BEAR_PUT_SPREAD, Structure.COLLAR,
    }),
}

# Structures that OPEN short-premium / wheel exposure — blocked on a falling
# knife regardless of how rich the IV looks (§8: high IV on distress is a
# warning, not an invitation).
WHEEL_ENTRY_STRUCTURES: frozenset[Structure] = frozenset({
    Structure.CASH_SECURED_PUT, Structure.WHEEL,
})

# Structures that SELL premium (need an IV-rank edge — §4, §5.3 vega rule).
SHORT_PREMIUM_STRUCTURES: frozenset[Structure] = frozenset({
    Structure.CASH_SECURED_PUT, Structure.COVERED_CALL,
    Structure.WHEEL, Structure.BULL_PUT_SPREAD,
})


@dataclass(frozen=True)
class OptionsRiskConfig:
    """Config-driven risk knobs (BRD defaults; UI-editable per the config screen)."""
    # Greek entry gates (§9.2)
    delta_min: float = 0.20
    delta_max: float = 0.35
    dte_min: int = 25
    dte_max: int = 50
    iv_rank_min: float = 30.0          # %
    # Liquidity gates (§6.1 filter 1, §9.2)
    oi_min: int = 250          # per-strike OI floor. 1,000 was index-level and
    #   rejected every single-name equity strike (KO/F/INTC near-month strikes
    #   run 50–500 OI); 250 is enough for 1-lot paper/wheel fills without bad
    #   slippage. Raise for size.
    spread_max_usd: float = 0.10
    # Capital rules (§9.1) — GBP. Pot ≈ £12k with ~£10k deployable (the trader's
    # stated capacity); a single position may use the full deploy so mid-priced
    # quality names (e.g. a $76 strike ≈ £6k cash-secured) aren't blocked on
    # notional. Names too expensive to cash-secure within £10k (e.g. a $200 strike
    # ≈ £16k) still correctly block — that's affordability, not a tunable.
    pot_gbp: float = 12000.0
    max_deploy_gbp: float = 10000.0
    per_position_gbp: float = 10000.0
    max_positions: int = 2
    # Drawdown brakes (§9.5) — cumulative realised LOSS in GBP (positive number)
    brake1_alert_gbp: float = 500.0
    brake2_reduce_gbp: float = 1000.0
    brake3_no_new_gbp: float = 1500.0
    circuit_breaker_gbp: float = 2500.0

    @staticmethod
    def from_env() -> "OptionsRiskConfig":
        """Env-tunable CAPITAL sizing so the wheel sleeve can match the account.
        The £12k pot / £10k per-position defaults were the trader's ORIGINAL
        stated capacity — a larger account should deploy more (a £17k JNJ
        contract blocks only because it doesn't fit a £12k pot). Set
        TRADEPRO_WHEEL_POT_GBP / _MAX_DEPLOY_GBP / _PER_POSITION_GBP /
        _MAX_POSITIONS to raise it. Greek + liquidity gates keep their BRD
        defaults (feedback_config_driven_no_hardcoding)."""
        import os as _os
        from dataclasses import replace as _replace

        def _f(k: str, cur: float) -> float:
            try:
                return float(_os.environ[k])
            except (KeyError, TypeError, ValueError):
                return cur

        def _i(k: str, cur: int) -> int:
            try:
                return int(_os.environ[k])
            except (KeyError, TypeError, ValueError):
                return cur

        d = OptionsRiskConfig()
        return _replace(
            d,
            pot_gbp=_f("TRADEPRO_WHEEL_POT_GBP", d.pot_gbp),
            max_deploy_gbp=_f("TRADEPRO_WHEEL_MAX_DEPLOY_GBP", d.max_deploy_gbp),
            per_position_gbp=_f("TRADEPRO_WHEEL_PER_POSITION_GBP", d.per_position_gbp),
            max_positions=_i("TRADEPRO_WHEEL_MAX_POSITIONS", d.max_positions),
        )


@dataclass(frozen=True)
class TradeCandidate:
    symbol: str
    structure: Structure
    # Per-contract greeks/economics (None = not computed → will BLOCK)
    abs_delta: float | None = None         # |delta| of the short leg
    dte: int | None = None
    strike: float | None = None            # USD
    contracts: int = 1
    notional_gbp: float | None = None      # capital at risk for this trade (GBP)


@dataclass(frozen=True)
class MarketContext:
    """Per-underlying market state at decision time. None on a required field =
    could-not-verify → BLOCK (no false positives)."""
    regime: Regime | None = None
    falling_knife: bool | None = None      # §8 detector result
    iv_rank: float | None = None           # %, from 52w IV history
    open_interest: int | None = None       # near-month OI at the strike
    bid_ask_spread_usd: float | None = None
    earnings_in_expiry_window: bool | None = None   # §9.4 blackout
    ex_div_in_expiry_window: bool | None = None      # §9.4 (covered calls)
    data_fresh: bool = True                # chain/greeks fresh + valid (§9.2 last check)
    quotes_delayed: bool = False           # bid/ask are DELAYED (no OPRA) → the
    #   spread is indicative, not a verified fill. The spread gate becomes an
    #   advisory WARNING rather than a hard block so paper candidates surface;
    #   flip to False (→ hard block) once real-time OPRA quotes are enabled.


@dataclass(frozen=True)
class PortfolioState:
    deployed_gbp: float = 0.0
    open_positions: int = 0
    cumulative_realised_loss_gbp: float = 0.0   # positive number = total banked loss


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    blocks: list[str]              # hard failures — why no trade
    warnings: list[str]           # non-blocking flags worth surfacing
    brake_tier: int               # 0 normal · 1 alert · 2 reduce-50% · 3 no-new · 4 circuit
    size_factor: float            # multiplier on max size (1.0 / 0.5 / 0)
    checked: list[str]            # gates that ran (audit)


def brake_tier_for(loss_gbp: float, cfg: OptionsRiskConfig) -> tuple[int, float]:
    """Map cumulative realised loss → (tier, size_factor). §9.5."""
    if loss_gbp >= cfg.circuit_breaker_gbp:
        return 4, 0.0     # circuit breaker → no new live trading
    if loss_gbp >= cfg.brake3_no_new_gbp:
        return 3, 0.0     # no new positions
    if loss_gbp >= cfg.brake2_reduce_gbp:
        return 2, 0.5     # halve new size
    if loss_gbp >= cfg.brake1_alert_gbp:
        return 1, 1.0     # alert only
    return 0, 1.0


def evaluate(
    candidate: TradeCandidate,
    ctx: MarketContext,
    portfolio: PortfolioState,
    cfg: OptionsRiskConfig | None = None,
) -> RiskDecision:
    """Run EVERY pre-trade + portfolio gate. Returns a RiskDecision; `allowed`
    is True only if zero blocks fired. A missing required input is a BLOCK, not
    a pass. No-trade (allowed=False) is a valid, logged outcome."""
    cfg = cfg or OptionsRiskConfig()
    blocks: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []
    s = candidate.structure

    # ── Drawdown brakes (portfolio-level; can veto ALL new) ──────────────
    tier, size_factor = brake_tier_for(portfolio.cumulative_realised_loss_gbp, cfg)
    checked.append("drawdown_brake")
    if tier == 4:
        blocks.append(
            f"CIRCUIT BREAKER: cumulative loss £{portfolio.cumulative_realised_loss_gbp:.0f} "
            f"≥ £{cfg.circuit_breaker_gbp:.0f} — all live trading stopped, return to paper.")
    elif tier == 3:
        blocks.append(
            f"Drawdown brake 3: cumulative loss £{portfolio.cumulative_realised_loss_gbp:.0f} "
            f"≥ £{cfg.brake3_no_new_gbp:.0f} — no new positions (manage existing only).")
    elif tier == 2:
        warnings.append("Drawdown brake 2: new size halved (size_factor 0.5).")
    elif tier == 1:
        warnings.append("Drawdown brake 1: alert — review open positions.")

    # ── Data availability — NO FALSE POSITIVES: can't verify → block ─────
    checked.append("data_available")
    if not ctx.data_fresh:
        blocks.append("Required market data (greeks/IV/OI/bid-ask) stale or invalid — cannot verify.")
    if ctx.regime is None:
        blocks.append("Regime could not be determined — cannot gate the structure.")
    if ctx.falling_knife is None:
        blocks.append("Falling-knife status unavailable — cannot clear short-premium entry.")

    # ── Regime gate (§7) ─────────────────────────────────────────────────
    if ctx.regime is not None:
        checked.append("regime_permits_structure")
        if s not in PERMITTED_BY_REGIME.get(ctx.regime, frozenset()):
            blocks.append(
                f"{s.value} not permitted in {ctx.regime.value} regime "
                f"(allowed: {', '.join(sorted(x.value for x in PERMITTED_BY_REGIME[ctx.regime]))}).")

    # ── Falling-knife block (§8) ─────────────────────────────────────────
    if ctx.falling_knife is True and s in WHEEL_ENTRY_STRUCTURES:
        checked.append("falling_knife")
        blocks.append(f"{candidate.symbol} in FALLING-KNIFE state — short-put / wheel entry blocked (high IV ≠ invitation).")

    # ── Short-premium IV-rank edge (§4, §5.3) ────────────────────────────
    if s in SHORT_PREMIUM_STRUCTURES:
        checked.append("iv_rank")
        if ctx.iv_rank is None:
            blocks.append("IV-Rank unavailable — cannot confirm the vega edge for selling premium.")
        elif ctx.iv_rank < cfg.iv_rank_min:
            blocks.append(f"IV-Rank {ctx.iv_rank:.0f}% < {cfg.iv_rank_min:.0f}% — premium too cheap, skip.")

    # ── Greek entry gates (§9.2) ─────────────────────────────────────────
    checked.append("delta")
    if candidate.abs_delta is None:
        blocks.append("Delta unavailable — cannot select strike within the assignment-probability band.")
    elif not (cfg.delta_min <= candidate.abs_delta <= cfg.delta_max):
        blocks.append(f"|delta| {candidate.abs_delta:.2f} outside {cfg.delta_min:.2f}–{cfg.delta_max:.2f}.")

    checked.append("dte")
    if candidate.dte is None:
        blocks.append("DTE unavailable.")
    elif not (cfg.dte_min <= candidate.dte <= cfg.dte_max):
        blocks.append(f"DTE {candidate.dte} outside {cfg.dte_min}–{cfg.dte_max} days.")

    # ── Liquidity gates (§6.1, §9.2) ─────────────────────────────────────
    checked.append("liquidity")
    if ctx.open_interest is None:
        blocks.append("Open interest unavailable — cannot confirm fillable liquidity.")
    elif ctx.open_interest < cfg.oi_min:
        blocks.append(f"Open interest {ctx.open_interest} < {cfg.oi_min} — illiquid, bad fills.")
    if ctx.bid_ask_spread_usd is None:
        blocks.append("Bid-ask spread unavailable.")
    elif ctx.bid_ask_spread_usd > cfg.spread_max_usd:
        _msg = f"Bid-ask ${ctx.bid_ask_spread_usd:.2f} > ${cfg.spread_max_usd:.2f} — spread too wide"
        if ctx.quotes_delayed:
            # DELAYED quote (no OPRA): the wide spread is a data artifact, not a
            # real market. Advise, don't block — but make it loud so nobody
            # mistakes the indicative premium for a verified fill.
            warnings.append(
                _msg + " — but quotes are DELAYED (no OPRA): indicative only, "
                "verify the live spread before you place. Enable OPRA for real-time.")
        else:
            blocks.append(_msg + ".")

    # ── Earnings blackout (§9.4) ─────────────────────────────────────────
    if s in SHORT_PREMIUM_STRUCTURES:
        checked.append("earnings_blackout")
        if ctx.earnings_in_expiry_window is None:
            blocks.append("Earnings calendar unavailable — cannot clear the event blackout.")
        elif ctx.earnings_in_expiry_window:
            blocks.append("Earnings fall within the expiry window — IV-crush + gap risk; no new short premium.")
    # Ex-div only matters for short calls (early-assignment risk) — warn, don't block
    if s == Structure.COVERED_CALL and ctx.ex_div_in_expiry_window:
        warnings.append("Ex-dividend within expiry window — ITM short call carries early-assignment risk.")

    # ── Capital limits (§9.1) ────────────────────────────────────────────
    eff_per_pos = cfg.per_position_gbp * size_factor
    checked.append("per_position_limit")
    if candidate.notional_gbp is None:
        blocks.append("Trade notional unavailable — cannot check per-position / deployment limits.")
    else:
        if size_factor == 0.0 and tier < 3:
            pass  # shouldn't happen; tiers 3/4 already blocked above
        if candidate.notional_gbp > eff_per_pos and eff_per_pos > 0:
            blocks.append(
                f"Notional £{candidate.notional_gbp:.0f} > per-position limit £{eff_per_pos:.0f}"
                + (" (halved by drawdown brake 2)" if size_factor == 0.5 else "") + ".")
        checked.append("deployment_limit")
        if portfolio.deployed_gbp + candidate.notional_gbp > cfg.max_deploy_gbp:
            blocks.append(
                f"Deployed £{portfolio.deployed_gbp:.0f} + £{candidate.notional_gbp:.0f} "
                f"> max £{cfg.max_deploy_gbp:.0f} (assignment buffer breached).")

    checked.append("max_positions")
    if portfolio.open_positions >= cfg.max_positions:
        blocks.append(f"Open positions {portfolio.open_positions} ≥ max {cfg.max_positions}.")

    return RiskDecision(
        allowed=len(blocks) == 0,
        blocks=blocks,
        warnings=warnings,
        brake_tier=tier,
        size_factor=size_factor,
        checked=checked,
    )
