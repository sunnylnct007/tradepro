"""Typed errors for the bar cache.

Errors carry structured fields so callers can pivot on them without
parsing message text. ``retry_strategy`` is the hint the caller uses
to decide what to do next ("switch_provider" → try next in chain;
"user_intervention" → stop and surface to operator; "fatal" → don't
retry under any circumstances).

The class hierarchy is deliberately shallow — every error inherits
from ``BarFetchError`` so a single ``except BarFetchError`` clause
catches everything the cache can produce. The structured ``error_class``
field is what differentiates them for the telemetry log + cockpit UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BarFetchError(Exception):
    """Base class for every error the bar cache produces. Never raised
    directly — callers see the concrete subclass below. The structured
    fields end up in ``bar_cache_events.error_*`` columns.

    ``retry_strategy`` is the machine-readable hint:
      - "exponential_backoff"  retry the same provider after a delay
      - "switch_provider"      try the next provider in the chain
      - "user_intervention"    operator must take action; don't auto-retry
      - "fatal"                no remedy; surface to UI as a hard failure
    """
    error_class: str
    provider: str
    canonical: str
    message: str
    expected: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)
    retry_strategy: str = "switch_provider"

    def __str__(self) -> str:
        return (
            f"{self.error_class} [{self.provider} → {self.canonical}]: "
            f"{self.message}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class,
            "provider": self.provider,
            "canonical": self.canonical,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "retry_strategy": self.retry_strategy,
        }


class ProviderRateLimitError(BarFetchError):
    """Provider returned 429 / rate-limit signal. Chain should
    fall through to the next provider; same provider can be retried
    later with exponential backoff."""

    def __init__(self, provider: str, canonical: str, message: str = "rate limited"):
        super().__init__(
            error_class="rate_limit",
            provider=provider,
            canonical=canonical,
            message=message,
            retry_strategy="switch_provider",
        )


class ProviderNetworkError(BarFetchError):
    """Network / connection failure talking to the provider. Chain
    falls through; same provider retryable with backoff."""

    def __init__(self, provider: str, canonical: str, message: str):
        super().__init__(
            error_class="network",
            provider=provider,
            canonical=canonical,
            message=message,
            retry_strategy="exponential_backoff",
        )


class ProviderParseError(BarFetchError):
    """Provider returned data we couldn't parse (schema drift,
    unexpected nulls in required columns, etc.). Chain falls through;
    operator intervention if the same shape repeats — likely a
    provider-side change we need to adapt to."""

    def __init__(self, provider: str, canonical: str, message: str):
        super().__init__(
            error_class="parse",
            provider=provider,
            canonical=canonical,
            message=message,
            retry_strategy="user_intervention",
        )


class NoProviderAvailableError(BarFetchError):
    """Every provider in the chain failed. The chain has been
    exhausted; the caller cannot proceed with a fetch right now.
    Fatal at the request level — retrying immediately won't help.
    """

    def __init__(self, canonical: str, asset_class: str, resolution: str,
                 attempted: list[str]):
        super().__init__(
            error_class="no_provider",
            provider=",".join(attempted),
            canonical=canonical,
            message=(
                f"all providers in the chain failed for "
                f"{canonical}/{asset_class}/{resolution}"
            ),
            expected={"asset_class": asset_class, "resolution": resolution},
            actual={"providers_tried": attempted},
            retry_strategy="user_intervention",
        )


class ManifestViolation(BarFetchError):
    """On-disk Parquet content doesn't match what the manifest
    declares. Could be a partial write that survived (Phase B-1 uses
    atomic writes so this should be impossible), an external mutation
    of the cache directory, or a schema-version drift.

    The strategy must NEVER trust a partition with a manifest
    violation — silent partial reads are the banned behaviour."""

    def __init__(self, canonical: str, partition: str,
                 expected: dict[str, Any], actual: dict[str, Any]):
        super().__init__(
            error_class="manifest",
            provider="bar_cache",
            canonical=canonical,
            message=(
                f"manifest violation on partition {partition} for "
                f"{canonical}: expected {expected}, actual {actual}"
            ),
            expected=expected,
            actual=actual,
            retry_strategy="user_intervention",
        )


class SchemaVersionMismatch(BarFetchError):
    """The on-disk partition was written by a different schema version
    than the asset-class plugin currently declares. The partition
    needs to be re-fetched or repartitioned (Phase D).

    Until Phase D ships, the remedy is delete the partition and
    re-fetch."""

    def __init__(self, canonical: str, partition: str,
                 expected_version: str, actual_version: str):
        super().__init__(
            error_class="schema",
            provider="bar_cache",
            canonical=canonical,
            message=(
                f"schema version mismatch on partition {partition} for "
                f"{canonical}: expected {expected_version}, got {actual_version}"
            ),
            expected={"schema_version": expected_version},
            actual={"schema_version": actual_version},
            retry_strategy="user_intervention",
        )
