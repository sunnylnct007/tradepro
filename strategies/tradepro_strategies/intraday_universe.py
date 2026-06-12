"""Intraday universe selection — rank candidates by LIQUIDITY × VOLATILITY.

A swing desk ranks names by trend score; an INTRADAY desk needs different inputs:
the day's tradeable, in-play names. A good intraday name is

  (a) LIQUID — high $-volume → tight spreads + reliable fills, and
  (b) VOLATILE — high intraday range (ATR%) → enough movement that the move
      clears the round-trip cost (the -£2.87/trade churn was the opposite).

This ranks a candidate pool on those two, NOT the daily Ichimoku trend score
intraday_flat mis-uses. RVOL (relative volume — "is it in play TODAY") is an
in-SESSION filter applied separately, since pre-market it isn't known yet.

Reads the daily cache (close/high/low/volume) — no new data dependency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

_CACHE = Path.home() / ".tradepro" / "cache" / "yahoo" / "1d"


@dataclass
class IntradayRank:
    symbol: str
    dollar_vol: float   # median daily $-volume over the lookback (liquidity)
    atr_pct: float      # ATR(14) / price (intraday-range proxy, as a fraction)
    score: float        # composite rank key (higher = better intraday name)


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """ATR(period) as a fraction of the last close — the volatility proxy."""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    px = float(c.iloc[-1])
    if px <= 0 or pd.isna(atr):
        return 0.0
    return float(atr) / px


def rank_intraday_universe(
    symbols: Iterable[str],
    *,
    lookback: int = 20,
    min_dollar_vol: float = 5e7,   # $50M/day floor — below this spreads bite
    min_atr_pct: float = 0.012,    # 1.2% daily range floor — below this is churn
    cache_dir: Path = _CACHE,
) -> list[IntradayRank]:
    """Rank a candidate pool for intraday trading. Filters out the illiquid and
    the too-quiet, then ranks the survivors by volatility weighted by (log)
    liquidity so a liquid mover beats an illiquid one. Returns best-first."""
    rows: list[tuple[str, float, float]] = []
    for s in symbols:
        p = Path(cache_dir) / f"{s}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
            if len(df) < max(30, lookback):
                continue
            tail = df.tail(lookback)
            dvol = float((tail["close"] * tail["volume"]).median())
            atrp = _atr_pct(df)
            rows.append((s, dvol, atrp))
        except Exception:  # noqa: BLE001 — skip unreadable/short symbols
            continue

    out: list[IntradayRank] = []
    for s, dvol, atrp in rows:
        if dvol < min_dollar_vol or atrp < min_atr_pct:
            continue
        # ATR% is the edge driver; log10($-vol) keeps liquidity a tie-breaker
        # rather than letting mega-caps dominate purely on size.
        score = atrp * math.log10(max(dvol, 1.0))
        out.append(IntradayRank(symbol=s, dollar_vol=dvol, atr_pct=atrp, score=score))
    out.sort(key=lambda r: -r.score)
    return out
