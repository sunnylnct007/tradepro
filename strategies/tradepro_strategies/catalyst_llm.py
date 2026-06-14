"""LLM catalyst judge — the news-reasoning seat in the Decide verdict.

A sentiment MEAN cannot tell a real catalyst from noise: TSLA's SpaceX-IPO
(+0.7) gets averaged to -0.15 by mixed headlines, so the deterministic
`_score_event` news fallback misses it. This module hands the headlines to the
LLM and asks the question a mean can't answer: "is there a GENUINE, directional,
market-moving catalyst for THIS symbol?" — returning a small structured verdict
that feeds the swing event layer.

Provider-agnostic via `get_provider()` (Ollama llama3.1:8b by default — local,
free, no API key). Disk-cached by (symbol, headline-hash) so the LLM is only
called when the news set actually changes; a cache hit is free.

Strict principle (matches the rest of the platform): the LLM produces a
*verdict + reason*, never a price or a fabricated number.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from .llm import LlmProvider, get_provider

CACHE_PATH = Path.home() / ".tradepro" / "cache" / "llm-catalyst.json"

# Catalyst judgment is a REASONING task — telling a real symbol-specific event
# from noise — where the small/fast 8B used for bulk per-headline sentiment
# falls short (it over-attributes + can't calibrate strength). A mid-size local
# model (gemma3:12b, well-calibrated in testing: BULLISH/2 on a real FDA approval,
# NEUTRAL on off-symbol/ambiguous news) fixes that with no cloud/key/cost. Still
# local + free. Override with TRADEPRO_CATALYST_MODEL.
_CATALYST_MODEL = os.environ.get("TRADEPRO_CATALYST_MODEL", "gemma3:12b")
_catalyst_provider_cache: list = []


def _catalyst_provider() -> LlmProvider:
    """Memoised provider for the catalyst seat — a stronger local model than the
    bulk-sentiment default. Falls back to the platform default if Ollama can't
    serve the stronger model."""
    if _catalyst_provider_cache:
        return _catalyst_provider_cache[0]
    try:
        from .llm.ollama_provider import OllamaProvider
        p: LlmProvider = OllamaProvider(model=_CATALYST_MODEL)
        if not p.healthy:
            p = get_provider()
    except Exception:
        p = get_provider()
    _catalyst_provider_cache.append(p)
    return p

_SCHEMA = {
    "event": "the specific market-moving event for THIS symbol, or 'none'",
    "verdict": "BULLISH | BEARISH | NEUTRAL",
    "strength": "0 (none) | 1 (moderate) | 2 (strong)",
    "reason": "one short sentence",
}

# Phrases an 8B model emits when it's actually saying "no catalyst" while still
# (wrongly) stamping a direction. Used to force such rows back to NEUTRAL.
_NO_EVENT_MARKERS = ("none", "no specific", "no catalyst", "no event", "n/a", "generic")

_lock = threading.Lock()


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=0))
    except Exception:
        pass


def _neutral(reason: str, cached: bool = False) -> dict[str, Any]:
    return {"verdict": "NEUTRAL", "strength": 0, "reason": reason, "cached": cached}


def _cache_key(symbol: str, titles: list[str]) -> str:
    # Include the model: a verdict is only valid for the model that produced it,
    # so changing TRADEPRO_CATALYST_MODEL must invalidate the cache (else a model
    # upgrade silently returns the old/weaker model's cached verdicts).
    blob = _CATALYST_MODEL + "|" + symbol + "|" + "|".join(titles)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def judge_catalyst(
    symbol: str,
    news_items: list[dict] | None,
    *,
    provider: LlmProvider | None = None,
    use_cache: bool = True,
    max_items: int = 10,
) -> dict[str, Any]:
    """Return ``{verdict, strength, reason, cached}``.

    Falls back to NEUTRAL (never raises) when there's no news, no healthy
    provider, or the LLM output can't be parsed — so a caller can always
    treat a missing/strong catalyst uniformly and the verdict degrades safely.
    """
    items = [
        (str(n.get("title") or ""), n.get("sentiment"))
        for n in (news_items or [])
        if n.get("title")
    ][:max_items]
    if not items:
        return _neutral("no news")

    titles = [t for t, _ in items]
    key = _cache_key(symbol, titles)
    if use_cache:
        with _lock:
            cache = _load_cache()
        if key in cache:
            return {**cache[key], "cached": True}

    p = provider or _catalyst_provider()
    if not getattr(p, "healthy", False):
        return _neutral("llm provider unavailable")

    headlines = "\n".join(
        f"- [{s:+.2f}] {t}" if isinstance(s, (int, float)) else f"- {t}"
        for t, s in items
    )
    prompt = (
        f"You are an equity trading analyst. Recent news headlines for "
        f"{symbol} (with per-headline sentiment from -1 to +1 where known):\n"
        f"{headlines}\n\n"
        f"Decide whether there is a GENUINE, market-moving catalyst SPECIFIC TO "
        f"{symbol} — a real event (product launch, IPO, M&A, regulatory action, "
        f"earnings beat/miss, guidance change, major partnership) that should move "
        f"the stock — versus generic chatter or coverage that mainly concerns other "
        f"companies. A single strong catalyst can dominate even amid mixed or "
        f"net-negative headlines, so judge the EVENT, not the average tone. "
        f"First identify the specific EVENT (or 'none' if it's just chatter / "
        f"about other companies). Only set verdict=BULLISH or BEARISH when you "
        f"named a real, symbol-specific event — otherwise verdict=NEUTRAL, "
        f"event='none', strength=0. strength: 0=none, 1=moderate, 2=strong."
    )

    res = p.complete_json(prompt, schema_hint=_SCHEMA, max_tokens=220)
    if not res.ok:
        return _neutral(f"llm error: {res.error or 'no output'}")

    out = res.data or {}
    event = str(out.get("event", "")).strip()
    verdict = str(out.get("verdict", "NEUTRAL")).upper().strip()
    if verdict not in ("BULLISH", "BEARISH", "NEUTRAL"):
        verdict = "NEUTRAL"
    try:
        strength = max(0, min(2, int(out.get("strength", 0))))
    except (TypeError, ValueError):
        strength = 0
    # Guardrails against weak-model noise: a directional call MUST be grounded
    # in a named, real event. No event (or a "none"-class marker) → NEUTRAL,
    # even if the model stamped a direction. And clamp strength ≥1 for a real
    # call so a "BULLISH strength 0" can't slip through as actionable.
    event_lc = event.lower()
    has_event = bool(event) and not any(m in event_lc for m in _NO_EVENT_MARKERS)
    if not has_event:
        verdict, strength = "NEUTRAL", 0
    elif verdict in ("BULLISH", "BEARISH") and strength == 0:
        strength = 1
    if verdict == "NEUTRAL":
        strength = 0
    rec = {
        "verdict": verdict,
        "strength": strength,
        "event": event if has_event else "none",
        "reason": str(out.get("reason", ""))[:200],
    }

    if use_cache:
        with _lock:
            cache = _load_cache()
            cache[key] = rec
            _save_cache(cache)
    return {**rec, "cached": False}
