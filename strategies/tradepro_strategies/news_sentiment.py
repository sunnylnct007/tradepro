"""Per-headline sentiment scoring + 7-day rolling aggregate.

Each news item gets a small annotated structure:
  - sentiment: -1.0 (very negative) to 1.0 (very positive)
  - themes:    short tag list (e.g. ['guidance', 'china', 'regulation'])
  - material:  bool — does this matter for the price, or is it filler?

Scored items are cached on disk by hash(headline + model). Yahoo's
news feed repeats articles across runs, so the cache is cheap insurance:
each headline costs one LLM call total, not one per refresh.

Aggregation: per symbol, take all items in the last 7 days and
compute (mean_sentiment, material_count, very_negative_count). The
comparator turns this into a 'Sentiment trend (7d)' check on the
decision_trace.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .llm import LlmProvider, get_provider
from .news import NewsItem


CACHE_PATH = Path.home() / ".tradepro" / "cache" / "llm-sentiment.json"


@dataclass
class ScoredHeadline:
    title: str
    sentiment: float | None
    themes: list[str]
    material: bool
    model: str | None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "sentiment": self.sentiment,
            "themes": self.themes,
            "material": self.material,
            "model": self.model,
            "error": self.error,
        }


@dataclass
class SentimentSummary:
    """7-day rolling aggregate per symbol — what the decision_trace check
    actually consumes."""
    items_considered: int
    mean_sentiment: float | None
    very_negative_count: int      # items with sentiment <= -0.5
    material_negative_count: int  # those that are also material
    most_negative: str | None     # the single worst headline title

    def to_dict(self) -> dict:
        return {
            "items_considered": self.items_considered,
            "mean_sentiment": self.mean_sentiment,
            "very_negative_count": self.very_negative_count,
            "material_negative_count": self.material_negative_count,
            "most_negative": self.most_negative,
        }


# ---------------------------------------------------------------------------
# Disk cache — survives across runs so we never re-score the same headline
# ---------------------------------------------------------------------------

class _DiskCache:
    """Tiny JSON-on-disk cache keyed by hash(headline + model). Loaded on
    first use, written incrementally via atomic rename."""
    def __init__(self, path: Path = CACHE_PATH):
        self._path = path
        self._data: dict[str, dict] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self._data = json.loads(self._path.read_text())
            if not isinstance(self._data, dict):
                self._data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def get(self, key: str) -> dict | None:
        self._load()
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        self._load()
        self._data[key] = value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data))
            tmp.replace(self._path)
        except OSError:
            # Cache write failure is non-fatal — next run just rescores.
            pass


_cache = _DiskCache()


def _key(title: str, model: str) -> str:
    return hashlib.sha1(f"{model}::{title}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Scoring one headline
# ---------------------------------------------------------------------------

_PROMPT = """You are a financial-news sentiment analyst. Score one headline.

Headline: {headline}
Publisher: {publisher}

Output ONLY a JSON object with these exact fields:
  sentiment: float in [-1, 1] (-1 strongly negative, 0 neutral, 1 strongly positive)
  themes:    array of 1-4 short lowercase tags (e.g. ["guidance", "china"])
  material:  boolean — true only if this would plausibly move the price of
             the named instrument; false for promotional / generic / filler.

Be conservative. Filler ("5 stocks to watch", routine analyst rating, list
articles) is material=false. Guidance, earnings beats/misses, regulatory
events, M&A, leadership changes, large customer wins/losses, geopolitical
shocks affecting the underlying are material=true.
"""

_SCHEMA_HINT = {
    "sentiment": -0.4,
    "themes": ["guidance", "earnings"],
    "material": True,
}


def _score_one(item: NewsItem, provider: LlmProvider) -> ScoredHeadline:
    """LLM call for one headline. Cached aggressively — same headline
    won't get re-scored within OR across runs."""
    if not provider.healthy():
        return ScoredHeadline(
            title=item.title, sentiment=None, themes=[], material=False,
            model=None, error="provider unavailable",
        )

    cache_key = _key(item.title, provider.model)
    cached = _cache.get(cache_key)
    if cached is not None:
        return ScoredHeadline(
            title=item.title,
            sentiment=cached.get("sentiment"),
            themes=cached.get("themes") or [],
            material=bool(cached.get("material")),
            model=cached.get("model"),
            error=cached.get("error"),
        )

    prompt = _PROMPT.format(
        headline=item.title,
        publisher=item.publisher or "Unknown",
    )
    result = provider.complete_json(prompt, schema_hint=_SCHEMA_HINT, max_tokens=120)

    if not result.ok:
        scored = ScoredHeadline(
            title=item.title, sentiment=None, themes=[], material=False,
            model=provider.model, error=result.error,
        )
    else:
        d = result.data
        sentiment = _coerce_float(d.get("sentiment"))
        themes = d.get("themes") or []
        if not isinstance(themes, list):
            themes = []
        themes = [str(t)[:32] for t in themes][:6]
        material = bool(d.get("material"))
        scored = ScoredHeadline(
            title=item.title,
            sentiment=sentiment,
            themes=themes,
            material=material,
            model=provider.model,
        )

    _cache.put(cache_key, scored.to_dict())
    return scored


def _coerce_float(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return max(-1.0, min(1.0, f))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_news(
    items: Iterable[NewsItem],
    provider: LlmProvider | None = None,
) -> list[ScoredHeadline]:
    p = provider or get_provider()
    return [_score_one(item, p) for item in items]


def summarise_recent(
    scored: list[ScoredHeadline],
    raw_items: list[NewsItem],
    days: int = 7,
) -> SentimentSummary:
    """Aggregate the last `days` of scored news for one symbol.

    Filtering: only items with a valid `published_at` and a non-null
    sentiment score count toward the rolling stats. Items the LLM
    refused (error set) are silently skipped.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    pairs: list[tuple[ScoredHeadline, NewsItem]] = []
    for s, raw in zip(scored, raw_items):
        if s.sentiment is None:
            continue
        if not raw.published_at:
            # No date → assume recent (most Yahoo news is fresh).
            pairs.append((s, raw))
            continue
        try:
            pub = datetime.fromisoformat(raw.published_at.replace("Z", "+00:00"))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except ValueError:
            pairs.append((s, raw))
            continue
        if pub >= cutoff:
            pairs.append((s, raw))

    if not pairs:
        return SentimentSummary(
            items_considered=0,
            mean_sentiment=None,
            very_negative_count=0,
            material_negative_count=0,
            most_negative=None,
        )

    sentiments = [s.sentiment for s, _ in pairs if s.sentiment is not None]
    mean = sum(sentiments) / len(sentiments) if sentiments else None
    very_neg = [s for s, _ in pairs if s.sentiment is not None and s.sentiment <= -0.5]
    material_neg = [s for s in very_neg if s.material]
    worst = min(pairs, key=lambda p: p[0].sentiment if p[0].sentiment is not None else 0)

    return SentimentSummary(
        items_considered=len(pairs),
        mean_sentiment=mean,
        very_negative_count=len(very_neg),
        material_negative_count=len(material_neg),
        most_negative=worst[0].title if worst[0].sentiment is not None and worst[0].sentiment <= 0 else None,
    )
