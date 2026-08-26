"""Wheel backtester — simulate the cash-secured-put → assignment → covered-call
cycle over historical daily bars and report BOTH the executions (every put/call
sold, every assignment / call-away) AND the performance (income, return, CAGR,
max drawdown) vs buy-and-hold and the bank rate.

This answers "how does the wheel perform with our strategy" with evidence,
without needing a live broker, OPRA data, or options permissions.

PROVISIONAL pricing: premiums are MODEL-priced (Black-Scholes) using trailing
realised volatility as the IV proxy — not market option quotes. Real IV usually
runs a bit above realised (the volatility risk premium the wheel harvests), so
this is a *conservative* estimate of premium income. Swap in a historical IV
feed for exact figures. The execution logic (assignment, call-away) is exact.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

from .black_scholes import BlackScholesPricer


@dataclass
class WheelTrade:
    date: str
    action: str       # SELL_PUT|ASSIGNED|PUT_EXPIRED|SELL_CALL|CALLED_AWAY|CALL_EXPIRED
    strike: float | None
    premium: float | None     # per share
    spot: float
    note: str = ""


@dataclass
class WheelResult:
    symbol: str
    start: str
    end: str
    contracts: int
    start_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    premium_income: float        # cumulative premium collected (cash)
    realised_pnl: float          # banked share gains on call-away
    max_drawdown_pct: float
    n_puts_sold: int
    n_assignments: int
    n_calls_sold: int
    n_call_aways: int
    days: int
    # comparisons
    buy_hold_return_pct: float
    bank_return_pct: float
    trades: list[WheelTrade] = field(default_factory=list)
    # Per-day series (post-warmup) for PORTFOLIO aggregation — correlated
    # drawdown/assignment across a book can't be seen from per-symbol
    # summaries alone (the 2020 stress question). state ∈ flat|short_put|
    # shares_pending|covered_call; "assigned" = holding shares.
    curve_dates: list[str] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    state_by_day: list[str] = field(default_factory=list)
    utilisation_pct: float = 0.0     # share of post-warmup days with a position on
    costs_paid: float = 0.0          # spread haircut + commissions actually deducted
    # v2 gate telemetry (WHEEL_BACKTEST_GATES_V2.md) — WHY the sim declined to
    # trade, so a low trade count is explainable rather than mysterious.
    n_blocked_floor: int = 0         # premium below $ / annualised-yield floor
    n_blocked_regime: int = 0        # ORANGE/RED or falling-knife at entry
    n_blocked_earnings: int = 0      # a print inside [today, expiry]
    n_g5_violations: int = 0         # CSPs opened into a known print — must be 0
    earnings_modelled: bool = False  # was the veto active for this window
    # v3 (WHEEL_BACKTEST_GATES_V3.md): the primary-trend floor — no new CSP on
    # a name below its own 200-SMA. Answers G4's failure mechanism directly:
    # the wheel's structural risk is being ASSIGNED INTO A DECLINER, and a
    # name under its primary trend is the definition of one.
    n_managed_closes: int = 0        # short puts bought back early at a profit target
    n_blocked_trend: int = 0         # spot below the primary-trend SMA at entry
    trend_modelled: bool = False     # was the floor active for this window


def _realised_vol(closes: list[float], i: int, window: int = 30) -> float:
    """Annualised realised vol from the trailing `window` daily log returns."""
    lo = max(1, i - window + 1)
    rets = [math.log(closes[j] / closes[j - 1]) for j in range(lo, i + 1)
            if closes[j] > 0 and closes[j - 1] > 0]
    if len(rets) < 5:
        return 0.30  # not enough history → sane default
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    vol = math.sqrt(var) * math.sqrt(252)
    return max(0.05, min(vol, 2.0))  # clamp to sane band


def simulate_wheel(
    dates: list[_dt.date],
    closes: list[float],
    *,
    otm_pct: float = 0.05,       # sell strikes ~5% OTM
    dte: int = 30,               # calendar days to expiry
    contracts: int = 1,
    start_capital: float = 100_000.0,
    rf: float = 0.04,            # risk-free / bank rate for comparison
    warmup: int = 30,
    premium_haircut_pct: float = 0.0,   # spread cost: fraction of each premium lost (0.05 = 5%)
    commission_per_leg: float = 0.0,    # $ per contract per SOLD leg (puts + calls)
    # ── v2 modelled live gates (all OFF by default ⇒ v1 reproducible) ──
    manage_at_pct: float = 0.0,         # close a short put once this FRACTION of the
                                        # premium has been captured (0.60 = buy back at
                                        # 40% of the credit). 0.0 = hold to expiry.
    min_premium_usd: float = 0.0,       # per-share floor, BOTH legs (live: 0.20)
    min_ann_yield_pct: float = 0.0,     # annualised-on-collateral floor (live: 8.0)
    regime_by_day: list[str] | None = None,   # 'GREEN'/'YELLOW'/'ORANGE'/'RED' per bar
    knife_by_day: list[bool] | None = None,   # falling-knife flag per bar
    earnings_dates: list[_dt.date] | None = None,  # historical print dates
    earnings_modelled: bool = False,    # False ⇒ veto inactive (declare coverage!)
    # ── v3 primary-trend floor (OFF by default ⇒ v1/v2 reproducible) ──
    # Per-bar "is this name above its primary trend?". Precomputed by the
    # CALLER, deliberately: a 200-SMA needs 200 bars of history, and the
    # backtest only ever receives the in-window slice — computing it here
    # would blind the first ~200 of a ~315-bar window. The runner has the
    # pre-window bars (it already requires ≥260) and is the honest place to
    # build this, exactly as it already does for regime_by_day/knife_by_day.
    trend_ok_by_day: list[bool] | None = None,
    trend_modelled: bool = False,
    idle_cash_rate: float = 0.0,        # annual rate accrued daily on the CASH balance.
    # V4 (25 Aug 2026) — a STOP on the ASSIGNED SHARES.
    #
    # The state machine had no exit from shares except being called away, so an
    # assigned position held through any decline indefinitely. META fell 71% and
    # the wheel held every point of it; that is why G4 (worst single-symbol
    # drawdown <= 40%) failed on the full window, and why v3's entry-side trend
    # floor could not reach it — by the time you own the shares, the entry
    # filter is already behind you.
    #
    # 0.0 = OFF, which reproduces v3 exactly and is the control.
    assigned_stop_pct: float = 0.0,
    # 0.0 = v1 behaviour (idle cash scores zero — understates low-utilisation
    # configs: money a premium floor keeps undeployed isn't dead, it earns ~rf;
    # and real CSP collateral itself sits in cash/money-market earning interest).
    # The v2 gates file registers this ON (= rf) BEFORE v2's numbers are known —
    # a measurement-definition fix, not a tuning knob (owner, 11 Aug 2026).
) -> WheelResult:
    """Daily-stepped wheel. FLAT → sell cash-secured put; if assigned → hold
    shares + sell covered calls; called away → back to FLAT. Premiums are
    Black-Scholes priced from trailing realised vol."""
    pricer = BlackScholesPricer(risk_free_rate=rf)
    mult = 100 * max(1, contracts)
    n = len(closes)

    cash = start_capital
    shares = 0
    n_share_stops = 0
    cost_basis = 0.0
    mode = "flat"               # flat | short_put | covered_call
    opt_strike = 0.0
    opt_expiry_idx = -1
    opt_entry_gross = 0.0       # per-share credit at entry, for the managed-close target
    n_managed_closes = 0
    premium_income = 0.0
    realised_pnl = 0.0
    costs_paid = 0.0
    trades: list[WheelTrade] = []
    n_puts = n_assign = n_calls = n_aways = 0
    n_blocked_floor = n_blocked_regime = n_blocked_earnings = n_g5_violations = 0
    n_blocked_trend = 0
    _prints = sorted(earnings_dates or [])

    equity_curve: list[float] = []
    state_by_day: list[str] = []

    def _net_premium(gross: float) -> float:
        """Premium after the pre-registered costs: spread haircut + per-leg
        commission (WHEEL_BACKTEST_GATES.md — all gates grade NET numbers)."""
        nonlocal costs_paid
        haircut = gross * mult * premium_haircut_pct
        costs_paid += haircut + commission_per_leg
        return gross * mult - haircut - commission_per_leg

    def expiry_index(entry_i: int) -> int:
        target = dates[entry_i] + _dt.timedelta(days=dte)
        for j in range(entry_i + 1, n):
            if dates[j] >= target:
                return j
        return n - 1

    daily_rate = (1.0 + idle_cash_rate) ** (1.0 / 252) - 1.0 if idle_cash_rate else 0.0
    for i in range(n):
        spot = closes[i]
        if daily_rate and i >= warmup and cash > 0:
            cash += cash * daily_rate
        if i < warmup or spot <= 0:
            equity_curve.append(cash + shares * spot)
            state_by_day.append("warmup" if i < warmup else mode)
            continue
        sigma = _realised_vol(closes, i)
        t_open = max(dte, 1) / 365.0
        iso = dates[i].isoformat()

        # ── settle an expiring option ────────────────────────────────
        # ── MANAGED CLOSE: take profit before expiry ────────────────────────
        #
        # The harness held every put to expiry or assignment. That is not how
        # this wheel is actually traded: the owner closes once ~60% of the
        # premium has been captured, which frees the collateral to be
        # redeployed and skips the final stretch where gamma risk is highest.
        #
        # It changes BOTH sides of the ledger, which is why modelling it
        # matters rather than adjusting the yield afterwards:
        #   - return: more cycles per year on the same capital
        #   - risk:   fewer puts carried into the window where they get assigned
        #
        # Priced with the same pricer as the equity mark, so the buyback cost is
        # consistent with the liability already being carried. The haircut is
        # applied AGAINST us on the way out (we pay the spread both ways) and a
        # commission is charged for the closing leg — a managed close is two
        # transactions, not one, and pretending otherwise would flatter it.
        if (manage_at_pct > 0.0 and mode == "short_put"
                and i < opt_expiry_idx and opt_entry_gross > 0):
            t_rem_mc = max(opt_expiry_idx - i, 1) / 252.0
            buyback = pricer.price(spot, opt_strike, t_rem_mc, sigma, "put")
            if buyback <= opt_entry_gross * (1.0 - manage_at_pct):
                cost = buyback * mult * (1.0 + premium_haircut_pct) + commission_per_leg
                cash -= cost
                realised_pnl -= cost
                costs_paid += buyback * mult * premium_haircut_pct + commission_per_leg
                captured = 1.0 - (buyback / opt_entry_gross)
                # WheelTrade is (date, action, strike, premium, SPOT, note) —
                # six fields. Passing five put the note into `spot` and left
                # `note` empty, which silently broke the trade log the holding
                # -period measurement reads. Uppercase action to match the
                # SELL_PUT / PUT_EXPIRED convention the other rows use.
                trades.append(WheelTrade(
                    dates[i].isoformat(), "CLOSED_PUT", opt_strike, buyback, spot,
                    f"bought back at {captured:.0%} of premium captured "
                    f"({opt_expiry_idx - i} sessions early)"))
                # "flat", NOT "cash". The idle state in this simulator is
                # "flat"; "cash" is not a state it recognises, so setting it
                # parked the machine permanently — one managed close, then 707
                # idle days and no further trades, while utilisation counted
                # every one of them as in-position. The A/B that produced
                # looked plausible (fewer puts, lower return) and was measuring
                # a jammed state machine.
                mode = "flat"
                opt_strike, opt_expiry_idx, opt_entry_gross = 0.0, -1, 0.0
                n_managed_closes += 1

        if mode in ("short_put", "covered_call") and i >= opt_expiry_idx:
            if mode == "short_put":
                if spot < opt_strike:                      # assigned
                    shares = mult
                    cost_basis = opt_strike
                    cash -= opt_strike * mult              # buy shares (cash was reserved)
                    n_assign += 1
                    trades.append(WheelTrade(iso, "ASSIGNED", opt_strike, None, spot,
                                             f"spot {spot:.2f} < {opt_strike:.2f} → +{mult} sh"))
                    mode = "shares_pending"
                else:                                      # expired worthless
                    trades.append(WheelTrade(iso, "PUT_EXPIRED", opt_strike, None, spot,
                                             "kept premium"))
                    mode = "flat"
            else:  # covered_call
                if spot >= opt_strike:                     # called away
                    cash += opt_strike * mult
                    realised_pnl += (opt_strike - cost_basis) * mult
                    n_aways += 1
                    note = f"sold {mult}sh @{opt_strike:.2f} (basis {cost_basis:.2f})"
                    trades.append(WheelTrade(iso, "CALLED_AWAY", opt_strike, None, spot, note))
                    shares = 0
                    mode = "flat"
                else:
                    trades.append(WheelTrade(iso, "CALL_EXPIRED", opt_strike, None, spot,
                                             "kept premium, still hold shares"))
                    mode = "shares_pending"

        # ── open a new option when idle (v2: live gates first) ───────
        def _clears_floor(prem_: float, strike_: float, days_: int) -> bool:
            """Premium floor, BOTH legs (live rule: don't tie up collateral
            for pennies). Graded on the GROSS premium — the live screen sees
            the quoted mid; haircut/commission are separate costs."""
            if min_premium_usd and prem_ < min_premium_usd:
                return False
            if min_ann_yield_pct and strike_ > 0 and days_ > 0:
                if (prem_ / strike_) * (365.0 / days_) * 100.0 < min_ann_yield_pct:
                    return False
            return True

        if mode == "flat":
            strike = round(spot * (1 - otm_pct))
            if strike > 0 and cash >= strike * mult:       # cash-secured
                exp_i = expiry_index(i)
                hold_days = max((dates[exp_i] - dates[i]).days, 1)
                # (a) REGIME — new short puts only in constructive tape.
                _reg = regime_by_day[i] if (regime_by_day and i < len(regime_by_day)) else None
                _knife = knife_by_day[i] if (knife_by_day and i < len(knife_by_day)) else False
                blocked = None
                if _reg is not None and (_knife or _reg not in ("GREEN", "YELLOW")):
                    blocked = "regime"
                # (b) EARNINGS — no new premium sold across a print.
                elif earnings_modelled and any(dates[i] <= p_ <= dates[exp_i] for p_ in _prints):
                    blocked = "earnings"
                # (c) PRIMARY TREND (v3) — never SELL A PUT on a name trading
                # below its own primary trend. This is the one rule aimed at
                # G4's failure: you can survive being assigned, but being
                # assigned into a name already in a primary downtrend is how a
                # slice grinds to −50%. Deliberately NOT applied to the covered
                # -call repair leg below: once you already hold the shares,
                # refusing to sell calls would remove the only income repairing
                # the position — it would make the failure worse, not better.
                elif (trend_modelled and trend_ok_by_day is not None
                        and i < len(trend_ok_by_day) and not trend_ok_by_day[i]):
                    blocked = "trend"
                if blocked == "regime":
                    n_blocked_regime += 1
                elif blocked == "earnings":
                    n_blocked_earnings += 1
                elif blocked == "trend":
                    n_blocked_trend += 1
                else:
                    prem = pricer.price(spot, strike, t_open, sigma, "put")
                    # (c) PREMIUM FLOOR — last, on the priced credit.
                    if not _clears_floor(prem, strike, hold_days):
                        n_blocked_floor += 1
                    else:
                        net = _net_premium(prem)
                        cash += net
                        premium_income += net
                        opt_strike, opt_expiry_idx = strike, exp_i
                        opt_entry_gross = prem      # per-share credit BEFORE haircut
                        mode = "short_put"
                        n_puts += 1
                        # G5 self-check: independent of the gate above, assert
                        # no print sits inside the window we just opened.
                        if earnings_modelled and any(
                                dates[i] <= p_ <= dates[exp_i] for p_ in _prints):
                            n_g5_violations += 1
                        trades.append(WheelTrade(iso, "SELL_PUT", strike, round(prem, 2), spot,
                                                 f"Δ~ otm {otm_pct:.0%}, dte {dte}, iv {sigma:.0%}"))
        elif mode == "shares_pending":
            # STOP FIRST. Checked on the CLOSE, like every other stop in this
            # codebase, so a gap can go straight through it — the fill is the
            # close we can actually see, not the trigger price.
            if assigned_stop_pct > 0.0 and cost_basis > 0 and spot <= cost_basis * (1 - assigned_stop_pct):
                loss_pct = 100.0 * (spot / cost_basis - 1.0)
                cash += shares * spot
                trades.append(WheelTrade(iso, "STOP_SHARES", round(spot, 2), 0.0, spot,
                                         f"assigned stock stopped at {loss_pct:.1f}% vs cost "
                                         f"{cost_basis:.2f} (limit -{assigned_stop_pct:.0%})"))
                shares = 0
                cost_basis = 0.0
                n_share_stops += 1
                mode = "flat"
                equity_curve.append(cash)
                state_by_day.append(mode)
                continue
            strike = round(max(cost_basis, spot * (1 + otm_pct)))
            exp_i = expiry_index(i)
            hold_days = max((dates[exp_i] - dates[i]).days, 1)
            prem = pricer.price(spot, strike, t_open, sigma, "call")
            # Repair leg obeys the same premium floor — the owner's hand-check
            # found whole XOM/COP repair chains selling $0.00-$0.05 calls that
            # LOSE money after costs. A real trader writes nothing there.
            if not _clears_floor(prem, strike, hold_days):
                n_blocked_floor += 1
            else:
                net = _net_premium(prem)
                cash += net
                premium_income += net
                opt_strike, opt_expiry_idx = strike, exp_i
                mode = "covered_call"
                n_calls += 1
                trades.append(WheelTrade(iso, "SELL_CALL", strike, round(prem, 2), spot,
                                         f"covered, dte {dte}, iv {sigma:.0%}"))

        # ── mark equity (cash + shares − current short-option liability) ──
        liab = 0.0
        if mode in ("short_put", "covered_call"):
            t_rem = max(opt_expiry_idx - i, 1) / 252.0   # trading days → years
            kind = "put" if mode == "short_put" else "call"
            liab = pricer.price(spot, opt_strike, t_rem, sigma, kind) * mult
        equity_curve.append(cash + shares * spot - liab)
        state_by_day.append(mode)

    final_equity = equity_curve[-1] if equity_curve else start_capital
    days = (dates[-1] - dates[warmup]).days if n > warmup else 1
    years = max(days / 365.0, 1e-9)
    total_ret = (final_equity / start_capital - 1) * 100
    cagr = ((final_equity / start_capital) ** (1 / years) - 1) * 100

    # max drawdown on the equity curve
    peak = -math.inf
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v / peak - 1) * 100)

    # comparisons over the same window
    bh_shares = math.floor(start_capital / closes[warmup]) if closes[warmup] > 0 else 0
    bh_final = (start_capital - bh_shares * closes[warmup]) + bh_shares * closes[-1]
    bh_ret = (bh_final / start_capital - 1) * 100
    bank_ret = ((1 + rf) ** years - 1) * 100

    post = slice(warmup, None)
    post_states = state_by_day[post]
    active = sum(1 for s in post_states if s not in ("flat", "warmup"))
    return WheelResult(
        symbol="", start=dates[warmup].isoformat() if n > warmup else dates[0].isoformat(),
        end=dates[-1].isoformat(), contracts=contracts, start_capital=start_capital,
        final_equity=final_equity, total_return_pct=total_ret, cagr_pct=cagr,
        premium_income=premium_income, realised_pnl=realised_pnl, max_drawdown_pct=max_dd,
        n_managed_closes=n_managed_closes,
        n_puts_sold=n_puts, n_assignments=n_assign, n_calls_sold=n_calls, n_call_aways=n_aways,
        days=days, buy_hold_return_pct=bh_ret, bank_return_pct=bank_ret, trades=trades,
        curve_dates=[d.isoformat() for d in dates[post]],
        equity_curve=equity_curve[post],
        state_by_day=post_states,
        utilisation_pct=round(active / max(1, len(post_states)) * 100, 1),
        costs_paid=round(costs_paid, 2),
        n_blocked_floor=n_blocked_floor, n_blocked_regime=n_blocked_regime,
        n_blocked_earnings=n_blocked_earnings, n_g5_violations=n_g5_violations,
        earnings_modelled=earnings_modelled,
        n_blocked_trend=n_blocked_trend, trend_modelled=trend_modelled,
    )


__all__ = ["WheelTrade", "WheelResult", "simulate_wheel"]
