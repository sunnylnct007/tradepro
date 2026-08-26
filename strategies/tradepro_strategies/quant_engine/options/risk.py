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
    # BRIDGE vega gate (OAuth-only architecture, 2026-08-09): while our own
    # options_iv_daily dataset accumulates toward a trustworthy rank window,
    # the edge check is IV vs 30d realised vol — the variance risk premium.
    # ≥1.0 means implied is at least paying for realised; the rank gate takes
    # over automatically once the window matures (fetch_iv_rank_web sets
    # iv_rank only then).
    iv_hv_min: float = 1.0
    # Premium floor (owner 2026-08-09: "avoid selling options not paying
    # much") — an otherwise-clean candidate that only pays pennies ties up
    # collateral for nothing. Both must clear: an absolute per-share floor
    # (fees/slippage swamp a $0.10 credit) AND an annualised yield that
    # meaningfully beats the ~4.5% bank rate the capital could earn instead.
    min_premium_usd: float = 0.20
    min_ann_yield_pct: float = 8.0

    # Managed close: take profit at `manage_at_pct` of max premium after roughly
    # `manage_dte_frac` of the DTE has elapsed. 0.0 = disabled (hold to expiry).
    # When enabled the yield gate is tested against the MANAGED annualised
    # return instead of the hold-to-expiry one.
    manage_at_pct: float = 0.0
    # MEASURED, not assumed. 641 managed closes simulated across 10 wheel names
    # (2019-2026, 5% OTM, 30 DTE, close at 60% of premium) held a median of
    # 16 of 30 sessions. The 0.50 originally written here was a guess and
    # happened to be close; this is the number the harness produced.
    manage_dte_frac: float = 0.53
    # Liquidity gates (§6.1 filter 1, §9.2)
    oi_min: int = 250          # per-strike OI floor. 1,000 was index-level and
    #   rejected every single-name equity strike (KO/F/INTC near-month strikes
    #   run 50–500 OI); 250 is enough for 1-lot paper/wheel fills without bad
    #   slippage. Raise for size.
    spread_max_usd: float = 0.10
    # Spread cap is PREMIUM-RELATIVE when the mid is known: a $0.10 absolute cap
    # is only realistic for sub-$1 premiums — a $315-strike JPM put quoting a
    # $5.90 mid will never show a dime-wide market, so the absolute cap alone
    # blocked every mid/high-priced underlying forever. Allowed spread =
    # max(spread_max_usd, spread_max_pct_of_mid × mid).
    spread_max_pct_of_mid: float = 0.15
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
            # DELTA BAND — configurable because it encodes WHICH TRADE we screen
            # for, not a safety limit. The 0.20-0.35 default is a
            # sell-near-the-money, hold-to-expiry wheel. An operator selling
            # FURTHER out of the money and closing early sits at 0.10-0.15, and
            # was being rejected by a band that describes a different strategy.
            #
            # Lowering delta_min REDUCES per-trade risk: further OTM is a lower
            # assignment probability, not a higher one. Aggregate exposure stays
            # bounded by max_positions / per_position_gbp / max_deploy_gbp, which
            # are unchanged.
            delta_min=_f("TRADEPRO_WHEEL_DELTA_MIN", d.delta_min),
            delta_max=_f("TRADEPRO_WHEEL_DELTA_MAX", d.delta_max),
            # MANAGED-CLOSE yield. The floor annualises premium over the FULL
            # DTE, which is only correct if the put is held to expiry. Closing at
            # 60% of max premium partway through frees the collateral to be
            # redeployed, so the realised annualised return is materially higher
            # than the number the floor tests. That is a measurement bug, not a
            # strategy disagreement: trades were being rejected for failing a
            # test they were never going to sit.
            #
            # 0 = off (hold-to-expiry, unchanged default). Set the capture
            # fraction to model the managed trade; the holding fraction is an
            # ASSUMPTION and is stated wherever the number is shown.
            manage_at_pct=_f("TRADEPRO_WHEEL_MANAGE_AT_PCT", d.manage_at_pct),
            manage_dte_frac=_f("TRADEPRO_WHEEL_MANAGE_DTE_FRAC", d.manage_dte_frac),
            min_premium_usd=_f("TRADEPRO_WHEEL_MIN_PREMIUM_USD", d.min_premium_usd),
            min_ann_yield_pct=_f("TRADEPRO_WHEEL_MIN_ANN_YIELD_PCT", d.min_ann_yield_pct),
            # Bridge vega threshold — external review (9 Aug) fairly noted a
            # hard 1.0 veto rejects nearly everything in an IV-crush tape;
            # keep the default but let the operator run it looser/tighter.
            iv_hv_min=_f("TRADEPRO_WHEEL_IV_HV_MIN", d.iv_hv_min),
            iv_rank_min=_f("TRADEPRO_WHEEL_IV_RANK_MIN", d.iv_rank_min),
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
    iv_rank: float | None = None           # %, from accumulated IV history (only set when window honest)
    iv_hv_ratio: float | None = None       # IV ÷ HV30 — bridge vega gate while the rank window accumulates
    iv_rank_window_days: int | None = None # depth of the IV dataset behind iv_rank/bridge (for honest reasons)
    open_interest: int | None = None       # near-month OI at the strike
    bid_ask_spread_usd: float | None = None
    premium_mid_usd: float | None = None   # mid of the short leg — scales the spread cap
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

    # ── Short-premium vega edge (§4, §5.3) ───────────────────────────────
    # Two-tier: IV-Rank when our accumulated IV dataset is deep enough to be
    # honest (caller only sets iv_rank then); otherwise the IV/HV-ratio BRIDGE
    # (variance risk premium) — passed loudly as a warning so nobody mistakes
    # it for the rank gate. Neither available → BLOCK (no false positives).
    if s in SHORT_PREMIUM_STRUCTURES:
        checked.append("iv_rank")
        _win = (f", {ctx.iv_rank_window_days}d data"
                if ctx.iv_rank_window_days is not None else "")
        if ctx.iv_rank is not None:
            if ctx.iv_rank < cfg.iv_rank_min:
                blocks.append(f"IV-Rank {ctx.iv_rank:.0f}% < {cfg.iv_rank_min:.0f}% — premium too cheap, skip.")
        elif ctx.iv_hv_ratio is not None:
            checked.append("iv_hv_bridge")
            # Terse on purpose — this fires on most of the universe at once and
            # the screen repeats it per row; boilerplate goes in the UI tooltip.
            if ctx.iv_hv_ratio < cfg.iv_hv_min:
                blocks.append(
                    f"IV/HV {ctx.iv_hv_ratio:.2f} < {cfg.iv_hv_min:.2f} — premium thin vs realised (bridge{_win}).")
            else:
                warnings.append(
                    f"Vega edge via IV/HV bridge {ctx.iv_hv_ratio:.2f} ≥ {cfg.iv_hv_min:.2f}{_win} — provisional until rank window matures.")
        else:
            blocks.append("IV-Rank unavailable — cannot confirm the vega edge for selling premium.")

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
    # Premium-relative spread cap: the absolute $0.10 floor only fits sub-$1
    # premiums; scale by the mid when we have one so mid/high-priced quality
    # names aren't permanently blocked on a structurally-wider (but fair) market.
    _spread_allowed = cfg.spread_max_usd
    if ctx.premium_mid_usd is not None and ctx.premium_mid_usd > 0:
        _spread_allowed = max(cfg.spread_max_usd, cfg.spread_max_pct_of_mid * ctx.premium_mid_usd)
    if ctx.bid_ask_spread_usd is None:
        blocks.append("Bid-ask spread unavailable.")
    elif ctx.bid_ask_spread_usd > _spread_allowed:
        _msg = f"Bid-ask ${ctx.bid_ask_spread_usd:.2f} > ${_spread_allowed:.2f} — spread too wide"
        if ctx.quotes_delayed:
            # DELAYED quote (no OPRA): the wide spread is a data artifact, not a
            # real market. Advise, don't block — but make it loud so nobody
            # mistakes the indicative premium for a verified fill.
            warnings.append(
                _msg + " — but quotes are DELAYED (no OPRA): indicative only, "
                "verify the live spread before you place. Enable OPRA for real-time.")
        else:
            blocks.append(_msg + ".")

    # ── Premium floor — "not paying much" gate ───────────────────────────
    # A short-premium trade must actually PAY: per-share credit above the
    # fees/slippage floor AND an annualised yield that beats parking the
    # collateral in the bank. Missing premium = could-not-verify = BLOCK.
    if s in SHORT_PREMIUM_STRUCTURES:
        checked.append("premium_floor")
        if ctx.premium_mid_usd is None:
            blocks.append("Premium (mid) unavailable — cannot verify the trade pays enough to be worth the collateral.")
        else:
            if ctx.premium_mid_usd < cfg.min_premium_usd:
                blocks.append(
                    f"Premium ${ctx.premium_mid_usd:.2f} < ${cfg.min_premium_usd:.2f} floor — "
                    f"pennies; fees/slippage eat the credit.")
            if (candidate.strike is not None and candidate.strike > 0
                    and candidate.dte is not None and candidate.dte > 0):
                checked.append("annualised_yield")
                ann = ctx.premium_mid_usd / candidate.strike * (365.0 / candidate.dte) * 100.0
                # MANAGED CLOSE: if the operator takes profit at a fraction of
                # max premium partway through, the collateral is freed early and
                # the realised annualised return is higher than the
                # hold-to-expiry figure. Test the gate against what the trade
                # would actually earn, not against a holding period nobody
                # intends to sit. Off by default (manage_at_pct = 0).
                if cfg.manage_at_pct > 0 and cfg.manage_dte_frac > 0:
                    ann = ann * (cfg.manage_at_pct / cfg.manage_dte_frac)
                if ann < cfg.min_ann_yield_pct:
                    blocks.append(
                        f"Annualised yield {ann:.1f}% < {cfg.min_ann_yield_pct:.1f}% — "
                        f"not paying enough over the bank rate for assignment risk.")

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
