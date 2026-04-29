"""Macro / sentiment context fetched once per comparator run.

Real news sentiment requires a feed (NewsAPI / Alpha Vantage / scraping)
that costs money or quota. The cheap proxy that covers most of the same
ground for free is:

- VIX  — fear gauge (^VIX on Yahoo). >25 = stressed market.
- 10Y  — rate-shock proxy (^TNX). Direction over 30d signals macro tone.
- SPY  — broad-market drawdown from peak. >5% off = correction territory.
- Active stress regime — if today falls inside any window in regimes.py.

The bar produced here is INFORMATIONAL — it doesn't auto-demote bucket
assignments. A human sees "VIX 28 (stressed) — be cautious with BUYs"
and decides; the algorithm doesn't pretend to know better than that.
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
    active_stress_regimes: list[str]   # regime keys whose date range includes today
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
            "active_stress_regimes": self.active_stress_regimes,
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

    today = pd.Timestamp(datetime.now(timezone.utc))
    active = _active_regimes(today, REGIMES)

    parts: list[str] = []
    if vix_last is not None:
        parts.append(f"VIX {vix_last:.1f} ({_vix_regime(vix_last)})")
    if tnx_last is not None:
        trend = _tnx_trend(tnx_change)
        if trend:
            parts.append(f"10Y {tnx_last:.2f}% ({trend})")
        else:
            parts.append(f"10Y {tnx_last:.2f}%")
    if spy_dd_pct is not None and spy_dd_pct < -3.0:
        parts.append(f"S&P {spy_dd_pct:.1f}% from peak")
    if active:
        parts.append(f"active stress regime: {', '.join(active)}")

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
        active_stress_regimes=active,
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
