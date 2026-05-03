"""Document manifest — what the API receives + persists.

The manifest is the JSON envelope around an extracted document. The
raw file isn't pushed (it could be huge); instead the extracted text
+ structural metadata lands at the API, where retrieval at decision
time is fast.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .extractor import ExtractedDocument


@dataclass
class DocumentManifest:
    doc_id: str
    title: str
    source_url: str | None
    linked_symbols: list[str]
    file_kind: str
    sha256: str
    char_count: int
    page_count: int | None
    extracted_at: str
    extractor: str
    uploaded_at: str
    uploader: str | None
    sections: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source_url": self.source_url,
            "linked_symbols": list(self.linked_symbols),
            "file_kind": self.file_kind,
            "sha256": self.sha256,
            "char_count": self.char_count,
            "page_count": self.page_count,
            "extracted_at": self.extracted_at,
            "extractor": self.extractor,
            "uploaded_at": self.uploaded_at,
            "uploader": self.uploader,
            "sections": list(self.sections),
        }


def build_manifest(
    *,
    extracted: ExtractedDocument,
    title: str,
    linked_symbols: list[str],
    source_url: str | None = None,
    uploader: str | None = None,
    doc_id: str | None = None,
) -> DocumentManifest:
    return DocumentManifest(
        doc_id=doc_id or str(uuid.uuid4()),
        title=title,
        source_url=source_url,
        linked_symbols=[s.strip().upper() for s in linked_symbols if s.strip()],
        file_kind=extracted.file_kind,
        sha256=extracted.sha256,
        char_count=extracted.char_count,
        page_count=extracted.page_count,
        extracted_at=extracted.extracted_at,
        extractor=extracted.extractor,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
        uploader=uploader,
        sections=[
            {"heading": s.heading, "text": s.text, "page": s.page}
            for s in extracted.sections
        ],
    )
