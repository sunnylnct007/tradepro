"""Macro / sentiment context fetched once per comparator run.

Real news sentiment requires a feed (NewsAPI / Alpha Vantage / scraping)
that costs money or quota. The cheap proxy that covers most of the same
ground for free is:

- VIX  — fear gauge (^VIX on Yahoo). >25 = stressed market.
- 10Y  — rate-shock proxy (^TNX). Direction over 30d signals macro tone.
- SPY  — broad-market drawdown from peak. >5% off = correction territory.
- HYG  — credit stress proxy (iShares HY Bond ETF). Drawdown > 4% = amber.
- Active stress regime — if today falls inside any window in regimes.py.

`risk_mode` is the single computed gate downstream models read:
  1 (GREEN)  — all clear; full position sizing allowed
  2 (AMBER)  — caution; new entries reduced to 60% sizing
  3 (RED)    — risk-off; paper-only, no new live entries

The narrative `summary` remains informational for human review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from .cache import ensure_cached
from .regimes import REGIMES, Regime


@dataclass
class MarketContext:
    as_of: str | None
    vix: float | None
    vix_regime: str | None             # calm / normal / stressed / None
    tnx: float | None                  # 10Y yield % (Yahoo's ^TNX is yield × 10, scaled here)
    tnx_change_30d: float | None       # absolute change in % over the past 30 days
    tnx_trend: str | None              # rising / falling / flat / None
    spy_drawdown_pct: float | None
    hyg_drawdown_pct: float | None     # HYG drawdown from 52w high — credit stress proxy
    active_stress_regimes: list[str]   # regime keys whose date range includes today
    risk_mode: int                     # 1=GREEN 2=AMBER 3=RED — gate for signal models
    summary: str

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "vix": self.vix,
            "vix_regime": self.vix_regime,
            "tnx": self.tnx,
            "tnx_change_30d": self.tnx_change_30d,
            "tnx_trend": self.tnx_trend,
            "spy_drawdown_pct": self.spy_drawdown_pct,
            "hyg_drawdown_pct": self.hyg_drawdown_pct,
            "active_stress_regimes": self.active_stress_regimes,
            "risk_mode": self.risk_mode,
            "summary": self.summary,
        }


def _safe_fetch(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    try:
        return ensure_cached("yahoo", symbol, start, end)
    except Exception:
        return pd.DataFrame()


def _vix_regime(level: float | None) -> str | None:
    if level is None:
        return None
    if level >= 25:
        return "stressed"
    if level >= 15:
        return "normal"
    return "calm"


def _tnx_trend(change: float | None) -> str | None:
    if change is None:
        return None
    if change > 0.20:
        return "rising"
    if change < -0.20:
        return "falling"
    return "flat"


def _series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    return df["adj_close"] if "adj_close" in df.columns else df["close"]


def _hyg_drawdown(start: datetime, end: datetime) -> float | None:
    """HYG drawdown from its 52-week high — credit stress proxy.
    A spread-widening event shows up as HYG selling off relative to its peak."""
    hyg_df = _safe_fetch("HYG", start, end)
    s = _series(hyg_df)
    if s.empty:
        return None
    peak = float(s.cummax().iloc[-1])
    last = float(s.iloc[-1])
    if peak <= 0:
        return None
    return (last / peak - 1.0) * 100.0


def _compute_risk_mode(
    vix: float | None,
    tnx_change: float | None,
    hyg_dd: float | None,
    active_regimes: list[str],
) -> int:
    """Derive risk_mode 1/2/3 from the available macro inputs.

    RED (3) — hard stop on new live entries:
      - VIX ≥ 32, OR
      - HYG drawdown ≤ -8% (severe credit stress), OR
      - Inside an active historical stress regime

    AMBER (2) — reduced sizing (60% of normal):
      - VIX ≥ 22, OR
      - HYG drawdown ≤ -4%, OR
      - 10Y yield rising > 0.40% over 30d

    GREEN (1) — all clear.
    """
    if active_regimes:
        return 3
    if vix is not None and vix >= 32:
        return 3
    if hyg_dd is not None and hyg_dd <= -8.0:
        return 3
    if vix is not None and vix >= 22:
        return 2
    if hyg_dd is not None and hyg_dd <= -4.0:
        return 2
    if tnx_change is not None and tnx_change > 0.40:
        return 2
    return 1


def market_context(start: datetime, end: datetime) -> MarketContext:
    """Snapshot the macro context as of the latest available bar in the
    [start, end] window. Tolerant: any fetch failing leaves that field null
    rather than blowing up the whole comparator run."""
    vix_df = _safe_fetch("^VIX", start, end)
    tnx_df = _safe_fetch("^TNX", start, end)
    spy_df = _safe_fetch("SPY", start, end)

    vix_series = _series(vix_df)
    tnx_series = _series(tnx_df)
    spy_series = _series(spy_df)

    vix_last = float(vix_series.iloc[-1]) if not vix_series.empty else None
    # Yahoo's ^TNX gives the 10Y yield directly as a percent (e.g. 4.35).
    tnx_last = float(tnx_series.iloc[-1]) if not tnx_series.empty else None
    tnx_change = None
    if tnx_last is not None and len(tnx_series) > 30:
        past = float(tnx_series.iloc[-31])
        tnx_change = tnx_last - past

    spy_dd_pct = None
    if not spy_series.empty:
        peak = float(spy_series.cummax().iloc[-1])
        last = float(spy_series.iloc[-1])
        if peak > 0:
            spy_dd_pct = (last / peak - 1.0) * 100.0

    hyg_dd_pct = _hyg_drawdown(start, end)

    today = pd.Timestamp(datetime.now(timezone.utc))
    active = _active_regimes(today, REGIMES)
    risk_mode = _compute_risk_mode(vix_last, tnx_change, hyg_dd_pct, active)

    parts: list[str] = []
    if vix_last is not None:
        parts.append(f"VIX {vix_last:.1f} ({_vix_regime(vix_last)})")
    if tnx_last is not None:
        trend = _tnx_trend(tnx_change)
        if trend:
            parts.append(f"10Y {tnx_last:.2f}% ({trend})")
        else:
            parts.append(f"10Y {tnx_last:.2f}%")
    if hyg_dd_pct is not None and hyg_dd_pct < -2.0:
        parts.append(f"HYG {hyg_dd_pct:.1f}% from peak")
    if spy_dd_pct is not None and spy_dd_pct < -3.0:
        parts.append(f"S&P {spy_dd_pct:.1f}% from peak")
    if active:
        parts.append(f"active stress regime: {', '.join(active)}")
    risk_labels = {1: "GREEN", 2: "AMBER", 3: "RED"}
    parts.append(f"risk_mode={risk_labels[risk_mode]}")

    summary = " · ".join(parts) if parts else "calm market — no macro flags"

    as_of = None
    for s in (vix_series, tnx_series, spy_series):
        if not s.empty:
            as_of = s.index[-1].isoformat()
            break

    return MarketContext(
        as_of=as_of,
        vix=vix_last,
        vix_regime=_vix_regime(vix_last),
        tnx=tnx_last,
        tnx_change_30d=tnx_change,
        tnx_trend=_tnx_trend(tnx_change),
        spy_drawdown_pct=spy_dd_pct,
        hyg_drawdown_pct=hyg_dd_pct,
        active_stress_regimes=active,
        risk_mode=risk_mode,
        summary=summary,
    )


def _active_regimes(today: pd.Timestamp, regimes: list[Regime]) -> list[str]:
    """Which named historical regimes (if any) overlap today's date — i.e. the
    market is still inside one of the canonical stress windows. Most days
    this is empty; useful when re-pushing during an actual event."""
    out = []
    for r in regimes:
        if pd.Timestamp(r.start) <= today <= pd.Timestamp(r.end):
            out.append(r.key)
    return out
