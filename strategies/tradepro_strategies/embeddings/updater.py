"""Catch-up worker: pull docs from the API, embed any chunks not yet
in the local store. Idempotent — running it multiple times only
embeds new chunks.

Usage:
    from tradepro_strategies.embeddings import update_embeddings
    summary = update_embeddings()
    # {'docs_seen': 4, 'chunks_added': 12, 'chunks_skipped': 8, ...}

Or via the comparator hook (auto-called at run start) — same code
path, no separate cron needed for the single-user case.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

from ..observability import RunLogger
from .chunker import chunk_document
from .embedder import OllamaEmbedder
from .store import EmbeddingStore, default_store


def _api_base() -> str:
    return os.environ.get("TRADEPRO_API_URL", "http://localhost:5080").rstrip("/")


@dataclass
class UpdateSummary:
    docs_seen: int = 0
    docs_skipped_no_symbols: int = 0
    chunks_total: int = 0
    chunks_added: int = 0
    chunks_skipped_existing: int = 0
    chunks_failed: int = 0
    failures: list[dict] = field(default_factory=list)
    embedder_healthy: bool = False
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "docs_seen": self.docs_seen,
            "docs_skipped_no_symbols": self.docs_skipped_no_symbols,
            "chunks_total": self.chunks_total,
            "chunks_added": self.chunks_added,
            "chunks_skipped_existing": self.chunks_skipped_existing,
            "chunks_failed": self.chunks_failed,
            "failures": list(self.failures),
            "embedder_healthy": self.embedder_healthy,
            "model": self.model,
        }


def update_embeddings(
    *,
    embedder: OllamaEmbedder | None = None,
    store: EmbeddingStore | None = None,
    logger: RunLogger | None = None,
    dry_run: bool = False,
) -> UpdateSummary:
    """Catch up the local embedding store with everything in the API.
    Returns a summary so the caller can render it (e.g. log in the
    comparator's run log, or print in a CLI)."""
    embedder = embedder or OllamaEmbedder()
    store = store or default_store()
    summary = UpdateSummary(model=embedder.model)

    summary.embedder_healthy = embedder.healthy()
    if logger:
        logger.emit("embed.start", model=embedder.model,
                    healthy=summary.embedder_healthy, dry_run=dry_run)

    if not summary.embedder_healthy:
        # Embedder unavailable — surface but don't fail the comparator.
        # A future run picks up the new docs once the model's pulled.
        summary.failures.append({
            "stage": "embedder",
            "error": (
                f"embedder unavailable; model '{embedder.model}' may not "
                f"be pulled. Run: ollama pull {embedder.model}"
            ),
        })
        if logger:
            logger.emit("embed.skipped", reason="embedder_unhealthy")
        return summary

    # Fetch the doc list from the API.
    try:
        resp = requests.get(f"{_api_base()}/api/documents", timeout=10)
        resp.raise_for_status()
        docs = (resp.json() or {}).get("documents") or []
    except Exception as e:  # noqa: BLE001
        summary.failures.append({"stage": "fetch_list", "error": str(e)})
        if logger:
            logger.emit("embed.list_failed", error=str(e))
        return summary

    for d in docs:
        summary.docs_seen += 1
        doc_id = d.get("docId") or d.get("doc_id")
        if not doc_id:
            continue
        symbols = d.get("linkedSymbols") or d.get("linked_symbols") or []
        if not symbols:
            summary.docs_skipped_no_symbols += 1
            continue

        # Pull the full doc to get its sections.
        try:
            full = requests.get(
                f"{_api_base()}/api/documents/{doc_id}", timeout=10,
            )
            full.raise_for_status()
            full_data = full.json()
        except Exception as e:  # noqa: BLE001
            summary.failures.append({
                "stage": "fetch_doc", "doc_id": doc_id, "error": str(e),
            })
            if logger:
                logger.emit("embed.doc_fetch_failed",
                            doc_id=doc_id, error=str(e))
            continue

        sections = _extract_sections(full_data)
        chunks = chunk_document(
            doc_id=doc_id, symbols=symbols, sections=sections,
        )
        summary.chunks_total += len(chunks)

        existing = store.doc_chunk_ids(doc_id, embedder.model)
        for c in chunks:
            if c.chunk_id in existing:
                summary.chunks_skipped_existing += 1
                continue
            res = embedder.embed_with_meta(c.text)
            if not res.get("ok"):
                summary.chunks_failed += 1
                summary.failures.append({
                    "stage": "embed", "doc_id": doc_id,
                    "chunk_id": c.chunk_id,
                    "error": res.get("error", "unknown"),
                })
                if logger:
                    logger.emit("embed.failed",
                                doc_id=doc_id, chunk_id=c.chunk_id,
                                error=res.get("error"))
                continue
            if not dry_run:
                store.add(c, res["vector"], embedder.model)
            summary.chunks_added += 1

    if not dry_run:
        store.commit()
    if logger:
        logger.emit("embed.done", **summary.to_dict())
    return summary


def _extract_sections(full_doc: Any) -> list[dict]:
    """Normalise the sections array out of either the .NET API shape
    or the raw manifest shape."""
    if not isinstance(full_doc, dict):
        return []
    raw = full_doc.get("sections")
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, dict)]
    return []
