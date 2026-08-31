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
    # Below this the trade is uneconomic rather than merely thin — the only
    # level that still HARD-blocks. Above it and below iv_hv_min the candidate
    # survives with a warning and a realised-vol strike adjustment, because a
    # gate that rejects the entire universe protects nothing (30 Aug 2026).
    iv_hv_floor: float = 0.35
    # Premium floor (owner 2026-08-09: "avoid selling options not paying
    # much") — an otherwise-clean candidate that only pays pennies ties up
    # collateral for nothing. Both must clear: an absolute per-share floor
    # (fees/slippage swamp a $0.10 credit) AND an annualised yield that
    # meaningfully beats the ~4.5% bank rate the capital could earn instead.
    min_premium_usd: float = 0.20
    min_ann_yield_pct: float = 8.0

    # Don't sell puts into a name at its own high (owner rule, 26 Aug 2026).
    # The underlying must trade at least this far BELOW its 52-week high. 5%
    # is deliberately modest: it removes the "at the high" case the rule is
    # about (XLF 0.2%, SPY 1.8%, IWM 2.2% on 26 Aug) without pretending to
    # know where a pullback becomes attractive — that is a judgement, so it is
    # a knob. TRADEPRO_WHEEL_MIN_PCT_OFF_HIGH=0 disables the gate entirely.
    min_pct_off_52w_high: float = 5.0

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
    oi_min: int = 250                # per-strike OI floor. 1,000 was index-level and
    #   rejected every single-name equity strike (KO/F/INTC near-month strikes
    #   run 50–500 OI); 250 is enough for 1-lot paper/wheel fills without bad
    #   slippage. Raise for size.
    # Below THIS, the contract is EMPTY rather than thin and the spread cannot
    # vouch for it. Distinct from oi_min above, which is the "thin but real"
    # line where a verified tight spread is allowed to outvote our weaker OI
    # feed. Nothing is outstanding at 0-10 contracts, so a two-sided quote there
    # is a market-maker's indicative, not evidence that anyone has traded it.
    oi_absent_max: int = 10
    # Sources whose open interest is allowed to BLOCK a candidate. Anything else
    # may only warn. IBKR serves OI on the live account (verified: XOM 155P =
    # 868 via option_open_interest) but NOT on the paper cpapi session we screen
    # with — probed 7085-7089, 7607, 7638, 7639, 7697-7698 against that same
    # contract and none was returned. So today every OI we hold comes from our
    # own Yahoo-derived capture, which is wrong by more than an order of
    # magnitude, and it was rejecting 53 of 82 rows.
    oi_blocking_sources: tuple[str, ...] = ("ibkr", "ibkr_web", "g3_ibkr")
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
            iv_hv_floor=_f("TRADEPRO_WHEEL_IV_HV_FLOOR", d.iv_hv_floor),
            min_pct_off_52w_high=_f("TRADEPRO_WHEEL_MIN_PCT_OFF_HIGH",
                                    d.min_pct_off_52w_high),
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
    # How far BELOW its own 52-week high the underlying trades, in percent.
    # 0 = sitting at the high. Owner rule, 26 Aug 2026: "there is no point
    # selling a put in stock which is high already as it will do mean
    # reversion" — a short put is short downside precisely when there is most
    # room to give back. The regime gate pulls the OTHER way (GREEN means in or
    # above the Ichimoku cloud, which selects for strength), so without this
    # the screen systematically surfaced names at their highs: of the 4 eligible
    # on 26 Aug, XLF sat 0.2% off its high, IWM 2.2%, SPY 1.8%.
    pct_off_52w_high: float | None = None
    iv_rank: float | None = None           # %, from accumulated IV history (only set when window honest)
    iv_hv_ratio: float | None = None       # IV ÷ HV30 — bridge vega gate while the rank window accumulates
    iv_rank_window_days: int | None = None # depth of the IV dataset behind iv_rank/bridge (for honest reasons)
    open_interest: int | None = None       # near-month OI at the strike
    # WHERE that OI came from. A number is only as blocking as its source is
    # trustworthy, and ours is currently not: measured 31 Aug 2026, XOM's 155P
    # read 58 for us against 868 live from IBKR, and an earlier review put the
    # same contract at 57 against 7,570. None means "unspecified" and is treated
    # as authoritative, so a caller that says nothing keeps the strict behaviour.
    open_interest_source: str | None = None
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
    # WHY A ROW IS DARK vs WHY IT IS A BAD TRADE (29 Aug 2026).
    #
    # These are different facts and the board conflated them: on 28 Aug, 44 of
    # 82 wheel rows were blocked by "Pricing carried from the last priced
    # screen" or "Bid-ask spread unavailable" — data conditions — and rendered
    # identically to a name rejected on merit. Half the screen looked like a
    # market verdict when it was a feed outage.
    #
    # `data_blocks` still counts against `allowed`: a trade whose inputs cannot
    # be verified must not be offered. What changes is that a caller can now
    # tell "this setup is sound, we just cannot price it" from "this is not a
    # trade", and say so.
    data_blocks: list[str] = field(default_factory=list)

    @property
    def all_blocks(self) -> list[str]:
        """Every reason this is not tradeable, merit and data together.

        Callers that only care THAT it is blocked should read this; callers
        rendering a board should read `blocks` and `data_blocks` separately, so
        a feed outage does not masquerade as a market verdict.
        """
        return list(self.blocks) + list(self.data_blocks)

    @property
    def merit_ok(self) -> bool:
        """True when nothing about the TRADE fails — only its data is missing."""
        return not self.blocks and bool(self.data_blocks)


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
    *,
    capital_gates: bool = True,
) -> RiskDecision:
    """Run EVERY pre-trade + portfolio gate. Returns a RiskDecision; `allowed`
    is True only if zero blocks fired. A missing required input is a BLOCK, not
    a pass. No-trade (allowed=False) is a valid, logged outcome."""
    cfg = cfg or OptionsRiskConfig()
    blocks: list[str] = []
    data_blocks: list[str] = []
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
        data_blocks.append("Required market data (greeks/IV/OI/bid-ask) stale or invalid — cannot verify.")
    if ctx.regime is None:
        data_blocks.append("Regime could not be determined — cannot gate the structure.")
    if ctx.falling_knife is None:
        data_blocks.append("Falling-knife status unavailable — cannot clear short-premium entry.")

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

    # ── Extension: don't sell puts into a name at its high (owner, 26 Aug) ─
    # The bookend to falling-knife. That gate rejects names that have fallen
    # too far; this one rejects names that have not fallen at all. Together
    # they define the band a wheel entry should live in — enough pullback that
    # the strike sits somewhere you would genuinely accept the shares, without
    # catching something in free-fall.
    #
    # It exists because the regime gate leans the other way: GREEN means in or
    # above the Ichimoku cloud, which selects for strength. On 26 Aug that put
    # XLF (0.2% off its high), IWM (2.2%) and SPY (1.8%) among only four
    # eligible names — the exact case the owner ruled out.
    if s in WHEEL_ENTRY_STRUCTURES and cfg.min_pct_off_52w_high > 0:
        checked.append("extension_vs_52w_high")
        if ctx.pct_off_52w_high is None:
            data_blocks.append(
                "Distance from the 52-week high unavailable — cannot confirm "
                "the underlying isn't extended.")
        elif ctx.pct_off_52w_high < cfg.min_pct_off_52w_high:
            blocks.append(
                f"{candidate.symbol} is {ctx.pct_off_52w_high:.1f}% off its 52-week "
                f"high (need ≥ {cfg.min_pct_off_52w_high:.0f}%) — too extended to "
                f"sell downside into; mean reversion works against a short put here.")

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
            if ctx.iv_hv_ratio < cfg.iv_hv_floor:
                # HARD floor only — genuinely uneconomic, not merely thin.
                blocks.append(
                    f"IV/HV {ctx.iv_hv_ratio:.2f} < {cfg.iv_hv_floor:.2f} — the option is priced "
                    f"for far less movement than the stock is actually delivering. Selling here "
                    f"is taking the wrong side of the variance gap, not harvesting it.")
            elif ctx.iv_hv_ratio < cfg.iv_hv_min:
                # A DIAL, NOT A GATE (30 Aug 2026).
                #
                # This was a hard block at 1.0 and it was blocking EVERYTHING:
                # every name on the live board sits below it (XOM 0.90, NVDA 0.81,
                # GOOGL 0.78, APLD 0.70, PG 0.63, MU 0.62, MRVL 0.49), which is
                # exactly the "82 screened, 0 eligible" the owner has been looking
                # at for weeks.
                #
                # The owner's own record says the calibration is wrong in this
                # direction: 10 closed option trades, 10 winners, $2,025 realised,
                # 85% of premium captured, 17 days held. A filter can only add
                # value by removing losers. There are none. So a gate that blocks
                # every candidate is not protecting anything — it is the failure
                # mode in evidence.
                #
                # IV < HV does not mean "do not sell". It means the QUOTED DELTA
                # UNDERSTATES THE RISK, because delta is derived from implied vol
                # while the stock is moving at realised. The correct response is
                # to sell FURTHER OUT for the same true delta, not to refuse.
                # Sized at realised vol the strike moves materially: on MRVL
                # (IV/HV 0.49) the naive and true strikes are 19 points apart.
                # SAY THE NUMBER, DO NOT DESCRIBE IT.
                #
                # This warning used to promise the reader "expect the true delta
                # to be ~N% worse" and then leave the row showing the quoted
                # delta anyway. Nothing in the screen sizes off realised vol —
                # the advice named a correction the desk never applied, so the
                # row still read as a 0.22-delta trade when it was not. That is
                # the understated-risk direction, on the screen the owner trades
                # from, which is exactly what the guard tests existed to stop.
                #
                # Quoted |delta| ~ N(-d) with d = ln(K/S)/(sigma*sqrt(T)). Only
                # sigma changes between the implied and realised view, so
                # d_true = d_quoted * (IV/HV) and the true delta follows without
                # needing S, K or T — all of which we may not have here.
                #   |delta| 0.28 at IV/HV 0.61 -> d 0.583 -> 0.356 -> |delta| 0.36
                #
                # APPROXIMATE, AND CONSERVATIVE BY DESIGN. It drops the
                # variance-drift term, so it overstates the true delta slightly:
                # against an explicit Black-Scholes reprice of the same contract
                # at realised vol, 0.36 here versus 0.345 there. Erring toward
                # MORE assignment risk is the right direction for a disclosure,
                # and the exact figure would need spot, which MarketContext does
                # not carry. Stated as "about", never as a precise delta.
                _true_delta = None
                try:
                    from statistics import NormalDist as _ND
                    _q = abs(float(candidate.abs_delta))
                    if 0.0 < _q < 1.0 and ctx.iv_hv_ratio > 0:
                        _d_true = _ND().inv_cdf(1.0 - _q) * ctx.iv_hv_ratio
                        _true_delta = 1.0 - _ND().cdf(_d_true)
                except Exception:  # noqa: BLE001 — a missing delta must not kill the screen
                    _true_delta = None
                _delta_txt = (
                    f"At this ratio the quoted |delta| {abs(candidate.abs_delta):.2f} is really "
                    f"about {_true_delta:.2f} — assignment odds ~{100 * _true_delta:.0f}%, not "
                    f"~{100 * abs(candidate.abs_delta):.0f}%. "
                    if _true_delta is not None else
                    "The true delta cannot be restated without a quoted delta. ")
                warnings.append(
                    f"IV/HV {ctx.iv_hv_ratio:.2f} < {cfg.iv_hv_min:.2f} — the option prices LESS "
                    f"movement than the stock is delivering, so the quoted delta UNDERSTATES "
                    f"assignment risk. {_delta_txt}"
                    f"Not a block: sell further out for the same TRUE delta. "
                    f"Ranked below better-paid names.")
            else:
                warnings.append(
                    f"Vega edge via IV/HV bridge {ctx.iv_hv_ratio:.2f} ≥ {cfg.iv_hv_min:.2f}{_win} — provisional until rank window matures.")
        else:
            data_blocks.append("IV-Rank unavailable — cannot confirm the vega edge for selling premium.")

    # ── Greek entry gates (§9.2) ─────────────────────────────────────────
    checked.append("delta")
    if candidate.abs_delta is None:
        data_blocks.append("Delta unavailable — cannot select strike within the assignment-probability band.")
    elif not (cfg.delta_min <= candidate.abs_delta <= cfg.delta_max):
        blocks.append(f"|delta| {candidate.abs_delta:.2f} outside {cfg.delta_min:.2f}–{cfg.delta_max:.2f}.")

    checked.append("dte")
    if candidate.dte is None:
        data_blocks.append("DTE unavailable.")
    elif not (cfg.dte_min <= candidate.dte <= cfg.dte_max):
        blocks.append(f"DTE {candidate.dte} outside {cfg.dte_min}–{cfg.dte_max} days.")

    # ── Liquidity gates (§6.1, §9.2) ─────────────────────────────────────
    checked.append("liquidity")
    # OPEN INTEREST MAY NOT BLOCK ON AN UNTRUSTED SOURCE (31 Aug 2026, owner:
    # "OI data doesnt need to be a hard gate").
    #
    # The liquidity gate was the single largest rejection reason on the live
    # board — 53 of 82 rows — and it was reading a feed measured wrong by more
    # than an order of magnitude. Blocking a genuinely liquid name on a number
    # we know is false is worse than not checking at all, because it presents as
    # a judgement about the market rather than about our data.
    #
    # The spread stays a real gate. It is verified against live IBKR quotes and
    # it measures the thing that actually decides a fill.
    _oi_may_block = (ctx.open_interest_source is None
                     or ctx.open_interest_source in cfg.oi_blocking_sources)
    if ctx.open_interest is not None and not _oi_may_block:
        warnings.append(
            f"Open interest {ctx.open_interest:,} is from {ctx.open_interest_source!r}, "
            f"not from IBKR — shown for context only and NOT used to reject this "
            f"candidate. Our capture has measured an order of magnitude below the "
            f"live figure. Liquidity here is judged on the spread, which is verified.")
    elif ctx.open_interest is None:
        data_blocks.append("Open interest unavailable — cannot confirm fillable liquidity.")
    elif _oi_may_block and ctx.open_interest <= cfg.oi_absent_max:
        # A MEASURED NEAR-ZERO IS NOT A THIN PROXY — IT IS AN EMPTY CONTRACT.
        #
        # The proxy argument below says our OI feed UNDERSTATES the true number,
        # so a low reading should not outvote a verified tight spread. That
        # argument does not reach the bottom of the range. At 0-10 contracts
        # nothing is outstanding at all, and a quoted spread there is a
        # market-maker's indicative, not evidence that anyone has traded it.
        #
        # Not a knife-edge at exactly zero: 3 outstanding contracts is as empty
        # as 0, and drawing the line at 0 would have let a 3-OI contract through
        # on a tight quote purely because the feed happened to return non-zero.
        #
        # The null/near-zero split is deliberate and load-bearing: None means
        # "not recorded" and blocks as UNAVAILABLE, a small number means
        # "recorded as almost none" and blocks as ILLIQUID. chains_g3 preserves
        # a missing OI as None rather than coercing it to 0, which is what makes
        # a measured zero trustworthy enough to act on.
        blocks.append(
            f"Open interest {ctx.open_interest} — effectively nothing is outstanding at this "
            f"strike, so it is illiquid regardless of the quoted spread. A two-sided quote on "
            f"a contract no one holds is indicative, not a market.")
    elif _oi_may_block and ctx.open_interest < cfg.oi_min:
        # OPEN INTEREST IS A PROXY. THE SPREAD IS THE MEASUREMENT. (30 Aug 2026)
        #
        # OI answers "how many contracts are outstanding". What actually decides
        # whether you get filled at a fair price is the SPREAD and the size
        # behind it — and we now know our spread data is right: XOM Oct-16 150P
        # reads bid 2.95 / ask 3.35 in option_quote_daily, matching the live
        # IBKR quote exactly.
        #
        # Our OI does NOT have that pedigree. It comes from the Yahoo capture,
        # coverage is intermittent run to run (82/82 symbols one night, null the
        # next), and a review measured that same XOM contract at 57 for us
        # against 7,570 live. So OI is the weaker of the two signals and it was
        # the one doing the blocking — 42 of 82 rows on the 26 Aug board.
        #
        # Block only when BOTH liquidity signals fail. A contract quoting a
        # tight two-sided market is fillable whatever a thin OI feed claims;
        # one with a wide spread AND low OI genuinely is not.
        _mid = ctx.premium_mid_usd
        _sp = ctx.bid_ask_spread_usd
        _tight = (_mid is not None and _mid > 0 and _sp is not None
                  and (_sp / _mid) <= cfg.spread_max_pct_of_mid)
        if _tight:
            warnings.append(
                f"Open interest {ctx.open_interest} < {cfg.oi_min}, but the market is "
                f"two-sided and tight ({_sp:.2f} on a {_mid:.2f} mid = "
                f"{100 * _sp / _mid:.0f}%). OI comes from the Yahoo capture and has been "
                f"measured low against IBKR; the spread is verified against the live quote. "
                f"Not blocked on the weaker signal — work a limit and check the depth.")
        else:
            blocks.append(
                f"Open interest {ctx.open_interest} < {cfg.oi_min} AND the spread is wide — "
                f"both liquidity signals fail, so this is genuinely hard to fill.")
    # Premium-relative spread cap: the absolute $0.10 floor only fits sub-$1
    # premiums; scale by the mid when we have one so mid/high-priced quality
    # names aren't permanently blocked on a structurally-wider (but fair) market.
    _spread_allowed = cfg.spread_max_usd
    if ctx.premium_mid_usd is not None and ctx.premium_mid_usd > 0:
        _spread_allowed = max(cfg.spread_max_usd, cfg.spread_max_pct_of_mid * ctx.premium_mid_usd)
    if ctx.bid_ask_spread_usd is None:
        data_blocks.append("Bid-ask spread unavailable.")
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
            data_blocks.append("Premium (mid) unavailable — cannot verify the trade pays enough to be worth the collateral.")
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
            data_blocks.append("Earnings calendar unavailable — cannot clear the event blackout.")
        elif ctx.earnings_in_expiry_window:
            blocks.append("Earnings fall within the expiry window — IV-crush + gap risk; no new short premium.")
    # Ex-div only matters for short calls (early-assignment risk) — warn, don't block
    if s == Structure.COVERED_CALL and ctx.ex_div_in_expiry_window:
        warnings.append("Ex-dividend within expiry window — ITM short call carries early-assignment risk.")

    # ── Capital limits (§9.1) ────────────────────────────────────────────
    # SIGNAL vs CAPITAL (owner ruling, restated 26 Aug 2026). Whether a trade
    # is SOUND and whether it fits TODAY'S account are different questions, and
    # only the first belongs on the screen. NVDA showed "— no · Notional £15,354
    # > per-position limit £10,000": a well-priced put on a name that cleared
    # every merit gate, presented as if it were not a trade. The owner's reply
    # was "I can put in capital if needed" — which is exactly the decision a
    # screener must leave open rather than pre-empt.
    #
    # With capital_gates=False these checks still RUN (so `checked` stays an
    # honest audit of what was evaluated) but say NOTHING. They were briefly
    # emitted as warnings and the owner's verdict was "it's more of a noise" —
    # correct, because the row ALREADY carries the same fact in a better form:
    # the Size fit column shows notional as a percentage of NAV (NVDA 12.9%,
    # SLV 3.8%). "Notional £22,835 > per-position limit £10,000" is that same
    # number restated as prose, on a screen whose job is trade quality.
    #
    # The autonomous paper-wheel keeps the default True: it spends real
    # collateral without asking, so for it these stay hard blocks.
    eff_per_pos = cfg.per_position_gbp * size_factor
    checked.append("per_position_limit")
    if candidate.notional_gbp is None:
        # NOT capital-conditional, and NOT suppressed: an unknown notional
        # means the sizing question cannot even be asked, and Size fit has
        # nothing to show either. That is a data failure, not a funding choice.
        data_blocks.append("Trade notional unavailable — cannot check per-position / deployment limits.")
    elif capital_gates:
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
    else:
        checked.append("deployment_limit")

    checked.append("max_positions")
    if portfolio.open_positions >= cfg.max_positions and capital_gates:
        blocks.append(f"Open positions {portfolio.open_positions} ≥ max {cfg.max_positions}.")

    return RiskDecision(
        # data_blocks still count: an unverifiable input must never be traded.
        allowed=len(blocks) == 0 and len(data_blocks) == 0,
        blocks=blocks,
        data_blocks=data_blocks,
        warnings=warnings,
        brake_tier=tier,
        size_factor=size_factor,
        checked=checked,
    )
