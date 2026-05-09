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
DEEP_DRAWDOWN_PCT = -20.0          # ≥ 20% drawdown → long-term-valuation signal only
WEAK_MOMENTUM_PCT = -10.0          # 12m return < -10% → downtrend confirmation
# Recent-dip threshold: how far off the 52w high we want before the
# bounce-zone BUY rule fires. Distinct from DEEP_DRAWDOWN_PCT, which
# is a 5y/full-series number — that's a long-term valuation signal,
# not a short-term entry trigger. Conflating the two led to BUY
# verdicts on INRG.L (−0% off 52w high but −22% off 2021 peak).
MEANINGFUL_52W_DROP_PCT = 8.0      # ≥ 8% off the 52w high counts as a real recent dip
# Range position thresholds — where the current price sits as a
# percentile of the 52w (low → high) range. Used to demote BUY
# signals when the symbol is sitting near the top of its annual
# range despite passing the other gates (RSI, SMA, drawdown). The
# VUKE-class case: 5% off 52w high after a +24% YoY run is NOT a
# dip — risk/reward is asymmetric (3p of upside, 8p of downside).
RANGE_HIGH_PCTILE = 70.0           # ≥ 70th pctile of 52w range → downgrade BUY → HOLD
RANGE_LOW_PCTILE = 40.0            # ≤ 40th pctile → confirms "dip" status


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
    # True when the 5y running-peak bar is INSIDE the trailing 252-bar
    # window. When true the 52w high IS the 5y peak — both metrics
    # legitimately read 0% off, NOT a data conflation bug. Surfaced
    # so the UI / rationale can render "at multi-year highs" instead
    # of looking like the two metrics are stuck together.
    peak_within_52w_window: bool = False
    # 52w low (mirror of pct_off_52w_high_price) and the percentile
    # the current price sits at within the (low → high) range. 100 =
    # at the 52w high, 0 = at the 52w low. Surfaces "you're near the
    # top of the year" cleanly so the BUY gate can downgrade when
    # the symbol is at 70th+ pctile despite passing other criteria.
    low_52w_price: float | None = None
    low_52w_date: str | None = None
    range_position_pct: float | None = None

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
            "peak_within_52w_window": self.peak_within_52w_window,
            "low_52w_price": self.low_52w_price,
            "low_52w_date": self.low_52w_date,
            "range_position_pct": self.range_position_pct,
            # Spec-canonical aliases (TRADEPRO-SPEC-001 §6.1).
            # classify_horizons() reads these names verbatim from the
            # market_state payload. Kept alongside the existing
            # `*_price` fields so older consumers don't break.
            "high_52w": self.pct_off_52w_high_price,
            "low_52w": self.low_52w_price,
            "range_pct": self.range_position_pct,
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

    # Long-term valuation: drawdown from full-series running peak.
    # Reported separately from the 52w-high distance so the user can
    # see both timeframes — "−0% off 52w high (near-term peak) but
    # −22% off 2021 peak (long-term cheap)" is a meaningful pair of
    # facts; collapsing them into a single 'drawdown' line is what
    # made INRG.L misread as a short-term BUY.
    #
    # When the 5y peak is INSIDE the trailing 52w window, the two
    # metrics legitimately read the same number — symbol is at
    # multi-year highs. The trace says so explicitly so it doesn't
    # look like a conflation bug.
    dd = state.drawdown_from_peak_pct
    peak_when = (state.peak_date or "")[:10]
    peak_suffix = f" (peak {peak_when})" if peak_when else ""
    coincides = state.peak_within_52w_window
    if dd is None:
        trace.append({"name": "Long-term valuation (5y peak)",
                      "status": "warn", "detail": "—"})
    elif coincides and (dd is None or dd >= -1.0):
        # Peak is within 52w AND price is essentially at peak →
        # at all-time highs in the 5y window
        trace.append({
            "name": "Long-term valuation (5y peak)",
            "status": "warn",
            "detail": (
                f"{dd:.1f}% from peak{peak_suffix} — peak is within 52w window, "
                f"symbol is at multi-year highs (52w high = 5y peak)"
            ),
        })
    elif dd <= DEEP_DRAWDOWN_PCT:
        coincide_note = (
            "" if not coincides else " (peak still within 52w — recent setback)"
        )
        trace.append({
            "name": "Long-term valuation (5y peak)",
            "status": "pass",
            "detail": (
                f"{dd:.1f}% from peak{peak_suffix}{coincide_note} — "
                f"structurally cheap vs own history "
                f"(long-term signal, not a timing trigger)"
            ),
        })
    elif dd <= MID_DRAWDOWN_PCT:
        trace.append({"name": "Long-term valuation (5y peak)", "status": "warn",
                      "detail": f"{dd:.1f}% from peak{peak_suffix} — mid-cycle"})
    else:
        trace.append({"name": "Long-term valuation (5y peak)", "status": "warn",
                      "detail": f"{dd:.1f}% from peak{peak_suffix} — near long-term highs"})

    # Range position within 52w high/low — where the current price
    # sits as a percentile of the annual range. The 52w-high distance
    # (above) tells you "how much room to recover"; this tells you
    # "where in the year are you actually sitting". A symbol 5% off
    # its 52w high but at the 72nd percentile of its range is NOT a
    # dip (VUKE case): downside-to-low far exceeds upside-to-high.
    rp = state.range_position_pct
    if rp is None:
        trace.append({"name": "Range position (52w)", "status": "warn",
                      "detail": "—"})
    elif rp >= RANGE_HIGH_PCTILE:
        trace.append({"name": "Range position (52w)", "status": "fail",
                      "detail": (
                          f"{rp:.0f}th percentile — near 52w highs, "
                          f"asymmetric risk/reward for a swing entry"
                      )})
    elif rp <= RANGE_LOW_PCTILE:
        trace.append({"name": "Range position (52w)", "status": "pass",
                      "detail": (
                          f"{rp:.0f}th percentile — closer to 52w lows, "
                          f"genuine dip territory"
                      )})
    else:
        trace.append({"name": "Range position (52w)", "status": "warn",
                      "detail": f"{rp:.0f}th percentile — mid-range"})

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

    # BUY: meaningful 52w drawdown but RSI bouncing — short-term
    # mean-reversion entry. Uses pct_off_52w_high (recent context),
    # NOT drawdown_from_peak (5y / full-series). The full-series
    # drawdown remains in the trace as a long-term valuation signal,
    # but cannot trigger a BUY on its own — a 5y-old peak does not
    # give you a near-term entry edge. (Fix: INRG.L was BUY-flagged
    # because its 2021 peak yielded −22% even though the 52w high is
    # today; the 5y signal was masquerading as a timing signal.)
    #
    # Fires BEFORE the WAIT-mid-drawdown rule so a real recent dip
    # with momentum doesn't get incorrectly demoted by a coincidental
    # 5y dd in the mid-zone.
    if (
        pct_off_high is not None
        and pct_off_high >= MEANINGFUL_52W_DROP_PCT
        and rsi_v is not None
        and rsi_v > RSI_OVERSOLD
    ):
        high_when = (state.pct_off_52w_high_date or "")[:10]
        high_suffix = f" (52w high {high_when})" if high_when else ""
        return ("BUY",
                f"{pct_off_high:.1f}% off 52w high{high_suffix} with RSI "
                f"{rsi_v:.0f} recovering — short-term bounce zone.")

    # WAIT: mid-drawdown, trend not yet stabilised. Still uses the
    # full-series dd because we want to flag deep-but-not-recovering
    # situations even when the 52w-window is too narrow to see them.
    if dd is not None and DEEP_DRAWDOWN_PCT < dd <= MID_DRAWDOWN_PCT:
        return ("WAIT",
                f"in {dd:.1f}% drawdown — wait for trend stabilisation before averaging in.")

    # BUY: clean uptrend (above SMA200, not overbought, not extended).
    # Plus a range-position guard: if the price is in the upper 30%
    # of its 52w range, the technical BUY is a misleading "buy near
    # highs" — return WAIT (not HOLD) so compute_bucket doesn't
    # promote it back to BUY on strategy consensus. Returning HOLD
    # here was the bug behind XLY / XLI / XLC / VUKE landing in
    # BUY candidates with "wait for a pullback" supporting text.
    if above is True:
        if rsi_v is None or rsi_v < RSI_OVERBOUGHT:
            if pct_off_high is None or pct_off_high >= EXTENDED_PCT_FROM_HIGH:
                rp = state.range_position_pct
                if rp is not None and rp >= RANGE_HIGH_PCTILE:
                    return ("WAIT",
                            f"above 200-day SMA but at {rp:.0f}th percentile "
                            f"of 52w range — near the highs, asymmetric risk/"
                            f"reward for a fresh entry. Wait for a pullback.")
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

    # 52w low + range position. The position is a percentile within
    # the (low → high) range. 100 = at high, 0 = at low. The classify
    # rules use this to demote a near-the-highs BUY to HOLD even when
    # the technical gates pass.
    low_52w = _safe_float(window_252.min())
    low_52w_date: str | None = None
    if not window_252.empty and low_52w is not None:
        idx = window_252.idxmin()
        try:
            low_52w_date = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        except Exception:  # noqa: BLE001
            low_52w_date = str(idx)
    range_position_pct: float | None = None
    if (last_price is not None and high_52w is not None
            and low_52w is not None and high_52w > low_52w):
        range_position_pct = (last_price - low_52w) / (high_52w - low_52w) * 100.0
        # Clamp to [0, 100] in case last_price drifts outside the
        # window (corporate action, stale bar, etc.).
        range_position_pct = max(0.0, min(100.0, range_position_pct))

    # Drawdown from running peak, full-series. This is the more honest
    # measure of "are we mid-correction?" than just the 52-week notion.
    # Capture peak date too — same rationale as the 52w-high date.
    peak = series.cummax()
    dd_series = (series - peak) / peak
    dd = _safe_float(dd_series.iloc[-1] * 100.0)
    peak_price = _safe_float(peak.iloc[-1]) if not peak.empty else None
    peak_date: str | None = None
    peak_within_52w = False
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
            # Is the running peak inside the trailing 252-bar window?
            # If yes, the 52w high IS the 5y peak — both metrics
            # legitimately read the same number, NOT a conflation bug.
            if not window_252.empty:
                peak_within_52w = bool(last_peak >= window_252.index[0])

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
        peak_within_52w_window=peak_within_52w,
        peak_price=peak_price,
        peak_date=peak_date,
        low_52w_price=low_52w,
        low_52w_date=low_52w_date,
        range_position_pct=range_position_pct,
    )
    state.entry_signal, state.entry_reason = _classify(state)
    state.decision_trace = _build_trace(state)
    return state
