"""Document ingestion: extract text from uploaded PDFs / HTML / TXT,
package with manifest, push to the API.

Usage from the Mac:

    uv run tradepro-doc-upload prospectus.pdf \\
        --symbols QQQ,VOO \\
        --title "Vanguard S&P 500 ETF prospectus 2026"

The extracted text + structured manifest land at
    /api/documents/<doc_id>
and are available for retrieval at decision time (Phase 5c-iii.)

Strict accuracy contract: the LLM rationale (Phase 6c) treats
retrieved chunks the same as any other input fact — every claim
referencing a chunk must verify against the chunk text, otherwise
the rationale falls back to template.
"""
from .extractor import (
    SUPPORTED_EXTENSIONS,
    ExtractedDocument,
    extract,
    extract_html,
    extract_pdf,
    extract_text,
)
from .manifest import DocumentManifest, build_manifest

__all__ = [
    "DocumentManifest",
    "ExtractedDocument",
    "SUPPORTED_EXTENSIONS",
    "build_manifest",
    "extract",
    "extract_html",
    "extract_pdf",
    "extract_text",
]
