"""Per-symbol "is now a good time to buy?" snapshot.

Strategies tell you which ETF has the best long-run risk-adjusted return.
That answers "what to own". It does NOT answer "should I buy *today* or
wait" — and a green BUY signal at the top of a parabolic move is not the
same thing as a green BUY signal after a 20% correction.

This module computes a transparent, rule-based verdict for any symbol's
recent price action:

    BUY   — trend up, not overbought, not extended
    HOLD  — already in a healthy uptrend, no fresh entry edge
    WAIT  — overbought / extended / mid-drawdown — better entries likely
    AVOID — clear downtrend, fighting the tape

Each verdict carries a one-line reason so the website can show *why*.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .indicators import rsi, sma


# Thresholds are deliberately conservative and easy to reason about.
# The whole point is that a human can look at the numbers and agree.
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
EXTENDED_PCT_FROM_HIGH = 1.0       # within 1% of 52w high → "extended"
MID_DRAWDOWN_PCT = -10.0           # in active drawdown ≥ 10% → "wait for stabilisation"
DEEP_DRAWDOWN_PCT = -20.0          # ≥ 20% drawdown → "potential opportunity if trend recovers"
WEAK_MOMENTUM_PCT = -10.0          # 12m return < -10% → downtrend confirmation


@dataclass
class MarketState:
    symbol: str
    as_of: str | None
    last_price: float | None
    sma_200: float | None
    above_sma_200: bool | None
    pct_off_52w_high_pct: float | None
    drawdown_from_peak_pct: float | None
    rsi_14: float | None
    momentum_3m_pct: float | None
    momentum_12m_pct: float | None
    vol_30d_annual_pct: float | None
    entry_signal: str           # BUY / HOLD / WAIT / AVOID
    entry_reason: str
    # Each item: {"name", "status" ∈ pass|warn|fail, "detail"}.
    # The trace is the audit trail behind entry_signal — every check the
    # classifier looked at, not just the one that fired.
    decision_trace: list[dict[str, Any]]
    # When + at what price the 52w high and running peak were set.
    # These give a percentage like "−20.7% off 52w high" the context
    # it needs ("set 11 months ago, before the April crash") so the
    # user (or the LLM) doesn't conflate a recovery rally with an
    # all-time-high entry. Optional so callers that don't need them
    # can still construct a MarketState positionally.
    pct_off_52w_high_date: str | None = None
    pct_off_52w_high_price: float | None = None
    peak_price: float | None = None
    peak_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "last_price": self.last_price,
            "sma_200": self.sma_200,
            "above_sma_200": self.above_sma_200,
            "pct_off_52w_high_pct": self.pct_off_52w_high_pct,
            "pct_off_52w_high_date": self.pct_off_52w_high_date,
            "pct_off_52w_high_price": self.pct_off_52w_high_price,
            "drawdown_from_peak_pct": self.drawdown_from_peak_pct,
            "peak_price": self.peak_price,
            "peak_date": self.peak_date,
            "rsi_14": self.rsi_14,
            "momentum_3m_pct": self.momentum_3m_pct,
            "momentum_12m_pct": self.momentum_12m_pct,
            "vol_30d_annual_pct": self.vol_30d_annual_pct,
            "entry_signal": self.entry_signal,
            "entry_reason": self.entry_reason,
            "decision_trace": list(self.decision_trace),
        }


def _safe_float(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _annual_vol_pct(closes: pd.Series, lookback: int = 30) -> float | None:
    if len(closes) < lookback + 1:
        return None
    rets = closes.pct_change().tail(lookback)
    if rets.std() == 0 or rets.empty:
        return None
    return float(rets.std() * (252 ** 0.5) * 100.0)


def _momentum_pct(closes: pd.Series, days: int) -> float | None:
    if len(closes) < days + 1:
        return None
    past = closes.iloc[-days - 1]
    last = closes.iloc[-1]
    if past <= 0:
        return None
    return float((last / past - 1.0) * 100.0)


def _build_trace(state: MarketState) -> list[dict[str, Any]]:
    """Build the audit trail of every check that goes into the verdict.
    Returned independently of the verdict so the UI can render it as a
    transparent checklist regardless of which rule fires."""
    trace: list[dict[str, Any]] = []

    # Trend
    if state.above_sma_200 is True:
        trace.append({"name": "Trend (200-day SMA)", "status": "pass",
                      "detail": f"price {state.last_price:,.2f} above SMA {state.sma_200:,.2f}"})
    elif state.above_sma_200 is False:
        trace.append({"name": "Trend (200-day SMA)", "status": "fail",
                      "detail": f"price {state.last_price:,.2f} below SMA {state.sma_200:,.2f}"})
    else:
        trace.append({"name": "Trend (200-day SMA)", "status": "warn",
                      "detail": "not enough history (<200 bars)"})

    # RSI
    rsi_v = state.rsi_14
    if rsi_v is None:
        trace.append({"name": "RSI (14-day)", "status": "warn", "detail": "—"})
    elif rsi_v >= RSI_OVERBOUGHT:
        trace.append({"name": "RSI (14-day)", "status": "fail",
                      "detail": f"{rsi_v:.0f} — overbought, pullback often follows"})
    elif rsi_v <= RSI_OVERSOLD:
        trace.append({"name": "RSI (14-day)", "status": "warn",
                      "detail": f"{rsi_v:.0f} — oversold (bounce candidate)"})
    else:
        trace.append({"name": "RSI (14-day)", "status": "pass",
                      "detail": f"{rsi_v:.0f} — healthy zone"})

    # Distance from 52w high — include the date so a "20% drawdown"
    # against an 11-month-old high doesn't read like the symbol fell
    # 20% yesterday.
    pct = state.pct_off_52w_high_pct
    high_when = (state.pct_off_52w_high_date or "")[:10]
    high_suffix = f" (high set {high_when})" if high_when else ""
    if pct is None:
        trace.append({"name": "Distance from 52w high", "status": "warn", "detail": "—"})
    elif pct < EXTENDED_PCT_FROM_HIGH:
        trace.append({"name": "Distance from 52w high", "status": "warn",
                      "detail": f"{pct:.1f}% off{high_suffix} — at the highs, potentially extended"})
    else:
        trace.append({"name": "Distance from 52w high", "status": "pass",
                      "detail": f"{pct:.1f}% off{high_suffix} — room to run"})

    # Drawdown from peak
    dd = state.drawdown_from_peak_pct
    if dd is None:
        trace.append({"name": "Drawdown from peak", "status": "warn", "detail": "—"})
    elif dd <= DEEP_DRAWDOWN_PCT:
        trace.append({"name": "Drawdown from peak", "status": "warn",
                      "detail": f"{dd:.1f}% — deep correction, classic bounce zone if trend recovers"})
    elif dd <= MID_DRAWDOWN_PCT:
        trace.append({"name": "Drawdown from peak", "status": "fail",
                      "detail": f"{dd:.1f}% — mid-drawdown, trend not stabilised"})
    else:
        trace.append({"name": "Drawdown from peak", "status": "pass",
                      "detail": f"{dd:.1f}% from peak — minimal"})

    # 12-month momentum
    mom12 = state.momentum_12m_pct
    if mom12 is None:
        trace.append({"name": "12-month momentum", "status": "warn", "detail": "—"})
    elif mom12 < WEAK_MOMENTUM_PCT:
        trace.append({"name": "12-month momentum", "status": "fail",
                      "detail": f"{mom12:.1f}% — weak, downtrend signal"})
    else:
        trace.append({"name": "12-month momentum", "status": "pass",
                      "detail": f"{mom12:+.1f}% — positive"})

    return trace


def _classify(state: MarketState) -> tuple[str, str]:
    """Map the snapshot to a (signal, reason) pair. Rules are intentionally
    short and explicit — easier to argue with than a black-box score.
    The decision_trace built separately surfaces the full audit trail."""
    above = state.above_sma_200
    pct_off_high = state.pct_off_52w_high_pct
    rsi_v = state.rsi_14
    mom12 = state.momentum_12m_pct
    dd = state.drawdown_from_peak_pct

    # AVOID: confirmed downtrend (below SMA200 + 12-month return clearly negative).
    if above is False and mom12 is not None and mom12 < WEAK_MOMENTUM_PCT:
        return ("AVOID",
                f"below 200-day SMA and 12m return {mom12:.1f}% — confirmed downtrend, fighting the tape.")

    # WAIT: stretched right at the highs, fading entries usually do better.
    if pct_off_high is not None and pct_off_high < EXTENDED_PCT_FROM_HIGH and rsi_v is not None and rsi_v >= RSI_OVERBOUGHT:
        return ("WAIT",
                f"at 52w high with RSI {rsi_v:.0f} (overbought) — let it cool before adding.")

    # WAIT: mid-drawdown, trend not yet stabilised.
    if dd is not None and DEEP_DRAWDOWN_PCT < dd <= MID_DRAWDOWN_PCT:
        return ("WAIT",
                f"in {dd:.1f}% drawdown — wait for trend stabilisation before averaging in.")

    # BUY: deeper drawdown but RSI bouncing — classic mean-reversion entry.
    if dd is not None and dd <= DEEP_DRAWDOWN_PCT and rsi_v is not None and rsi_v > RSI_OVERSOLD:
        peak_when = (state.peak_date or "")[:10]
        peak_suffix = f" (peak set {peak_when})" if peak_when else ""
        return ("BUY",
                f"{dd:.1f}% drawdown{peak_suffix} with RSI {rsi_v:.0f} "
                f"recovering — historical bounce zone.")

    # BUY: clean uptrend (above SMA200, not overbought, not extended).
    if above is True:
        if rsi_v is None or rsi_v < RSI_OVERBOUGHT:
            if pct_off_high is None or pct_off_high >= EXTENDED_PCT_FROM_HIGH:
                return ("BUY",
                        f"above 200-day SMA, RSI {rsi_v:.0f} healthy" if rsi_v is not None else
                        "above 200-day SMA, healthy entry zone.")

    # HOLD: anything else — neither clearly attractive nor clearly avoid.
    return ("HOLD",
            "no fresh entry edge — keep position if held, no rush to add.")


def market_state(symbol: str, prices: pd.DataFrame) -> MarketState:
    """Compute the now-or-wait snapshot for one symbol.

    Expects an OHLCV DataFrame with `adj_close` (or `close`) and a
    DatetimeIndex. Returns a MarketState with all numeric metrics + a
    rule-based verdict.
    """
    if prices.empty:
        return MarketState(symbol=symbol, as_of=None, last_price=None,
                           sma_200=None, above_sma_200=None,
                           pct_off_52w_high_pct=None, drawdown_from_peak_pct=None,
                           rsi_14=None, momentum_3m_pct=None,
                           momentum_12m_pct=None, vol_30d_annual_pct=None,
                           entry_signal="HOLD", entry_reason="no data")

    series = prices["adj_close"] if "adj_close" in prices.columns else prices["close"]

    last_price = _safe_float(series.iloc[-1])
    as_of = prices.index[-1].isoformat()

    sma_series = sma(series, 200)
    sma_200 = _safe_float(sma_series.iloc[-1])
    above = None if sma_200 is None or last_price is None else bool(last_price > sma_200)

    # 52-week high / drawdown — use trailing 252 trading days. We
    # capture both the date and the price of the high so a user
    # reading "−20% off 52w high" can immediately see *when* that
    # high was set and reconcile against what their broker shows
    # (recent rally peak vs. pre-crash high are different things).
    window_252 = series.tail(252)
    high_52w = _safe_float(window_252.max())
    high_52w_date: str | None = None
    if not window_252.empty and high_52w is not None:
        idx = window_252.idxmax()
        try:
            high_52w_date = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        except Exception:  # noqa: BLE001
            high_52w_date = str(idx)
    pct_off_high = (
        (1.0 - last_price / high_52w) * 100.0
        if last_price is not None and high_52w not in (None, 0)
        else None
    )

    # Drawdown from running peak, full-series. This is the more honest
    # measure of "are we mid-correction?" than just the 52-week notion.
    # Capture peak date too — same rationale as the 52w-high date.
    peak = series.cummax()
    dd_series = (series - peak) / peak
    dd = _safe_float(dd_series.iloc[-1] * 100.0)
    peak_price = _safe_float(peak.iloc[-1]) if not peak.empty else None
    peak_date: str | None = None
    if not series.empty:
        # The most recent index where price equalled the running peak
        # is the date the peak was last touched. For a clean uptrend
        # this is "today"; for a correction it's the pre-correction high.
        peak_idx = series[series == peak].index
        if len(peak_idx) > 0:
            last_peak = peak_idx[-1]
            try:
                peak_date = last_peak.isoformat() if hasattr(last_peak, "isoformat") else str(last_peak)
            except Exception:  # noqa: BLE001
                peak_date = str(last_peak)

    rsi_14 = _safe_float(rsi(series, 14).iloc[-1])
    mom_3m = _momentum_pct(series, 63)
    mom_12m = _momentum_pct(series, 252)
    vol_30d = _annual_vol_pct(series, 30)

    state = MarketState(
        symbol=symbol, as_of=as_of, last_price=last_price,
        sma_200=sma_200, above_sma_200=above,
        pct_off_52w_high_pct=pct_off_high, drawdown_from_peak_pct=dd,
        rsi_14=rsi_14, momentum_3m_pct=mom_3m, momentum_12m_pct=mom_12m,
        vol_30d_annual_pct=vol_30d,
        entry_signal="HOLD", entry_reason="", decision_trace=[],
        pct_off_52w_high_date=high_52w_date,
        pct_off_52w_high_price=high_52w,
        peak_price=peak_price,
        peak_date=peak_date,
    )
    state.entry_signal, state.entry_reason = _classify(state)
    state.decision_trace = _build_trace(state)
    return state
