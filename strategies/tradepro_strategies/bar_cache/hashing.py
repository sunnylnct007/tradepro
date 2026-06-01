"""Data-state hashing for reproducibility (Phase D-1).

Closes ``CURRENT_BACKTEST_LIMITATIONS.md`` §L4 ("Reproducibility is
weak"). Every ``BarStore.get()`` call returns a ``data_state_hash``
on the ``BarFrame`` — a SHA256 hex digest computed deterministically
from the manifests of the partitions read.

What goes into the hash
=======================

Per partition (sorted by ``(asset_class, canonical, resolution,
partition)`` for determinism):

    schema_version
    asset_class
    canonical
    resolution
    partition
    actual_bar_count
    file_size_bytes
    provider_used

Excluded on purpose:

    fetched_at_utc   wall-clock timestamp; differs across cache
                     rebuilds even when the bytes are identical, so
                     including it would defeat "rebuild → same hash"
                     reproducibility. The point of the hash is to
                     identify data identity, not fetch identity.

    fetched_by       same reasoning — operator metadata, not data.

    provider_chain   only the *winner* matters for the bytes you
                     ended up with; trying all-providers-in-chain
                     would make the hash depend on provider order.

What the hash buys us
=====================

Two BarStore.get() calls over the same range, against the same
cached partitions, produce the same hash. A backtest stamped with
the hash can later be replayed and we KNOW (not "hope") it used the
same bars. The Phase D-2 PR will plumb the hash into backtest
result envelopes so the cockpit can show "this matches the 2024-08
baseline".

What it does NOT do
===================

* Doesn't hash the parquet bytes themselves. A subtle re-write
  with the same row count + file size would slip through. That's
  a deliberate tradeoff: bytes-hashing is expensive on large
  partitions and the manifest fields above are sufficient signal
  for the cases we care about (yfinance silently revising bars,
  schema bumps, provider switches). A future bytes-hash mode lands
  in Phase D-2 if/when we need stricter equality.

* Doesn't try to be cross-platform-stable for non-data fields
  (timezone formatting, locale-dependent strings). Every input is
  one of the manifest's canonical fields, all serialised through
  ``json.dumps(..., sort_keys=True)`` so the bytes the SHA reads
  are byte-identical across Python versions / platforms.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


# A sentinel returned when the BarStore did NOT touch any partitions
# (empty range, or all cache hits before the hash code ran). The
# sentinel is intentionally distinct from a real digest so callers
# can pivot on it ("no data state to record").
EMPTY_DATA_STATE_HASH = "EMPTY_DATA_STATE"


@dataclass(frozen=True)
class PartitionFingerprint:
    """The minimum identity tuple per partition. Constructed from a
    ``Manifest`` at fingerprint time and consumed by
    ``compute_data_state_hash``."""
    schema_version: str
    asset_class: str
    canonical: str
    resolution: str
    partition: str
    actual_bar_count: int
    file_size_bytes: int
    provider_used: str

    def sort_key(self) -> tuple:
        return (
            self.asset_class,
            self.canonical,
            self.resolution,
            self.partition,
        )

    def to_canonical_dict(self) -> dict:
        # asdict on a frozen dataclass returns a fresh dict — safe
        # to hand to json.dumps. Keys are alphabetised by sort_keys
        # at serialisation time.
        return asdict(self)


def fingerprint_from_manifest(manifest) -> PartitionFingerprint:
    """Build a fingerprint from a ``Manifest`` instance. Tolerates
    ``Manifest``-like duck types so test fixtures don't need to
    import the real class — anything with the listed attributes
    works."""
    return PartitionFingerprint(
        schema_version=str(getattr(manifest, "schema_version", "")),
        asset_class=str(getattr(manifest, "asset_class", "")),
        canonical=str(getattr(manifest, "canonical", "")),
        resolution=str(getattr(manifest, "resolution", "")),
        partition=str(getattr(manifest, "partition", "")),
        actual_bar_count=int(getattr(manifest, "actual_bar_count", 0)),
        file_size_bytes=int(getattr(manifest, "file_size_bytes", 0)),
        provider_used=str(getattr(manifest, "provider_used", "")),
    )


def compute_data_state_hash(
    fingerprints: Iterable[PartitionFingerprint],
) -> str:
    """SHA256 hex digest of the canonicalised partition fingerprints.

    Order-independent: callers can pass fingerprints in any order
    and get the same digest because we sort by ``sort_key()`` before
    serialising.

    Returns ``EMPTY_DATA_STATE_HASH`` when the iterable is empty —
    the convention for "no partitions were read, no state to hash".
    """
    fps: Sequence[PartitionFingerprint] = sorted(
        list(fingerprints), key=lambda fp: fp.sort_key(),
    )
    if not fps:
        return EMPTY_DATA_STATE_HASH

    # json.dumps with sort_keys=True gives deterministic bytes
    # regardless of how the dict was constructed.
    payload = json.dumps(
        [fp.to_canonical_dict() for fp in fps],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def short_hash(full_hash: str, length: int = 12) -> str:
    """Operator-friendly truncation. The cockpit shows ``abc123def456``
    rather than the full 64 chars in compact contexts; an
    investigator looking up "did these two runs use the same data"
    sees the full hash on the detail page."""
    if full_hash == EMPTY_DATA_STATE_HASH:
        return full_hash
    return full_hash[:length]
