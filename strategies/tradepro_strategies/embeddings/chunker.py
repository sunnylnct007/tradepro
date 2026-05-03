"""Section-aware text chunking.

Each chunk preserves enough metadata that the rationale's citation
can reference the original source precisely — which document, which
section heading, which page (for PDFs). The chunker doesn't cross
section boundaries: a 5-page PDF produces 5+ chunks, never one chunk
spanning pages 2-3.

Token sizing is approximate (1 token ≈ 4 chars for English). We use
a character-based split rather than a real tokeniser to avoid
adding tiktoken / transformers as a dep — for retrieval purposes
the precise boundary doesn't matter.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

DEFAULT_CHUNK_CHARS = 2000        # ≈ 500 tokens
DEFAULT_OVERLAP_CHARS = 400       # ≈ 100 tokens
MIN_CHUNK_CHARS = 200             # don't emit ultra-short chunks


@dataclass
class Chunk:
    """One unit of retrievable text. The `id` is a stable hash so
    re-chunking the same doc produces the same ids — embeddings
    don't need re-running on idempotent re-chunks."""
    chunk_id: str
    doc_id: str
    symbols: list[str]
    heading: str | None
    page: int | None
    section_index: int
    chunk_in_section: int
    text: str

    @property
    def uri(self) -> str:
        """Citation-stable URI, used by the LLM and the verifier."""
        return f"tradepro://documents/{self.doc_id}#chunk-{self.chunk_id}"

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "symbols": list(self.symbols),
            "heading": self.heading,
            "page": self.page,
            "section_index": self.section_index,
            "chunk_in_section": self.chunk_in_section,
            "text": self.text,
            "uri": self.uri,
        }


def _hash(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _split_text(
    text: str,
    chunk_size: int,
    overlap: int,
) -> Iterable[str]:
    """Sliding-window split with overlap. Tries to break on sentence
    boundaries when within ~10% of the target window so chunks read
    naturally. Falls back to hard char split when no break is
    available within the slack."""
    text = text.strip()
    if len(text) <= chunk_size:
        if len(text) >= MIN_CHUNK_CHARS or len(text) > 0:
            yield text
        return

    pos = 0
    while pos < len(text):
        end = pos + chunk_size
        if end >= len(text):
            tail = text[pos:].strip()
            if tail:
                yield tail
            return

        # Look for a sentence boundary in the last 10% of the window.
        window_lo = end - max(chunk_size // 10, 100)
        window = text[window_lo:end]
        cut_in_window = -1
        for marker in (".\n", ". ", "?\n", "? ", "!\n", "! ", "\n\n"):
            idx = window.rfind(marker)
            if idx > cut_in_window:
                cut_in_window = idx + len(marker)
        cut = (window_lo + cut_in_window) if cut_in_window >= 0 else end

        chunk = text[pos:cut].strip()
        if len(chunk) >= MIN_CHUNK_CHARS:
            yield chunk

        # Step forward minus overlap. Guard against infinite loops by
        # always advancing at least chunk_size - overlap.
        next_pos = cut - overlap if cut - overlap > pos else pos + (chunk_size - overlap)
        pos = next_pos


def chunk_document(
    *,
    doc_id: str,
    symbols: list[str],
    sections: list[dict],
    chunk_size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Chunk every section of a document independently. Section
    boundaries are preserved so chunks never span (e.g.) PDF pages.

    `sections` matches the manifest shape: list of
    {heading, text, page}.
    """
    out: list[Chunk] = []
    for sec_idx, sec in enumerate(sections or []):
        text = (sec.get("text") or "").strip()
        if not text:
            continue
        heading = sec.get("heading")
        page = sec.get("page")
        for chunk_idx, body in enumerate(_split_text(text, chunk_size, overlap)):
            cid = _hash(doc_id, str(sec_idx), str(chunk_idx), body[:200])
            out.append(Chunk(
                chunk_id=cid,
                doc_id=doc_id,
                symbols=[s.upper() for s in symbols if s],
                heading=heading,
                page=page,
                section_index=sec_idx,
                chunk_in_section=chunk_idx,
                text=body,
            ))
    return out
