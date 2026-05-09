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
    """Cheap-vs-basket valuation flag using dividend yield as a proxy.
    Higher yield → more likely cheap; lower yield → more likely
    expensive. Best for ETF / dividend-paying-stock baskets where P/E
    isn't reported (most ETFs) or means little.

    Caveat: yield can be elevated for a structurally distressed asset
    (the dividend hasn't been cut yet but the price already fell).
    The flag is descriptive, not prescriptive — pair with the
    technical bucket vote.

    For mixed / growth-stock baskets (NVDA, AMZN don't pay much),
    prefer `bucket_by_valuation` which falls back to P/E.

    Quartile rules (per basket):
      Q1 (top 25% by yield)    → "cheap"
      Q2-Q3 (middle 50%)        → "fair"
      Q4 (bottom 25%)           → "expensive"
      missing yield             → "n/a"
    """
    return _quartile_bucket(
        symbol_yields,
        cheaper_when="higher",
        metric_name="dividend_yield_pct",
        units="%",
        value_label="yield",
    )


def bucket_by_pe_ratio(
    symbol_pes: dict[str, float | None],
) -> dict[str, dict]:
    """Cheap-vs-basket valuation flag using forward (or trailing) P/E.
    Lower P/E → cheaper; higher P/E → more expensive.

    Best for growth / non-dividend-paying stocks where dividend yield
    is uninformative (NVDA dividend ≈ 0.02% — yield-quartile gives
    "expensive" purely because it doesn't pay, which is a wrong signal).

    Caveat: a low P/E can also mean broken thesis (value trap). The
    flag is cross-sectional vs basket peers, not absolute. Truly
    historical P/E vs the symbol's own 10y median requires a
    fundamentals snapshot store — parked.

    Quartile rules (per basket, ascending by P/E):
      Q1 (lowest P/E quartile)  → "cheap"
      Q2-Q3 (middle 50%)         → "fair"
      Q4 (highest P/E quartile)  → "expensive"
      missing P/E                → "n/a"
    """
    # Negative or zero P/E (loss-making companies) is meaningless for
    # ranking — drop those from the comparison rather than rank them
    # as "cheapest". They get "n/a" with an explanatory basis.
    cleaned: dict[str, float | None] = {}
    for s, v in symbol_pes.items():
        if v is None or not isinstance(v, (int, float)) or v <= 0:
            cleaned[s] = None
        else:
            cleaned[s] = v
    return _quartile_bucket(
        cleaned,
        cheaper_when="lower",
        metric_name="forward_pe",
        units="×",
        value_label="P/E",
    )


def bucket_by_valuation(
    symbol_pes: dict[str, float | None],
    symbol_yields: dict[str, float | None],
    *,
    pe_density_threshold: float = 0.5,
) -> dict[str, dict]:
    """Pick the right valuation lens based on basket composition.

    If at least `pe_density_threshold` (default 50%) of the basket
    has a positive P/E, use P/E-quartile (better for stocks). Else
    fall back to dividend-yield-quartile (better for ETF baskets
    where P/E is rarely reported).

    Why a hybrid: a pure stocks basket like (NVDA, MSFT, AMZN, META)
    has good P/E coverage and bad dividend coverage — yield ranks
    NVDA expensive only because it doesn't pay. A pure ETF basket
    (VUSA.L, VUKE.L, INRG.L) has the opposite: yield is the only
    valuation-flavoured field Yahoo returns. The orchestrator picks
    automatically so callers don't have to.

    Output shape matches both primitives — adds a `lens_used` field
    ('pe' or 'yield') so the rationale layer can render the
    metric-aware reason string.
    """
    pe_valid = sum(
        1 for v in symbol_pes.values()
        if v is not None and isinstance(v, (int, float)) and v > 0
    )
    n = len(symbol_pes) or 1
    use_pe = (pe_valid / n) >= pe_density_threshold

    if use_pe:
        out = bucket_by_pe_ratio(symbol_pes)
        lens = "pe"
    else:
        out = bucket_by_yield_quartile(symbol_yields)
        lens = "yield"
    for v in out.values():
        v["lens_used"] = lens
    return out


def _quartile_bucket(
    symbol_values: dict[str, float | None],
    *,
    cheaper_when: str,         # "higher" or "lower"
    metric_name: str,
    units: str,                # "%" or "×"
    value_label: str,          # "yield" / "P/E"
) -> dict[str, dict]:
    """Generic quartile bucketing, parameterised by which direction is
    cheap. Used by both yield-quartile (cheaper when higher) and
    P/E-quartile (cheaper when lower).

    Output preserves legacy `yield_pct` / `basket_median_yield_pct`
    field names when the lens is yield (downstream consumers read
    them). Adds metric-aware `value` / `basket_median` fields the
    new P/E lens uses, plus a `pe_ratio` mirror when the lens is P/E.
    """
    is_yield = metric_name == "dividend_yield_pct"
    is_pe = metric_name == "forward_pe"

    def _shape(flag: str, val: float | None, median: float | None, basis: str) -> dict:
        out = {
            "flag": flag,
            "value": val,
            "basket_median": median,
            "basis": basis,
            "metric": metric_name,
        }
        if is_yield:
            # Legacy aliases — rationale.py / frontend / step files all
            # read these and rely on them. Keep populated even after
            # we add the metric-aware names above.
            out["yield_pct"] = val
            out["basket_median_yield_pct"] = median
        if is_pe:
            out["pe_ratio"] = val
            out["basket_median_pe"] = median
        return out

    valid = [(s, v) for s, v in symbol_values.items()
             if v is not None and isinstance(v, (int, float))]
    if not valid:
        return {
            s: _shape("n/a", None, None, f"no {value_label} data in basket")
            for s in symbol_values
        }

    reverse = (cheaper_when == "higher")  # higher = cheaper → desc; lower = cheaper → asc
    sorted_for_rank = sorted(valid, key=lambda kv: kv[1], reverse=reverse)
    n = len(sorted_for_rank)
    rank: dict[str, int] = {s: i + 1 for i, (s, _) in enumerate(sorted_for_rank)}
    median = _median([v for _, v in valid])
    q1_cutoff = max(1, n // 4)            # rank ≤ this → cheap (rank=1 means "most cheap")
    q4_cutoff = n - max(1, n // 4) + 1    # rank ≥ this → expensive

    out: dict[str, dict] = {}
    for sym, val in symbol_values.items():
        if sym not in rank:
            out[sym] = _shape(
                "n/a", None, median,
                f"no {value_label} data for this symbol",
            )
            continue
        r = rank[sym]
        if r <= q1_cutoff:
            flag = "cheap"
        elif r >= q4_cutoff:
            flag = "expensive"
        else:
            flag = "fair"
        basis = (
            f"{value_label} {float(val):.2f}{units} vs basket median "
            f"{median:.2f}{units} (rank {r} of {n})"
        )
        out[sym] = _shape(flag, float(val), median, basis)
    return out
