"""Versioned data-model schema for everything the comparator emits.

The compare payload accumulated organically — rows, regimes, sentiment,
LLM info, currency, errors. This subpackage formalises the contract so:

  1. The Python side validates outputs against the schema before
     serialising — drift fails fast at runtime, not 3 commits later
     in a CI build.
  2. TypeScript types are generated from the Python schema (see
     tools/gen_ts_types.py) — no more hand-written drift.
  3. Every payload carries a `schema_version` field. A consumer that
     understands version X can fast-fail when handed Y, instead of
     silently breaking.

Versioning policy
-----------------
- Compatible additions (new optional field, more enum values): no
  bump, frontend keeps working.
- Breaking changes (field removed, type changed, semantics shifted):
  major bump. Old API responses + new client (or vice versa) is a
  bug we want surfaced loudly.

When you add a field, decide which it is and update SCHEMA_VERSION
accordingly. Document the change at the top of compare.py.
"""
from .compare import (
    SCHEMA_VERSION,
    CompareBest,
    ComparePayload,
    CompareRow,
    CompareStrategySpec,
    CompareCurrencyMix,
    CompareError,
    CompareLlmInfo,
    CompareLlmTelemetry,
    CompareLlmDemotionRule,
)
from .market_state import (
    DecisionCheck,
    MarketState,
)
from .market_context import MarketContext
from .news import (
    NewsItem,
    SentimentSummary,
)
from .regimes import (
    RegimeRow,
    RegimeSpec,
)
from .fundamentals import (
    Fundamentals,
    TopHolding,
)
from .external_consensus import ExternalConsensus
from .rationale import Rationale

__all__ = [
    "SCHEMA_VERSION",
    "CompareBest",
    "ComparePayload",
    "CompareRow",
    "CompareStrategySpec",
    "CompareCurrencyMix",
    "CompareError",
    "CompareLlmInfo",
    "CompareLlmTelemetry",
    "CompareLlmDemotionRule",
    "Rationale",
    "DecisionCheck",
    "MarketState",
    "MarketContext",
    "NewsItem",
    "SentimentSummary",
    "RegimeRow",
    "RegimeSpec",
    "Fundamentals",
    "TopHolding",
    "ExternalConsensus",
]
