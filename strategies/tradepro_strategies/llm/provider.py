"""LlmProvider interface + the no-op fallback.

Each method returns a `LlmResult` so callers can branch on success vs
failure uniformly without try/except blocks all over the place. A
parse failure / network error → `LlmResult(ok=False, ...)`, never an
exception. The comparator always finishes a run; sentiment just
becomes 'no signal' for that row.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LlmResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    error: str | None = None
    latency_ms: int | None = None
    model: str | None = None

    @classmethod
    def fail(cls, error: str, raw: str = "") -> "LlmResult":
        return cls(ok=False, error=error, raw=raw)


class LlmProvider(ABC):
    """Minimal contract every provider implements. Methods return
    LlmResult — never raise — so callers can compose pipelines without
    propagating provider failures."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete_json(self, prompt: str, *, schema_hint: dict | None = None,
                      max_tokens: int = 256, temperature: float = 0.0) -> LlmResult:
        """Run a prompt and parse the response as JSON. `schema_hint` is
        embedded into the prompt so the model knows the expected shape;
        the implementation must return ok=False if the response can't
        be parsed as JSON, rather than raising."""

    def healthy(self) -> bool:
        """Cheap probe — returns True if the provider is reachable. Used
        by news_sentiment to short-circuit a run when the provider is
        down (skip scoring entirely vs hammer with retries)."""
        return True


class NoOpProvider(LlmProvider):
    """Returned when no LLM is configured. Every call cleanly fails so
    the comparator runs to completion without sentiment data — verdicts
    just don't get the LLM-augmented decision_trace check."""

    @property
    def name(self) -> str: return "noop"

    @property
    def model(self) -> str: return "none"

    def complete_json(self, prompt: str, *, schema_hint: dict | None = None,
                      max_tokens: int = 256, temperature: float = 0.0) -> LlmResult:
        return LlmResult.fail("no LLM provider configured")

    def healthy(self) -> bool:
        return False
