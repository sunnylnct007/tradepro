"""Phase-X composite swing-trade scorer.

Combines the four signal families we now compute on every Compare
row into a single 0-8 score, plus a verdict label so the user has
one number to read instead of four:

    Layer       Source                                  Max points
    Quality     5y Sharpe + max-DD recovery time        2
    Valuation   Family-2 cheap/fair/expensive flag      2
    Event       Family-4 beat-and-retreat verdict       2
    Price       Family-1 strategy consensus + RSI / SMA 2

Verdict mapping:
    >= 6        STRONG_BUY  — three+ families align positively
    4–5         BUY          — most layers favourable
    2–3         HOLD         — mixed signal
    0–1         AVOID        — every family says no

The scorer is intentionally simple — explicit thresholds, no ML, no
hidden weights — so the output is debuggable. Each layer also
returns its score breakdown so the rationale can quote it
("scored 6/8 — quality 2, valuation 1, event 2, price 1").

Pair with Phase-2 portfolio-aware engine: when a holding (qty +
cost basis) is supplied, the scorer's recommendation becomes
'buy_more / hold / trim' instead of generic STRONG_BUY/etc,
weighted by current P&L.
"""
from __future__ import annotations

from dataclasses import dataclass


# Quality-layer thresholds — calibrated against the existing
# etf_uk_core / etf_us_core stats. A 5y Sharpe ≥ 0.7 is solid for an
# unleveraged equity ETF; ≥ 0.95 is exceptional. Recovery ≤ 12
# months is fast; ≤ 24 is acceptable; longer means investors who
# bought the prior peak waited > 2 years to break even.
QUALITY_GREAT_SHARPE = 0.7
QUALITY_FAST_RECOVERY_DAYS = 365
QUALITY_OK_RECOVERY_DAYS = 730

# Price-layer thresholds.
PRICE_HEALTHY_RSI_LO = 35.0
PRICE_HEALTHY_RSI_HI = 55.0
PRICE_STRONG_CONSENSUS = 4   # ≥ N of 5 strategies long → strong


@dataclass
class SwingScore:
    total: int                 # 0–8
    verdict: str               # STRONG_BUY / BUY / HOLD / AVOID
    layers: dict[str, int]     # {quality, valuation, event, price}
    reasons: dict[str, str]    # one-liner per layer explaining the score

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "verdict": self.verdict,
            "layers": dict(self.layers),
            "reasons": dict(self.reasons),
        }


def evaluate_swing(row: dict) -> SwingScore:
    """Score a Compare row across the four families. The row shape
    matches what compare.py emits — stats, market_state,
    cross_sectional_momentum, valuation_flag, earnings_signal,
    long_count, total_strategies.

    Each layer is independent and contributes 0-2; missing data on a
    layer scores 0 (silent — the reason explains why)."""
    layers: dict[str, int] = {}
    reasons: dict[str, str] = {}

    layers["quality"], reasons["quality"] = _score_quality(row)
    layers["valuation"], reasons["valuation"] = _score_valuation(row)
    layers["event"], reasons["event"] = _score_event(row)
    layers["price"], reasons["price"] = _score_price(row)

    total = sum(layers.values())
    verdict = (
        "STRONG_BUY" if total >= 6
        else "BUY" if total >= 4
        else "HOLD" if total >= 2
        else "AVOID"
    )
    return SwingScore(total=total, verdict=verdict, layers=layers, reasons=reasons)


def _score_quality(row: dict) -> tuple[int, str]:
    stats = row.get("stats") or {}
    sharpe = stats.get("sharpe")
    recovery_days = stats.get("max_drawdown_recovery_days")
    still_recovering = stats.get("max_drawdown_still_recovering")
    if sharpe is None:
        return 0, "no Sharpe data"
    pts = 0
    bits: list[str] = []
    if sharpe >= QUALITY_GREAT_SHARPE:
        pts += 1
        bits.append(f"Sharpe {sharpe:.2f} ≥ {QUALITY_GREAT_SHARPE}")
    else:
        bits.append(f"Sharpe {sharpe:.2f} below {QUALITY_GREAT_SHARPE}")
    if still_recovering:
        bits.append("max-DD still recovering")
    elif recovery_days is None:
        bits.append("no recovery-time data")
    elif recovery_days <= QUALITY_FAST_RECOVERY_DAYS:
        pts += 1
        bits.append(f"recovered in {recovery_days}d (fast)")
    elif recovery_days <= QUALITY_OK_RECOVERY_DAYS:
        # Slow but acceptable — half a point would be ideal but we
        # round down to keep the integer math honest.
        bits.append(f"recovered in {recovery_days}d (ok)")
    else:
        bits.append(f"recovered in {recovery_days}d (slow)")
    return pts, "; ".join(bits)


def _score_valuation(row: dict) -> tuple[int, str]:
    """Score the valuation layer — metric-aware so the reason string
    quotes the lens that was actually used (P/E vs basket for stocks,
    yield vs basket for ETFs). Avoids the old NVDA-style false
    'expensive' that flowed from yield-only ranking on growth names."""
    flag_obj = row.get("valuation_flag") or {}
    flag = flag_obj.get("flag")
    lens = flag_obj.get("lens_used") or (
        "yield" if flag_obj.get("metric") == "dividend_yield_pct" else
        "pe" if flag_obj.get("metric") == "forward_pe" else None
    )
    if not flag or flag == "n/a":
        return 0, "no valuation data"
    metric_label = "P/E" if lens == "pe" else "yield"
    if flag == "cheap":
        return 2, f"top-quartile {metric_label} (cheap vs basket)"
    if flag == "fair":
        return 1, f"mid-basket {metric_label}"
    if flag == "expensive":
        return 0, f"bottom-quartile {metric_label} (expensive vs basket)"
    return 0, f"unknown flag {flag!r}"


def _score_event(row: dict) -> tuple[int, str]:
    ev = row.get("earnings_signal") or {}
    verdict = ev.get("verdict")
    if not verdict or verdict in ("NO_RECENT", "NOT_APPLICABLE"):
        # ETFs and stocks-with-no-recent-earnings get 0 — the layer
        # silently doesn't fire. The reason explains why so the
        # rationale layer doesn't quote a fabricated number.
        return 0, "no recent earnings event"
    if verdict == "STRONG":
        retreat = ev.get("retreat_from_post_earnings_peak_pct")
        if retreat is not None:
            return 2, f"BEAT_AND_RETREAT — {retreat:+.1f}% off post-earnings peak"
        return 2, "BEAT_AND_RETREAT — fired"
    if verdict == "MODERATE":
        return 1, "beat but retreat outside the sweet spot"
    if verdict in ("EXPIRED", "NO_BEAT", "NO_PRICES"):
        return 0, verdict.lower().replace("_", " ")
    return 0, f"unknown earnings verdict {verdict!r}"


def _score_price(row: dict) -> tuple[int, str]:
    long_count = row.get("long_count")
    total = row.get("total_strategies") or 0
    ms = row.get("market_state") or {}
    rsi = ms.get("rsi_14")
    above_sma = ms.get("above_sma_200")
    if long_count is None or not total:
        return 0, "no consensus data"
    pts = 0
    bits: list[str] = []
    if long_count >= PRICE_STRONG_CONSENSUS:
        pts += 1
        bits.append(f"{long_count}/{total} strategies long")
    else:
        bits.append(f"only {long_count}/{total} strategies long")
    healthy_zone = (
        rsi is not None
        and PRICE_HEALTHY_RSI_LO <= rsi <= PRICE_HEALTHY_RSI_HI
    )
    if healthy_zone and above_sma is True:
        pts += 1
        bits.append(f"RSI {rsi:.0f} healthy + above 200d SMA")
    elif rsi is None:
        bits.append("no RSI")
    elif above_sma is None:
        bits.append("no SMA200 reference")
    elif above_sma is False:
        bits.append("below 200d SMA")
    elif rsi > PRICE_HEALTHY_RSI_HI:
        bits.append(f"RSI {rsi:.0f} overbought")
    elif rsi < PRICE_HEALTHY_RSI_LO:
        bits.append(f"RSI {rsi:.0f} oversold")
    return pts, "; ".join(bits)
