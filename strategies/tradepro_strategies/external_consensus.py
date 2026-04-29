"""Cross-check our verdict against Wall Street's published consensus.

We compute BUY/HOLD/WAIT/AVOID from price action + strategy votes; analysts
write to a different brief (12-month price target, fundamentals, sector
view). Surfacing both side-by-side lets a user see whether our system
agrees with the consensus or argues against it — a fast trust check.

Source: Yahoo Finance via yfinance.Ticker.info. Free, no key, but slow
(~1-2s per ticker) and rate-limited; calls are best-effort and any
failure leaves the row's consensus as None rather than blowing up.

ETFs typically have no analyst rating (Yahoo returns null) — that's
expected. We pass it through as 'not rated' rather than inventing one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


# Yahoo's `recommendationKey` taxonomy. We normalise to 5 buckets so the
# frontend can colour-code consistently.
KEY_TO_LABEL = {
    "strong_buy": "STRONG BUY",
    "buy": "BUY",
    "hold": "HOLD",
    "underperform": "UNDERPERFORM",
    "sell": "SELL",
    "strong_sell": "STRONG SELL",
    "none": None,
}


@dataclass
class ExternalConsensus:
    symbol: str
    fetched_at: str
    rating_key: str | None              # raw recommendationKey from Yahoo
    rating_label: str | None            # normalised display label
    rating_mean: float | None           # 1.0 (strong buy) to 5.0 (strong sell)
    n_analysts: int | None
    target_mean: float | None
    target_median: float | None
    target_high: float | None
    target_low: float | None
    current_price: float | None
    target_vs_current_pct: float | None
    source: str = "yahoo"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "fetched_at": self.fetched_at,
            "rating_key": self.rating_key,
            "rating_label": self.rating_label,
            "rating_mean": self.rating_mean,
            "n_analysts": self.n_analysts,
            "target_mean": self.target_mean,
            "target_median": self.target_median,
            "target_high": self.target_high,
            "target_low": self.target_low,
            "current_price": self.current_price,
            "target_vs_current_pct": self.target_vs_current_pct,
            "source": self.source,
        }


def _empty(symbol: str) -> ExternalConsensus:
    return ExternalConsensus(
        symbol=symbol,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        rating_key=None, rating_label=None, rating_mean=None,
        n_analysts=None,
        target_mean=None, target_median=None, target_high=None, target_low=None,
        current_price=None, target_vs_current_pct=None,
    )


def _safe_float(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # filter NaN


def fetch_consensus(symbol: str) -> ExternalConsensus:
    """Best-effort fetch. Returns an empty record on any failure so the
    caller can serialise it without special-casing."""
    try:
        import yfinance as yf
    except ImportError:
        return _empty(symbol)

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:  # noqa: BLE001
        return _empty(symbol)

    rating_key = info.get("recommendationKey")
    # Yahoo returns 'none' (string) for ETFs / unrated tickers.
    if rating_key in (None, "", "none"):
        rating_key = None
    rating_label = KEY_TO_LABEL.get(rating_key) if rating_key else None
    rating_mean = _safe_float(info.get("recommendationMean"))
    n_analysts = info.get("numberOfAnalystOpinions")
    if n_analysts is not None:
        try:
            n_analysts = int(n_analysts)
        except (TypeError, ValueError):
            n_analysts = None

    target_mean = _safe_float(info.get("targetMeanPrice"))
    target_median = _safe_float(info.get("targetMedianPrice"))
    target_high = _safe_float(info.get("targetHighPrice"))
    target_low = _safe_float(info.get("targetLowPrice"))
    current = _safe_float(info.get("regularMarketPrice")) or _safe_float(info.get("currentPrice"))

    vs_current = None
    if target_mean is not None and current is not None and current > 0:
        vs_current = (target_mean / current - 1.0) * 100.0

    return ExternalConsensus(
        symbol=symbol,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        rating_key=rating_key,
        rating_label=rating_label,
        rating_mean=rating_mean,
        n_analysts=n_analysts,
        target_mean=target_mean,
        target_median=target_median,
        target_high=target_high,
        target_low=target_low,
        current_price=current,
        target_vs_current_pct=vs_current,
    )
