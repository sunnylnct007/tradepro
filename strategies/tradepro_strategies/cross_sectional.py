"""Cross-sectional momentum ranking — Family 3 in the multi-family
signal stack we're building toward (see Phase 3 memory).

Existing signals are all Family 1 (price vs its own moving average).
This module looks ACROSS the basket and answers: 'compared to its
peers in this watchlist, where does this symbol sit on momentum?'
A symbol that's 12-month momentum +20% in a basket where the median
is +18% is NOT a momentum standout — but in a basket where the
median is +5%, it is. The bucket vote currently can't tell those
apart; this module exposes the data so future scorers can.

Output is annotation only: each symbol gets a rank (1 = highest)
and a z-score. We don't (yet) feed this into compute_bucket — that
needs a coordinated redesign of the bucket signature when we add
multi-family scoring in Phase 3.
"""
from __future__ import annotations

from statistics import mean, pstdev


def rank_by_momentum(
    symbol_returns: dict[str, float | None],
    *,
    metric_name: str = "momentum_12m_pct",
) -> dict[str, dict]:
    """Rank a basket by a single momentum metric.

    Input: { "VUKE.L": 8.4, "VUSA.L": 15.7, "INRG.L": 0.6, "MISSING": None, ... }

    Output: per symbol → {
        rank: 1-based, 1 = highest momentum among rows that have data
        rank_pct: percentile (1.0 = top, 0.0 = bottom)
        zscore: (value - basket_mean) / basket_stdev
        peer_count: number of peers with data (excluding self)
        basket_mean: mean of the basket
        basket_median: median of the basket
        is_top_quartile: bool
        metric_name: what was ranked
    }

    Symbols with None value get rank=None / zscore=None so callers
    can render them as '—' rather than guess.
    """
    valid = [(s, v) for s, v in symbol_returns.items()
             if v is not None and isinstance(v, (int, float))]
    n = len(valid)
    if n == 0:
        return {s: _empty(metric_name) for s in symbol_returns}

    sorted_desc = sorted(valid, key=lambda kv: kv[1], reverse=True)
    rank_by_symbol: dict[str, int] = {s: i + 1 for i, (s, _) in enumerate(sorted_desc)}

    values = [v for _, v in valid]
    basket_mean = float(mean(values))
    basket_median = _median(values)
    basket_stdev = float(pstdev(values)) if n > 1 else 0.0
    top_quartile_threshold = max(1, n // 4)

    out: dict[str, dict] = {}
    for sym, val in symbol_returns.items():
        if sym not in rank_by_symbol:
            out[sym] = _empty(metric_name, basket_mean=basket_mean,
                              basket_median=basket_median, peer_count=n)
            continue
        rank = rank_by_symbol[sym]
        zscore = ((val - basket_mean) / basket_stdev) if basket_stdev > 0 else 0.0
        out[sym] = {
            "metric_name": metric_name,
            "value": float(val),
            "rank": rank,
            "rank_pct": (n - rank + 1) / n,  # 1.0 for #1, 1/n for last
            "zscore": float(zscore),
            "peer_count": n - 1,  # peers excluding self
            "basket_mean": basket_mean,
            "basket_median": basket_median,
            "is_top_quartile": rank <= top_quartile_threshold,
        }
    return out


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 0:
        return float((s[mid - 1] + s[mid]) / 2)
    return float(s[mid])


def _empty(metric_name: str, **base: object) -> dict:
    out: dict = {
        "metric_name": metric_name,
        "value": None,
        "rank": None,
        "rank_pct": None,
        "zscore": None,
        "peer_count": None,
        "basket_mean": None,
        "basket_median": None,
        "is_top_quartile": False,
    }
    out.update(base)
    return out
