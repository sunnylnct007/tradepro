"""QDB (Quality Dip Buyer) E4 — full-universe event-study engine.

Validates the QDB hypothesis (design v0.9, see the `project_qdb_strategy_
candidate` memory note): large single-day declines in quality large-caps
mean-revert over 4-9 weeks ONLY when the name is above a rising 200-day
SMA (an overreaction/liquidity event); the same decline below that line
tends to continue (a real information event).

Tests this across the FULL requested universe INCLUDING historical losers
(INTC, NKE, BA) so the pilot's survivor bias (7 names, NVDA alone = 108 of
272 events, winners-only universe) can't hide inside the numbers. In/out-
of-sample split so a parameter that only worked in hindsight gets caught.

SCOPE, DELIBERATE: this validates the PRICE-ACTION + REGIME + EXIT core
the hypothesis is actually about — not the full production entry-gate
stack (macro regime, earnings blackout, quality grade). Those gates only
ever DEMOTE eligibility at entry; they can't rescue a hypothesis that
fails on price action + regime alone, and each needs its own historical
data engineering (a backtestable macro-regime series, a historical
earnings calendar) that doesn't exist yet. Testing the core first is the
right order — refine the entry gates once the core edge is confirmed real.

NO FALSE POSITIVES: every trade's exit reason is recorded (target / stop /
time-stop / BABA-rule-delayed-stop / still-open-at-window-end); nothing is
silently dropped, and a symbol with insufficient history is skipped with a
visible reason, not silently zero-weighted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr as _atr
from ..indicators import rsi as _rsi
from ..indicators import sma as _sma

log = logging.getLogger("tradepro.qdb.event_study")

# ── Design v0.9 parameters (defaults) ────────────────────────────────
DIP_SINGLE_DAY_PCT = -4.5          # single-day close-to-close trigger
DIP_CUM_3D_PCT = -7.0              # 3-day cumulative trigger
DIP_CUM_3D_RSI_MAX = 35.0          # ...combined with RSI<35
COOLDOWN_SESSIONS = 21             # one entry per symbol per 21 sessions
TARGET_PCT = 9.0                   # +9% profit target
TIME_STOP_SESSIONS = 42            # 42-session time stop
ATR_STOP_MULT = 1.5                # -1.5x ATR hard stop
BABA_RSI_FLOOR = 15.0              # don't exit into RSI<15 on the stop day
MAX_OFF_52W_HIGH_PCT = 25.0        # not already >25% off 52w high
STAMPEDE_CAP = 8                   # >8 simultaneous triggers = market-wide crash


@dataclass
class DipEvent:
    symbol: str
    date: pd.Timestamp
    idx: int
    trigger: str            # "single_day" | "cum_3d_rsi"
    regime: str             # "above_rising_200sma" | "below_or_falling_200sma"
    close: float
    off_52w_high_pct: float


@dataclass
class TradeResult:
    symbol: str
    entry_date: pd.Timestamp
    entry_close: float
    regime: str
    trigger: str
    exit_date: pd.Timestamp | None
    exit_reason: str        # "target" | "stop" | "time_stop" | "baba_delayed_stop" | "open_at_end"
    exit_close: float | None
    return_pct: float | None
    sessions_held: int | None


@dataclass
class SymbolSkip:
    symbol: str
    reason: str


def _off_52w_high_pct(close: pd.Series, i: int) -> float:
    """% below the trailing 252-session high as of bar i (0 = at the high)."""
    lo = max(0, i - 251)
    window = close.iloc[lo:i + 1]
    hi = window.max()
    if hi <= 0:
        return 0.0
    return (window.iloc[-1] / hi - 1.0) * 100.0


def detect_dip_events(prices: pd.DataFrame, symbol: str) -> list[DipEvent]:
    """Find every QDB entry trigger in `prices` (needs columns: close, high,
    low; index = trading dates ascending). Applies the 21-session cooldown
    per symbol so re-triggers on the same drawdown don't double-count."""
    close = prices["close"]
    n = len(close)
    if n < 260:  # need a real 200SMA + 52w window before anything is meaningful
        return []

    sma200 = _sma(close, 200)
    sma200_prev20 = sma200.shift(20)
    rsi14 = _rsi(close, 14)
    ret_1d = close.pct_change() * 100.0
    ret_3d = (close / close.shift(3) - 1.0) * 100.0

    events: list[DipEvent] = []
    last_entry_idx = -COOLDOWN_SESSIONS - 1
    for i in range(200, n):
        if i - last_entry_idx < COOLDOWN_SESSIONS:
            continue
        single = ret_1d.iloc[i] <= DIP_SINGLE_DAY_PCT
        cum = ret_3d.iloc[i] <= DIP_CUM_3D_PCT and rsi14.iloc[i] < DIP_CUM_3D_RSI_MAX
        if not (single or cum):
            continue
        off_high = _off_52w_high_pct(close, i)
        if off_high < -MAX_OFF_52W_HIGH_PCT:
            continue  # already too far off the high — not a fresh dip, a bear market
        sma_now = sma200.iloc[i]
        if pd.isna(sma_now) or pd.isna(sma200_prev20.iloc[i]):
            continue
        above = close.iloc[i] > sma_now
        rising = sma_now > sma200_prev20.iloc[i]
        regime = "above_rising_200sma" if (above and rising) else "below_or_falling_200sma"
        events.append(DipEvent(
            symbol=symbol, date=close.index[i], idx=i,
            trigger="single_day" if single else "cum_3d_rsi",
            regime=regime, close=float(close.iloc[i]), off_52w_high_pct=off_high,
        ))
        last_entry_idx = i
    return events


def simulate_exit(prices: pd.DataFrame, entry: DipEvent) -> TradeResult:
    """Walk forward from `entry`, applying target / ATR stop / time stop with
    the BABA rule (never exit into RSI<15 on the stop day — wait for the
    first up-close, then exit regardless)."""
    close = prices["close"]
    high = prices["high"]
    low = prices["low"]
    n = len(close)
    i0 = entry.idx
    entry_px = entry.close
    atr14 = _atr(high, low, close, 14)
    rsi14 = _rsi(close, 14)
    stop_px = entry_px - ATR_STOP_MULT * (atr14.iloc[i0] if not pd.isna(atr14.iloc[i0]) else entry_px * 0.03)
    target_px = entry_px * (1 + TARGET_PCT / 100.0)

    stop_breached_pending = False
    for offset in range(1, TIME_STOP_SESSIONS + 1):
        i = i0 + offset
        if i >= n:
            break
        c = close.iloc[i]
        if c >= target_px:
            return _result(entry, close.index[i], c, "target", offset)
        if c <= stop_px or stop_breached_pending:
            # BABA rule: don't sell into a capitulation RSI print — wait for
            # the first up-close (today's close > yesterday's), then exit.
            r = rsi14.iloc[i]
            up_close = c > close.iloc[i - 1]
            if (not pd.isna(r) and r < BABA_RSI_FLOOR) and not up_close:
                stop_breached_pending = True
                continue
            reason = "baba_delayed_stop" if stop_breached_pending else "stop"
            return _result(entry, close.index[i], c, reason, offset)

    # Time stop: exit at the 42nd session's close, or the last available bar
    # if the series ends first (still counts — it's what would have happened).
    last_offset = min(TIME_STOP_SESSIONS, n - 1 - i0)
    if last_offset <= 0:
        return _result(entry, None, None, "open_at_end", None)
    i = i0 + last_offset
    reason = "time_stop" if i0 + TIME_STOP_SESSIONS < n else "open_at_end"
    return _result(entry, close.index[i], close.iloc[i], reason, last_offset)


def _result(entry: DipEvent, exit_date, exit_close, reason: str, sessions: int | None) -> TradeResult:
    ret = None
    if exit_close is not None:
        ret = (exit_close / entry.close - 1.0) * 100.0
    return TradeResult(
        symbol=entry.symbol, entry_date=entry.date, entry_close=entry.close,
        regime=entry.regime, trigger=entry.trigger,
        exit_date=exit_date, exit_reason=reason, exit_close=exit_close,
        return_pct=ret, sessions_held=sessions,
    )


def run_symbol(symbol: str, prices: pd.DataFrame) -> tuple[list[TradeResult], SymbolSkip | None]:
    """All QDB trades for one symbol. Returns (trades, skip) — skip is set
    (trades always []) when there isn't enough history to say anything,
    never a silent empty list with no explanation."""
    if prices is None or prices.empty:
        return [], SymbolSkip(symbol, "no price data")
    if len(prices) < 260:
        return [], SymbolSkip(symbol, f"only {len(prices)} bars — need 260+ for a real 200SMA/52w read")
    events = detect_dip_events(prices, symbol)
    if not events:
        return [], None  # real, valid answer: this symbol never triggered
    trades = [simulate_exit(prices, e) for e in events]
    return trades, None


def apply_stampede_cap(all_trades: list[TradeResult], top_n: int = 4) -> list[TradeResult]:
    """Design rule: >8 simultaneous triggers = a market-wide crash, not
    independent dips — keep only the top `top_n` by (implicit) quality on
    that date. We don't have a quality score wired into this engine yet, so
    this keeps the FIRST top_n encountered per date deterministically and
    flags the day; a real quality-ranked cap is a refinement once the core
    result is validated. Kept as a documented, visible approximation."""
    by_date: dict[pd.Timestamp, list[TradeResult]] = {}
    for t in all_trades:
        by_date.setdefault(t.entry_date, []).append(t)
    kept: list[TradeResult] = []
    stampede_days = 0
    for date, trades in by_date.items():
        if len(trades) > STAMPEDE_CAP:
            stampede_days += 1
            kept.extend(trades[:top_n])
        else:
            kept.extend(trades)
    if stampede_days:
        log.info("stampede cap applied on %d date(s) (>%d simultaneous triggers)",
                  stampede_days, STAMPEDE_CAP)
    return kept


@dataclass
class RegimeStats:
    regime: str
    n_events: int
    hit_rate_pct: float | None       # % with return_pct > 0
    median_return_pct: float | None
    p10_return_pct: float | None
    expectancy_pct: float | None     # mean return_pct (simple, not cost-adjusted)
    max_loss_pct: float | None


def summarise(trades: list[TradeResult]) -> list[RegimeStats]:
    out = []
    for regime in ("above_rising_200sma", "below_or_falling_200sma"):
        rs = [t for t in trades if t.regime == regime and t.return_pct is not None]
        if not rs:
            out.append(RegimeStats(regime, 0, None, None, None, None, None))
            continue
        rets = np.array([t.return_pct for t in rs])
        out.append(RegimeStats(
            regime=regime, n_events=len(rs),
            hit_rate_pct=round(float((rets > 0).mean() * 100), 1),
            median_return_pct=round(float(np.median(rets)), 1),
            p10_return_pct=round(float(np.percentile(rets, 10)), 1),
            expectancy_pct=round(float(rets.mean()), 2),
            max_loss_pct=round(float(rets.min()), 1),
        ))
    return out


__all__ = [
    "DipEvent", "TradeResult", "SymbolSkip", "RegimeStats",
    "detect_dip_events", "simulate_exit", "run_symbol", "apply_stampede_cap", "summarise",
]
