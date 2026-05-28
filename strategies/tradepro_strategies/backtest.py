"""Minimal event-driven backtester. One symbol, long-only, daily bars.
Matches the backend C# `Simulator` so UK fees behave identically."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class FeeModel:
    commission_per_trade: float = 0.0
    stamp_duty_rate: float = 0.005  # UK default
    fx_spread: float = 0.0


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    currency: str = "GBP"
    fees: FeeModel = field(default_factory=FeeModel)


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: pd.DataFrame
    stats: dict


SignalFn = Callable[[pd.DataFrame], pd.Series]
"""A signal function returns a Series aligned with `prices.index` whose values
are +1 (go long), -1 (exit) or 0 (hold)."""


def run_backtest(prices: pd.DataFrame, signal_fn: SignalFn, config: BacktestConfig) -> BacktestResult:
    if prices.empty:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame(), {})

    # Use total-return prices: dividends + splits are baked into adj_close.
    # Strategies consume prices["close"], so swap close←adj_close for the
    # whole backtest pass. Keeps every existing strategy correct without
    # per-strategy edits.
    if "adj_close" in prices.columns:
        prices = prices.assign(close=prices["adj_close"])

    signals = signal_fn(prices).reindex(prices.index).fillna(0).astype(int)
    cash = config.initial_capital
    qty = 0.0
    fees = config.fees
    equity: list[float] = []
    trade_rows: list[dict] = []

    closes = prices["close"].to_numpy()
    ts = prices.index

    for i, price in enumerate(closes):
        sig = int(signals.iloc[i])
        if sig == 1 and qty == 0 and cash > 0:
            notional = cash - fees.commission_per_trade
            if notional <= 0:
                equity.append(cash + qty * price)
                continue
            effective_price = price * (1.0 + fees.stamp_duty_rate)
            bought = np.floor((notional / effective_price) * 1e4) / 1e4
            if bought > 0:
                stamp = bought * price * fees.stamp_duty_rate
                total_fees = stamp + fees.commission_per_trade
                cash -= bought * price + total_fees
                qty += bought
                trade_rows.append(dict(
                    timestamp=ts[i], side="BUY", price=float(price),
                    quantity=float(bought), fees=float(total_fees),
                ))
        elif sig == -1 and qty > 0:
            proceeds = qty * price - fees.commission_per_trade
            cash += proceeds
            trade_rows.append(dict(
                timestamp=ts[i], side="SELL", price=float(price),
                quantity=float(qty), fees=float(fees.commission_per_trade),
            ))
            qty = 0.0

        equity.append(cash + qty * price)

    # Close out at the end so PnL is realised.
    if qty > 0:
        last = float(closes[-1])
        cash += qty * last - fees.commission_per_trade
        trade_rows.append(dict(
            timestamp=ts[-1], side="SELL", price=last,
            quantity=float(qty), fees=float(fees.commission_per_trade),
        ))
        equity[-1] = cash

    eq = pd.Series(equity, index=ts, name="equity")
    trades = pd.DataFrame(trade_rows)
    stats = _compute_stats(eq, config.initial_capital)
    return BacktestResult(eq, trades, stats)


def _compute_stats(equity: pd.Series, initial: float) -> dict:
    if equity.empty:
        return {}
    final = float(equity.iloc[-1])
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    total_return = final / initial - 1.0
    cagr = (final / initial) ** (1 / years) - 1 if years > 0 else 0.0

    returns = equity.pct_change().dropna()
    sharpe = 0.0
    if returns.std() > 0:
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252))

    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    max_dd = float(drawdown.min())

    # Recovery time: from the bar of the deepest drawdown trough,
    # how many calendar days until equity reclaimed the prior peak?
    # null when the curve hasn't recovered by the end of the series
    # (still in drawdown), with `still_in_drawdown=True` so callers
    # can render "still recovering — N days and counting" honestly.
    # Per the design review: max-DD is half the story; a 30% DD
    # that recovered in 9 months is very different from a 30% DD
    # that took 7 years.
    recovery_days: int | None = None
    still_in_drawdown = False
    days_since_trough: int | None = None
    if not drawdown.empty:
        trough_idx = drawdown.idxmin()
        prior_peak = float(peak.loc[trough_idx])
        post = equity.loc[trough_idx:]
        recovered = post[post >= prior_peak]
        if not recovered.empty:
            recovery_days = int((recovered.index[0] - trough_idx).days)
        else:
            still_in_drawdown = True
            days_since_trough = int((equity.index[-1] - trough_idx).days)

    return dict(
        final_equity=final,
        total_return_pct=total_return * 100.0,
        cagr_pct=cagr * 100.0,
        sharpe=sharpe,
        max_drawdown_pct=max_dd * 100.0,
        max_drawdown_recovery_days=recovery_days,
        max_drawdown_still_recovering=still_in_drawdown,
        days_since_max_dd_trough=days_since_trough,
    )
