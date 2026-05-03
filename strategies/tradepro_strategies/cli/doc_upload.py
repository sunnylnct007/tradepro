"""CLI: extract a document on the Mac and push it to the API.

    uv run tradepro-doc-upload prospectus.pdf \\
        --symbols QQQ,VOO \\
        --title "Vanguard S&P 500 prospectus 2026" \\
        --source-url https://...

The extraction happens locally (pdfplumber / trafilatura). Only the
JSON manifest + extracted text travel to the API — raw PDFs stay on
the Mac, mirroring the broader 'Mac is source of truth, API is the
shop window' posture.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from ..documents import SUPPORTED_EXTENSIONS, build_manifest, extract
from .push_to_api import load_credentials, push


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("file", type=Path,
                   help="Document to upload (.pdf / .html / .htm / .txt / .md)")
    p.add_argument("--title", default=None,
                   help="Display title; defaults to filename")
    p.add_argument("--symbols", default="",
                   help="Comma-separated symbols this doc applies to (e.g. QQQ,VOO)")
    p.add_argument("--source-url", default=None)
    p.add_argument("--uploader", default=None,
                   help="Free-form 'who uploaded this'; defaults to host:user")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract + print summary, do not POST")
    return p.parse_args()


def _default_uploader() -> str:
    return f"{socket.gethostname()}:{os.environ.get('USER', 'unknown')}"


def main() -> None:
    args = parse_args()
    if not args.file.exists():
        sys.exit(f"file not found: {args.file}")
    ext = args.file.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        sys.exit(
            f"unsupported extension {ext!r}; "
            f"supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    print(f"extracting {args.file} ...", file=sys.stderr)
    extracted = extract(args.file)
    print(
        f"  → {extracted.char_count:,} chars in "
        f"{len(extracted.sections)} section(s) "
        f"({extracted.extractor})",
        file=sys.stderr,
    )

    title = args.title or args.file.stem
    symbols = [s for s in args.symbols.split(",") if s.strip()]
    manifest = build_manifest(
        extracted=extracted,
        title=title,
        linked_symbols=symbols,
        source_url=args.source_url,
        uploader=args.uploader or _default_uploader(),
    )

    payload = {"document": manifest.to_dict()}

    if args.dry_run:
        print(json.dumps(
            {**payload, "preview": extracted.full_text[:600]},
            indent=2, default=str,
        ))
        return

    base, token = load_credentials()
    push("document", payload, base, token)
    print(f"uploaded: doc_id={manifest.doc_id} title={title!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
