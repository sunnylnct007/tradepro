"""Top-level ComparePayload + everything that hangs off it."""
from __future__ import annotations

from typing import Literal

from ._base import TPModel
from .external_consensus import ExternalConsensus
from .fundamentals import Fundamentals
from .market_context import MarketContext
from .market_state import MarketState
from .news import NewsItem, SentimentSummary
from .rationale import Rationale
from .regimes import RegimeRow, RegimeSpec


# Bumped on every breaking change. 1.0.0 is the first formalised
# version — all prior payloads are compatible because the schema is
# extra="allow", but readers should fail loudly on mismatched majors.
SCHEMA_VERSION = "1.0.0"


SentimentStatus = Literal[
    "scored",
    "partial",
    "all_failed",
    "no_news",
    "provider_down",
]


class CompareStrategySpec(TPModel):
    """Strategy entry in the payload's strategies list."""
    name: str
    params: dict[str, float] = {}
    label: str


class CompareCurrencyMix(TPModel):
    is_mixed: bool
    primary: str
    currencies: list[str] = []


class CompareError(TPModel):
    symbol: str
    stage: str
    error: str


class CompareLlmTelemetry(TPModel):
    """Per-run LLM activity counters."""
    calls_attempted: int = 0
    calls_succeeded: int = 0
    calls_failed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_latency_ms: int | None = None
    max_latency_ms: int | None = None
    total_scored: int = 0


class CompareLlmDemotionRule(TPModel):
    """Sentiment-driven BUY → WAIT demotion rule. Thresholds travel
    in the data so the UI shows exactly what fired."""
    mean_sentiment_threshold: float
    min_material_negative_count: int
    lookback_days: int
    description: str


class CompareLlmInfo(TPModel):
    """Provider + model + telemetry + demotion rule for the run."""
    provider: str
    model: str
    healthy: bool
    prompt_version: str
    demotion_rule: CompareLlmDemotionRule
    telemetry: CompareLlmTelemetry | None = None


class CompareRow(TPModel):
    """One (symbol × strategy) result row.

    Big-ish object by design — every dimension a user might want to
    inspect for that combo lands here so the UI doesn't have to
    cross-reference between rows + side blocks.
    """
    symbol: str
    strategy: str
    strategy_label: str
    params: dict[str, float] = {}
    bars: int = 0
    stats: dict[str, float | None] = {}
    regimes: list[RegimeRow] = []
    current_action: Literal["BUY", "SELL", "HOLD"]
    latest_signal: int = 0
    latest_bar: str | None = None
    in_position: bool = False
    position_since: str | None = None
    market_state: MarketState
    external_consensus: ExternalConsensus | None = None
    fundamentals: Fundamentals | None = None
    news: list[NewsItem] = []
    sentiment_summary: SentimentSummary | None = None
    sentiment_status: SentimentStatus | None = None
    rationale: Rationale | None = None
    bucket: Literal["BUY", "WAIT", "AVOID"] | None = None
    bucket_reason: str | None = None
    sentiment_demoted: bool = False
    currency: str | None = None
    data_age_days: int | None = None
    rank: int = 0
    error: str | None = None


class CompareBest(TPModel):
    """Headline pick from the run."""
    symbol: str
    strategy: str
    rank_metric: str
    value: float | None = None


class ComparePayload(TPModel):
    """The complete payload that flows from the comparator → API →
    frontend. Top-level entry point for both validation + TS
    generation."""
    schema_version: str = SCHEMA_VERSION
    kind: Literal["compare"] = "compare"
    generated_at: str
    from_: str  # 'from' is reserved in some places; keep python-clean
    to: str
    provider: str
    currency: str
    rank_metric: str
    universe: str | None = None
    run_id: str | None = None
    symbols: list[str] = []
    strategies: list[CompareStrategySpec] = []
    regimes: list[RegimeSpec] = []
    market_context: MarketContext | None = None
    currency_mix: CompareCurrencyMix | None = None
    llm: CompareLlmInfo | None = None
    rows: list[CompareRow] = []
    errors: list[CompareError] = []
    best_per_strategy: dict[str, dict] = {}
    best_overall: CompareBest | None = None

    @classmethod
    def from_payload_dict(cls, data: dict) -> "ComparePayload":
        """Validate-on-the-way-out helper. The producer-side dict has
        a `from` field (Python keyword); remap to from_ for Pydantic.
        Tolerant: extra fields pass through (extra='allow')."""
        if "from" in data and "from_" not in data:
            data = dict(data)
            data["from_"] = data.pop("from")
        return cls.model_validate(data)
