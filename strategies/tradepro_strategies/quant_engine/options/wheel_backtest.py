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
    action: str       # SELL_PUT | ASSIGNED | PUT_EXPIRED | SELL_CALL | CALLED_AWAY | CALL_EXPIRED
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
    trades: list[WheelTrade] = []
    n_puts = n_assign = n_calls = n_aways = 0

    equity_curve: list[float] = []

    def expiry_index(entry_i: int) -> int:
        target = dates[entry_i] + _dt.timedelta(days=dte)
        for j in range(entry_i + 1, n):
            if dates[j] >= target:
                return j
        return n - 1

    for i in range(n):
        spot = closes[i]
        if i < warmup or spot <= 0:
            equity_curve.append(cash + shares * spot)
            continue
        sigma = _realised_vol(closes, i)
        t_open = max(dte, 1) / 365.0

        # ── settle an expiring option ────────────────────────────────
        if mode in ("short_put", "covered_call") and i >= opt_expiry_idx:
            if mode == "short_put":
                if spot < opt_strike:                      # assigned
                    shares = mult
                    cost_basis = opt_strike
                    cash -= opt_strike * mult              # buy shares (cash was reserved)
                    n_assign += 1
                    trades.append(WheelTrade(dates[i].isoformat(), "ASSIGNED", opt_strike, None, spot,
                                             f"spot {spot:.2f} < strike {opt_strike:.2f} → bought {mult} sh"))
                    mode = "shares_pending"
                else:                                      # expired worthless
                    trades.append(WheelTrade(dates[i].isoformat(), "PUT_EXPIRED", opt_strike, None, spot,
                                             "kept premium"))
                    mode = "flat"
            else:  # covered_call
                if spot >= opt_strike:                     # called away
                    cash += opt_strike * mult
                    realised_pnl += (opt_strike - cost_basis) * mult
                    n_aways += 1
                    trades.append(WheelTrade(dates[i].isoformat(), "CALLED_AWAY", opt_strike, None, spot,
                                             f"sold {mult} sh @ {opt_strike:.2f} (basis {cost_basis:.2f})"))
                    shares = 0
                    mode = "flat"
                else:
                    trades.append(WheelTrade(dates[i].isoformat(), "CALL_EXPIRED", opt_strike, None, spot,
                                             "kept premium, still hold shares"))
                    mode = "shares_pending"

        # ── open a new option when idle ──────────────────────────────
        if mode == "flat":
            strike = round(spot * (1 - otm_pct))
            if strike > 0 and cash >= strike * mult:       # cash-secured
                prem = pricer.price(spot, strike, t_open, sigma, "put")
                cash += prem * mult
                premium_income += prem * mult
                opt_strike, opt_expiry_idx = strike, expiry_index(i)
                mode = "short_put"
                n_puts += 1
                trades.append(WheelTrade(dates[i].isoformat(), "SELL_PUT", strike, round(prem, 2), spot,
                                         f"Δ~ otm {otm_pct:.0%}, dte {dte}, iv {sigma:.0%}"))
        elif mode == "shares_pending":
            strike = round(max(cost_basis, spot * (1 + otm_pct)))
            prem = pricer.price(spot, strike, t_open, sigma, "call")
            cash += prem * mult
            premium_income += prem * mult
            opt_strike, opt_expiry_idx = strike, expiry_index(i)
            mode = "covered_call"
            n_calls += 1
            trades.append(WheelTrade(dates[i].isoformat(), "SELL_CALL", strike, round(prem, 2), spot,
                                     f"covered, dte {dte}, iv {sigma:.0%}"))

        # ── mark equity (cash + shares − current short-option liability) ──
        liab = 0.0
        if mode == "short_put":
            liab = pricer.price(spot, opt_strike, max((opt_expiry_idx - i), 1) / 365.0 * (dte / max(dte, 1)), sigma, "put") * mult
        elif mode == "covered_call":
            liab = pricer.price(spot, opt_strike, max((opt_expiry_idx - i), 1) / 365.0 * (dte / max(dte, 1)), sigma, "call") * mult
        equity_curve.append(cash + shares * spot - liab)

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

    return WheelResult(
        symbol="", start=dates[warmup].isoformat() if n > warmup else dates[0].isoformat(),
        end=dates[-1].isoformat(), contracts=contracts, start_capital=start_capital,
        final_equity=final_equity, total_return_pct=total_ret, cagr_pct=cagr,
        premium_income=premium_income, realised_pnl=realised_pnl, max_drawdown_pct=max_dd,
        n_puts_sold=n_puts, n_assignments=n_assign, n_calls_sold=n_calls, n_call_aways=n_aways,
        days=days, buy_hold_return_pct=bh_ret, bank_return_pct=bank_ret, trades=trades,
    )


__all__ = ["WheelTrade", "WheelResult", "simulate_wheel"]
