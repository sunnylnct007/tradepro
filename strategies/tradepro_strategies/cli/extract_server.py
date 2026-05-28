"""Extractor sidecar — small FastAPI service the .NET API forwards
multipart uploads to.

Why a sidecar: PDF / HTML extraction lives in Python (PyMuPDF,
trafilatura), not in .NET. Browser → .NET API (auth, accepts
multipart) → POST to this service → manifest JSON → .NET stores
via DocumentStore. Same shape as the existing tradepro-doc-upload
CLI, just exposed over HTTP.

Default port 8000. Override:
    TRADEPRO_EXTRACTOR_PORT=9000 uv run tradepro-extract-server

Stateless. Survives restarts trivially. No persistence — the
service writes nothing to disk except the temp file used for
extraction (deleted in the same handler).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from ..documents import SUPPORTED_EXTENSIONS, build_manifest, extract


def build_app() -> FastAPI:
    app = FastAPI(title="tradepro-extract", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "tradepro-extract"}

    @app.post("/extract")
    async def extract_endpoint(
        file: UploadFile = File(..., description="The document to extract."),
        title: str = Form(..., description="Display title."),
        symbols: str = Form("", description="Comma-separated linked symbols."),
        source_url: str = Form("", description="Source URL (optional)."),
        uploader: str = Form("browser-upload", description="Free-form uploader id."),
    ) -> dict:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unsupported extension {ext!r}; "
                    f"supported: {', '.join(SUPPORTED_EXTENSIONS)}"
                ),
            )

        # Write to a temp file because the extractors take a path.
        # Bounded by FastAPI's default upload limits — large PDFs are
        # streamed to disk anyway.
        body = await file.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        try:
            tmp.write(body)
            tmp.close()
            tmp_path = Path(tmp.name)
            try:
                extracted = extract(tmp_path)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"extraction failed: {e}")
        finally:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

        # Replace local-temp path with the original filename for
        # provenance — caller doesn't care where on disk we extracted.
        extracted.file_path = file.filename or "(unnamed)"

        manifest = build_manifest(
            extracted=extracted,
            title=title,
            linked_symbols=[s for s in symbols.split(",") if s.strip()],
            source_url=source_url or None,
            uploader=uploader,
        )
        return {"document": manifest.to_dict()}

    return app


# Module-level app so `uvicorn tradepro_strategies.cli.extract_server:app`
# works without the factory.
app = build_app()


def main() -> None:
    import uvicorn
    port = int(os.environ.get("TRADEPRO_EXTRACTOR_PORT", "8000"))
    host = os.environ.get("TRADEPRO_EXTRACTOR_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
