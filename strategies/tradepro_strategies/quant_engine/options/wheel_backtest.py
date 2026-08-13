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
    min_premium_usd: float = 0.0,       # per-share floor, BOTH legs (live: 0.20)
    min_ann_yield_pct: float = 0.0,     # annualised-on-collateral floor (live: 8.0)
    regime_by_day: list[str] | None = None,   # 'GREEN'/'YELLOW'/'ORANGE'/'RED' per bar
    knife_by_day: list[bool] | None = None,   # falling-knife flag per bar
    earnings_dates: list[_dt.date] | None = None,  # historical print dates
    earnings_modelled: bool = False,    # False ⇒ veto inactive (declare coverage!)
    idle_cash_rate: float = 0.0,        # annual rate accrued daily on the CASH balance.
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
    cost_basis = 0.0
    mode = "flat"               # flat | short_put | covered_call
    opt_strike = 0.0
    opt_expiry_idx = -1
    premium_income = 0.0
    realised_pnl = 0.0
    costs_paid = 0.0
    trades: list[WheelTrade] = []
    n_puts = n_assign = n_calls = n_aways = 0
    n_blocked_floor = n_blocked_regime = n_blocked_earnings = n_g5_violations = 0
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
                if blocked == "regime":
                    n_blocked_regime += 1
                elif blocked == "earnings":
                    n_blocked_earnings += 1
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
    )


__all__ = ["WheelTrade", "WheelResult", "simulate_wheel"]
