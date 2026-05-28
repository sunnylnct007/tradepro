"""News items + sentiment summaries."""
from __future__ import annotations

from ._base import TPModel


class NewsItem(TPModel):
    """Yahoo news item, optionally annotated with LLM-scored sentiment."""
    title: str
    publisher: str | None = None
    link: str | None = None
    published_at: str | None = None
    thumbnail: str | None = None
    # Sentiment fields (optional; None when scoring failed for this item)
    sentiment: float | None = None
    sentiment_themes: list[str] = []
    sentiment_material: bool = False
    sentiment_model: str | None = None
    sentiment_error: str | None = None


class SentimentSummary(TPModel):
    """7-day rolling aggregate per symbol."""
    items_considered: int = 0
    material_items_considered: int = 0
    mean_sentiment: float | None = None
    very_negative_count: int = 0
    material_negative_count: int = 0
    most_negative: str | None = None
