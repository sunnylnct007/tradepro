"""Fetch user-editable settings from the API at run start.

The API is the source of truth (file-backed at
`<Compare:StorePath>/settings.json`). The Mac comparator pulls the
current values once per run and uses them; if the API is unreachable
or returns nonsense, we fall back to the compiled defaults so a
network blip doesn't corrupt the verdict.

Failure mode is loud, not silent: the run logger emits which source
was used (`api` or `defaults`) so the artefact log makes it auditable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests


# Compiled fallbacks — same numbers as the prior hardcoded constants in
# compare.py. The Mac uses these only when the API is unreachable.
DEFAULT_MEAN_SENTIMENT_THRESHOLD = -0.30
DEFAULT_MIN_MATERIAL_NEGATIVE = 2
DEFAULT_LOOKBACK_DAYS = 7


@dataclass
class SentimentSettings:
    mean_sentiment_threshold: float
    min_material_negative_count: int
    lookback_days: int
    source: str          # "api" or "defaults"
    updated_at: str | None = None


def _api_base() -> str:
    return os.environ.get("TRADEPRO_API_URL", "http://localhost:5080").rstrip("/")


def fetch_sentiment_settings(timeout: float = 3.0) -> SentimentSettings:
    """Best-effort fetch. Always returns valid settings — falls back
    to compiled defaults on any failure (network, HTTP, parse,
    out-of-range)."""
    try:
        resp = requests.get(f"{_api_base()}/api/settings", timeout=timeout)
        resp.raise_for_status()
        data = resp.json() or {}
        s = data.get("sentiment") or {}
        threshold = float(s.get("meanSentimentThreshold", DEFAULT_MEAN_SENTIMENT_THRESHOLD))
        min_mat = int(s.get("minMaterialNegativeCount", DEFAULT_MIN_MATERIAL_NEGATIVE))
        lookback = int(s.get("lookbackDays", DEFAULT_LOOKBACK_DAYS))
        if not (-1.0 <= threshold <= 1.0):
            raise ValueError(f"threshold out of range: {threshold}")
        if not (0 <= min_mat <= 50):
            raise ValueError(f"min_material out of range: {min_mat}")
        if not (1 <= lookback <= 60):
            raise ValueError(f"lookback out of range: {lookback}")
        return SentimentSettings(
            mean_sentiment_threshold=threshold,
            min_material_negative_count=min_mat,
            lookback_days=lookback,
            source="api",
            updated_at=data.get("updatedAtUtc"),
        )
    except Exception:  # noqa: BLE001
        return SentimentSettings(
            mean_sentiment_threshold=DEFAULT_MEAN_SENTIMENT_THRESHOLD,
            min_material_negative_count=DEFAULT_MIN_MATERIAL_NEGATIVE,
            lookback_days=DEFAULT_LOOKBACK_DAYS,
            source="defaults",
            updated_at=None,
        )
