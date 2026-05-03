"""Text extractors per file type.

PDFs: pdfplumber (preserves text + tables). Falls back to pypdf if
pdfplumber misbehaves on a particular file.
HTML: trafilatura (boilerplate removal, keeps article body).
TXT/MD: pass-through (sanitised line endings only).

Each returns an `ExtractedDocument` with structured sections so the
chunker (next phase) can preserve "from section: <heading>" metadata
that follows through to the rationale's citations.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# Supported extensions — the rest of the platform branches on this.
SUPPORTED_EXTENSIONS = (".pdf", ".html", ".htm", ".txt", ".md")


@dataclass
class ExtractedSection:
    heading: str | None
    text: str
    page: int | None = None


@dataclass
class ExtractedDocument:
    """Structured extract — enough for the chunker to preserve section
    boundaries when it splits into embedding-ready chunks."""
    file_path: str
    file_kind: Literal["pdf", "html", "text"]
    sha256: str
    char_count: int
    page_count: int | None
    sections: list[ExtractedSection] = field(default_factory=list)
    extracted_at: str = ""
    extractor: str = ""

    @property
    def full_text(self) -> str:
        """Plain concatenation — used for searching, hashing, simple
        retrieval before chunking is wired in."""
        out: list[str] = []
        for s in self.sections:
            if s.heading:
                out.append(f"# {s.heading}")
            out.append(s.text)
        return "\n\n".join(out).strip()

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "file_kind": self.file_kind,
            "sha256": self.sha256,
            "char_count": self.char_count,
            "page_count": self.page_count,
            "extracted_at": self.extracted_at,
            "extractor": self.extractor,
            "sections": [
                {"heading": s.heading, "text": s.text, "page": s.page}
                for s in self.sections
            ],
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_pdf(path: Path) -> ExtractedDocument:
    """PDF extraction. PyMuPDF (fitz) is the primary engine — faster
    and more reliable across messy PDFs than pdfplumber, which we
    keep as a fallback when fitz can't import (env-specific)."""
    try:
        return _extract_pdf_pymupdf(path)
    except (ImportError, RuntimeError):
        return _extract_pdf_pdfplumber(path)


def _extract_pdf_pymupdf(path: Path) -> ExtractedDocument:
    """Primary PDF path. PyMuPDF (pymupdf) — 5-10x faster than
    pdfplumber on large prospectuses, handles columnar layouts
    cleanly, copes with mixed-encoding text where pdfplumber drops
    glyphs."""
    try:
        import pymupdf  # noqa: F401  — modern import name
        import fitz     # legacy alias still exposed by pymupdf
    except ImportError as e:
        raise RuntimeError(f"pymupdf not installed: {e}")

    sections: list[ExtractedSection] = []
    page_count = 0
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            txt = page.get_text("text") or ""
            txt = txt.strip()
            if not txt:
                continue
            sections.append(ExtractedSection(
                heading=f"page {i}",
                text=txt,
                page=i,
            ))
            page_count += 1

    char_count = sum(len(s.text) for s in sections)
    return ExtractedDocument(
        file_path=str(path),
        file_kind="pdf",
        sha256=_sha256(path),
        char_count=char_count,
        page_count=page_count,
        sections=sections,
        extracted_at=_now_iso(),
        extractor="pymupdf",
    )


def _extract_pdf_pdfplumber(path: Path) -> ExtractedDocument:
    """Fallback PDF path. Slower but pure-Python (BSD-licensed).
    Useful when fitz can't load — corner cases on weird PDF formats."""
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError(f"pdfplumber not installed: {e}")

    sections: list[ExtractedSection] = []
    page_count = 0
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            txt = page.extract_text() or ""
            txt = txt.strip()
            if not txt:
                continue
            sections.append(ExtractedSection(
                heading=f"page {i}",
                text=txt,
                page=i,
            ))
            page_count += 1

    char_count = sum(len(s.text) for s in sections)
    return ExtractedDocument(
        file_path=str(path),
        file_kind="pdf",
        sha256=_sha256(path),
        char_count=char_count,
        page_count=page_count,
        sections=sections,
        extracted_at=_now_iso(),
        extractor="pdfplumber",
    )


def extract_html(path: Path) -> ExtractedDocument:
    """HTML via trafilatura. Returns one big section — trafilatura
    already strips boilerplate (nav, ads, footers). Heading detection
    will come with chunking."""
    try:
        import trafilatura
    except ImportError as e:
        raise RuntimeError(f"trafilatura not installed: {e}")

    raw = path.read_text(encoding="utf-8", errors="replace")
    text = trafilatura.extract(raw, include_comments=False, include_tables=True) or ""
    text = text.strip()
    sections = [ExtractedSection(heading=None, text=text)] if text else []
    return ExtractedDocument(
        file_path=str(path),
        file_kind="html",
        sha256=_sha256(path),
        char_count=len(text),
        page_count=None,
        sections=sections,
        extracted_at=_now_iso(),
        extractor="trafilatura",
    )


def extract_text(path: Path) -> ExtractedDocument:
    """Plain text / markdown. Pass-through with line-ending normalisation."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    return ExtractedDocument(
        file_path=str(path),
        file_kind="text",
        sha256=_sha256(path),
        char_count=len(raw),
        page_count=None,
        sections=[ExtractedSection(heading=None, text=raw)] if raw else [],
        extracted_at=_now_iso(),
        extractor="text",
    )


def extract(path: Path | str) -> ExtractedDocument:
    """Dispatch by extension. Raises ValueError on unsupported types
    so the caller can surface a clean message in the upload UI."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(p)
    if ext in (".html", ".htm"):
        return extract_html(p)
    if ext in (".txt", ".md"):
        return extract_text(p)
    raise ValueError(
        f"unsupported document type {ext!r}; "
        f"supported: {', '.join(SUPPORTED_EXTENSIONS)}"
    )
