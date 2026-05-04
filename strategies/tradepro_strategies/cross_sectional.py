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


def cross_basket_trace_rows(
    cs_momentum: dict | None,
    valuation: dict | None,
) -> list[dict]:
    """Build decision-trace rows for the cross-basket signals so they
    show up in the Compare expand panel's "Why the verdict" ladder
    next to RSI / SMA / etc. Each row matches the existing trace
    shape: {"name", "status" ∈ pass|warn|fail, "detail"}.

    Status semantics for momentum:
      pass = top quartile in basket
      warn = above-median but not top quartile
      fail = bottom half (below basket median)

    Status semantics for valuation:
      pass = cheap (yield in top quartile)
      warn = fair (middle 50%)
      fail = expensive (bottom quartile)
      (omitted entirely when n/a — no synthetic warn for missing data)
    """
    rows: list[dict] = []

    if cs_momentum and cs_momentum.get("rank") is not None:
        rank = cs_momentum["rank"]
        peer_count = cs_momentum.get("peer_count")
        total = peer_count + 1 if isinstance(peer_count, int) else None
        z = cs_momentum.get("zscore")
        is_top = bool(cs_momentum.get("is_top_quartile"))
        below_median = isinstance(z, (int, float)) and z < 0
        status = "pass" if is_top else ("fail" if below_median else "warn")
        of_str = f" of {total}" if total is not None else ""
        z_str = f", z={z:+.2f}" if isinstance(z, (int, float)) else ""
        rows.append({
            "name": "Cross-basket momentum",
            "status": status,
            "detail": f"rank {rank}{of_str}{z_str}",
        })

    if valuation and valuation.get("flag") in ("cheap", "fair", "expensive"):
        flag = valuation["flag"]
        status = {"cheap": "pass", "fair": "warn", "expensive": "fail"}[flag]
        basis = valuation.get("basis") or ""
        rows.append({
            "name": "Cross-basket valuation",
            "status": status,
            "detail": f"{flag} — {basis}" if basis else flag,
        })

    return rows


def bucket_by_yield_quartile(
    symbol_yields: dict[str, float | None],
) -> dict[str, dict]:
    """Cheap-vs-basket valuation flag (Family 2 starter) using
    dividend yield as a proxy. Higher yield within the basket → more
    likely to be cheap; lower yield → more likely expensive.

    Why dividend yield as a proxy: it's the only valuation-flavoured
    field we currently store in fundamentals. True historical P/E
    vs 10-year median needs a fundamentals snapshot store we haven't
    built. This is the 80%-of-the-value Family-2 starter; replace
    with real P/E-vs-history once that store exists.

    Caveat: yield can be elevated for a structurally distressed asset
    (the dividend hasn't been cut yet but the price already fell).
    The flag is descriptive, not prescriptive — pair with the
    technical bucket vote.

    Quartile rules (per basket):
      Q1 (top 25% by yield)    → "cheap"
      Q2-Q3 (middle 50%)        → "fair"
      Q4 (bottom 25%)           → "expensive"
      missing yield             → "n/a"
    """
    valid = [(s, v) for s, v in symbol_yields.items()
             if v is not None and isinstance(v, (int, float))]
    if not valid:
        return {s: {
            "flag": "n/a",
            "yield_pct": None,
            "basket_median_yield_pct": None,
            "basis": "no dividend yield data in basket",
            "metric": "dividend_yield_pct",
        } for s in symbol_yields}

    sorted_desc = sorted(valid, key=lambda kv: kv[1], reverse=True)
    n = len(sorted_desc)
    rank: dict[str, int] = {s: i + 1 for i, (s, _) in enumerate(sorted_desc)}
    median = _median([v for _, v in valid])
    q1_cutoff = max(1, n // 4)
    q4_cutoff = n - max(1, n // 4) + 1

    out: dict[str, dict] = {}
    for sym, val in symbol_yields.items():
        if sym not in rank:
            out[sym] = {
                "flag": "n/a",
                "yield_pct": None,
                "basket_median_yield_pct": median,
                "basis": "no dividend yield data for this symbol",
                "metric": "dividend_yield_pct",
            }
            continue
        r = rank[sym]
        if r <= q1_cutoff:
            flag = "cheap"
        elif r >= q4_cutoff:
            flag = "expensive"
        else:
            flag = "fair"
        out[sym] = {
            "flag": flag,
            "yield_pct": float(val),
            "basket_median_yield_pct": median,
            "basis": (
                f"yield {float(val):.2f}% vs basket median "
                f"{median:.2f}% (rank {r} of {n})"
            ),
            "metric": "dividend_yield_pct",
        }
    return out
