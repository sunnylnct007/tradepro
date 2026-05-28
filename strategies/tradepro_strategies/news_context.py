"""News context block — SIGNAL_CARD_SPEC_v1.md §2.3.

Reshapes the data TradePro already collects (Yahoo news + LLM
sentiment scoring + Finnhub earnings calendar) into the
`news_context` block on the signal card:

  news_context: {
    sentiment_score: 0..1,
    sentiment_trend: IMPROVING / STABLE / DETERIORATING,
    article_count_30d_avg: int,
    article_count_today: int,
    key_headlines: [str, ...],
    earnings_proximity_days: int | None,
    suppress_signal: bool,
    suppress_reason: str | None,
  }

This is a pure transformation — no new network calls. The actual
GDELT integration listed in the spec stays a separate follow-on
that swaps the underlying source without changing call sites or
the row schema. compute_news_context() works against whatever
sentiment + news pipeline is already producing data.

sentiment_trend is conservatively STABLE today: computing
IMPROVING / DETERIORATING needs a 7d-vs-30d historic comparison
that we don't yet store per ticker. Once that lands, only this
helper changes — callers keep their shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Literal


# Threshold for "suppress this signal because of imminent news event".
# Mirrors apply_earnings_suppressor's default — keep in sync.
EARNINGS_SUPPRESS_DAYS = 7

# Number of recent headlines to include on the card. Spec example
# shows 2 — keeping it lean so the card fits on screen.
MAX_KEY_HEADLINES = 3


@dataclass
class NewsContext:
    """Output of compute_news_context(). Maps 1:1 to the news_context
    block in SIGNAL_CARD_SPEC §3."""
    sentiment_score: float | None
    sentiment_trend: Literal["IMPROVING", "STABLE", "DETERIORATING"]
    article_count_30d_avg: float | None
    article_count_today: int
    key_headlines: list[str]
    earnings_proximity_days: int | None
    suppress_signal: bool
    suppress_reason: str | None

    def to_dict(self) -> dict:
        return {
            "sentiment_score": (
                round(self.sentiment_score, 3)
                if self.sentiment_score is not None else None
            ),
            "sentiment_trend": self.sentiment_trend,
            "article_count_30d_avg": (
                round(self.article_count_30d_avg, 2)
                if self.article_count_30d_avg is not None else None
            ),
            "article_count_today": self.article_count_today,
            "key_headlines": list(self.key_headlines),
            "earnings_proximity_days": self.earnings_proximity_days,
            "suppress_signal": self.suppress_signal,
            "suppress_reason": self.suppress_reason,
        }


def _normalise_score(mean_sentiment: float | None) -> float | None:
    """LLM sentiment scoring outputs -1.0 (very negative) .. +1.0 (very
    positive). The signal-card schema wants 0..1 where higher = more
    positive. Linear remap with clamping for sanity."""
    if mean_sentiment is None:
        return None
    try:
        m = float(mean_sentiment)
    except (TypeError, ValueError):
        return None
    out = (m + 1.0) / 2.0
    if out < 0.0:
        return 0.0
    if out > 1.0:
        return 1.0
    return out


def _today_count(news_items: Iterable[dict]) -> int:
    """Count headlines published in the last 24h. Tolerant of multiple
    timestamp shapes (epoch int, ISO string, missing field)."""
    today = date.today()
    count = 0
    for item in news_items:
        if not isinstance(item, dict):
            continue
        ts = item.get("published_at") or item.get("publishedAt")
        if ts is None:
            continue
        try:
            if isinstance(ts, (int, float)):
                pub_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            else:
                pub_date = datetime.fromisoformat(
                    str(ts).replace("Z", "+00:00")
                ).date()
        except (ValueError, OSError):
            continue
        if pub_date == today:
            count += 1
    return count


def compute_news_context(
    *,
    sentiment_summary: dict | None,
    news_items: list[dict] | None,
    earnings_proximity_days: int | None,
    earnings_suppress_threshold: int = EARNINGS_SUPPRESS_DAYS,
) -> NewsContext:
    """Build the news_context block from existing TradePro row fields.

    sentiment_summary: shape from `news_sentiment.SentimentSummary` —
        the dict with ``mean_sentiment`` etc. that compare.py already
        attaches to every row.
    news_items: list of enriched NewsItem dicts (already on the row
        as ``news``). Used for article_count_today + key_headlines.
    earnings_proximity_days: days until next earnings, from the
        existing earnings_signal.upcoming.days_until pipeline.

    Returns a NewsContext with suppress_signal=True when earnings
    land inside the threshold (mirrors apply_earnings_suppressor's
    contract so UI and engine agree).
    """
    ss = sentiment_summary or {}
    items = list(news_items or [])
    mean_sent = ss.get("mean_sentiment")
    sentiment_score = _normalise_score(mean_sent)

    headlines: list[str] = []
    for it in items[:MAX_KEY_HEADLINES]:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or it.get("headline")
        if isinstance(title, str) and title.strip():
            headlines.append(title.strip())

    # article_count_30d_avg: we don't have a 30d window today, so
    # approximate as len(news_items) / 30 — gives a rough articles-
    # per-day rate that the UI can compare against today's count. None
    # when there's no news at all.
    avg_count: float | None = None
    if items:
        avg_count = len(items) / 30.0

    today_count = _today_count(items)

    suppress = False
    suppress_reason: str | None = None
    if (earnings_proximity_days is not None
            and 0 <= earnings_proximity_days <= earnings_suppress_threshold):
        suppress = True
        suppress_reason = (
            f"earnings in {earnings_proximity_days}d "
            f"(threshold {earnings_suppress_threshold}d)"
        )

    return NewsContext(
        sentiment_score=sentiment_score,
        sentiment_trend="STABLE",
        article_count_30d_avg=avg_count,
        article_count_today=today_count,
        key_headlines=headlines,
        earnings_proximity_days=earnings_proximity_days,
        suppress_signal=suppress,
        suppress_reason=suppress_reason,
    )
