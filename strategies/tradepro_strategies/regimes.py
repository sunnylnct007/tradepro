"""Named historical regimes for stress-testing strategy results.

A regime is a date window with a label. Given an equity curve (or any
time-indexed series), `regime_stats` slices the curve to the overlap with
each window and returns return / drawdown for that slice. This lets the
ranker say "Strategy X on ETF Y returned 158% overall — but lost 34%
during COVID and 21% during the 2022 rate shock."

Windows are inclusive on both ends. They cover both crashes (negative
regimes) and notable recoveries / rallies (positive regimes), because
"how did it behave when the market rallied 70% in 9 months" is just as
informative as "how did it behave in March 2020".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Regime:
    key: str
    name: str
    start: datetime
    end: datetime
    kind: str  # "crash" | "recovery" | "drawdown"
    description: str


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


REGIMES: list[Regime] = [
    Regime("dotcom_bust", "Dot-com bust",
           _utc(2000, 3, 24), _utc(2002, 10, 9),
           "crash",
           "S&P 500 fell ~49% peak-to-trough as the late-90s tech bubble unwound."),
    Regime("gfc", "Global Financial Crisis",
           _utc(2007, 10, 9), _utc(2009, 3, 9),
           "crash",
           "Lehman, AIG, and the credit-market freeze. S&P 500 -57% peak-to-trough."),
    Regime("gfc_recovery", "Post-GFC recovery",
           _utc(2009, 3, 10), _utc(2010, 4, 23),
           "recovery",
           "13-month rally as central banks flooded markets with liquidity."),
    Regime("eurozone_2011", "Eurozone debt crisis",
           _utc(2011, 7, 22), _utc(2011, 10, 3),
           "drawdown",
           "S&P downgrade of US debt + Greek/Italian bond fears. ~19% drawdown."),
    Regime("china_devaluation_2015", "China devaluation / oil crash",
           _utc(2015, 8, 17), _utc(2016, 2, 11),
           "drawdown",
           "PBoC yuan devaluation, oil to $26, S&P -14% peak-to-trough."),
    Regime("volmageddon_2018", "Volmageddon",
           _utc(2018, 2, 1), _utc(2018, 2, 9),
           "crash",
           "Short-vol ETN blowup. VIX 4x in two days, S&P -10% in a week."),
    Regime("q4_2018", "Q4 2018 selloff",
           _utc(2018, 10, 1), _utc(2018, 12, 25),
           "drawdown",
           "Fed hike + trade-war fears. S&P -19% peak-to-trough into Christmas."),
    Regime("covid_crash", "COVID-19 crash",
           _utc(2020, 2, 19), _utc(2020, 3, 23),
           "crash",
           "Fastest 30%+ S&P drawdown in history (33 days)."),
    Regime("covid_rally", "Post-COVID rally",
           _utc(2020, 3, 24), _utc(2021, 1, 8),
           "recovery",
           "Liquidity-fuelled rebound. S&P doubled from the March low in 9 months."),
    Regime("rate_shock_2022", "2022 rate shock",
           _utc(2022, 1, 3), _utc(2022, 10, 14),
           "drawdown",
           "Fastest Fed hiking cycle since 1980s. S&P -25%, AGG -17%, growth -35%."),
    Regime("svb_march_2023", "Regional banking crisis",
           _utc(2023, 3, 8), _utc(2023, 3, 17),
           "drawdown",
           "SVB collapse + Credit Suisse rescue. Sharp financials drawdown, broad market shrugged."),
    Regime("aug_2024_unwind", "Aug 2024 yen-carry unwind",
           _utc(2024, 7, 31), _utc(2024, 8, 5),
           "crash",
           "BoJ hike sparked global carry-trade unwind; Nikkei -12% on a single day."),
    Regime("tariff_shock_2025", "2025 tariff shock",
           _utc(2025, 4, 1), _utc(2025, 4, 9),
           "crash",
           "Reciprocal-tariff announcement. S&P -12% in 4 sessions before partial reversal."),
]


def by_key(key: str) -> Regime:
    for r in REGIMES:
        if r.key == key:
            return r
    raise KeyError(f"unknown regime '{key}'. Available: {[r.key for r in REGIMES]}")


def regime_stats(equity: pd.Series, regime: Regime) -> dict:
    """Slice `equity` to the regime window and report return + max-DD.

    Returns a dict with start_observed/end_observed (the actual covered
    window after intersecting with the equity curve), bars, return_pct,
    and max_drawdown_pct. If there's no overlap, fields are NaN/0.
    """
    if equity.empty:
        return _empty_regime_row(regime)

    idx = equity.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
        equity = equity.copy()
        equity.index = idx

    start = pd.Timestamp(regime.start)
    end = pd.Timestamp(regime.end)
    window = equity[(equity.index >= start) & (equity.index <= end)]

    if window.empty or len(window) < 2:
        return _empty_regime_row(regime)

    first = float(window.iloc[0])
    last = float(window.iloc[-1])
    ret_pct = (last / first - 1.0) * 100.0 if first > 0 else float("nan")

    peak = window.cummax()
    dd = (window - peak) / peak
    max_dd_pct = float(dd.min()) * 100.0

    return {
        "regime_key": regime.key,
        "regime_name": regime.name,
        "kind": regime.kind,
        "start_window": regime.start.date().isoformat(),
        "end_window": regime.end.date().isoformat(),
        "start_observed": window.index[0].date().isoformat(),
        "end_observed": window.index[-1].date().isoformat(),
        "bars": int(len(window)),
        "return_pct": ret_pct,
        "max_drawdown_pct": max_dd_pct,
    }


def _empty_regime_row(regime: Regime) -> dict:
    return {
        "regime_key": regime.key,
        "regime_name": regime.name,
        "kind": regime.kind,
        "start_window": regime.start.date().isoformat(),
        "end_window": regime.end.date().isoformat(),
        "start_observed": None,
        "end_observed": None,
        "bars": 0,
        "return_pct": float("nan"),
        "max_drawdown_pct": float("nan"),
    }


def all_regime_stats(equity: pd.Series, regimes: list[Regime] | None = None) -> pd.DataFrame:
    """One row per regime with return / max-DD over the observed overlap.

    Regimes the equity curve doesn't cover at all are still returned (with
    bars=0 and NaN metrics) so downstream consumers can show "no data" in
    a stable column layout.
    """
    rows = [regime_stats(equity, r) for r in (regimes or REGIMES)]
    df = pd.DataFrame(rows)
    return df
