"""tradepro-embed — catch up the local embedding store.

Walks every document in the API, chunks any that aren't yet in the
local Parquet vector store, embeds the new chunks via Ollama
(default `mxbai-embed-large`), and writes them to disk. Idempotent.

Run after uploading a new document, or just rely on the comparator
calling update_embeddings() at the start of each run.

    uv run tradepro-embed              # default — embed any new chunks
    uv run tradepro-embed --dry-run    # show what would happen
"""
from __future__ import annotations

import argparse
import json

from ..embeddings import update_embeddings


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write to the embeddings store; print summary.")
    p.add_argument("--json", action="store_true",
                   help="Emit summary as JSON (machine-readable).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    summary = update_embeddings(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, default=str))
        return

    print(f"embedder:   {summary.model} (healthy={summary.embedder_healthy})")
    print(f"docs seen:  {summary.docs_seen} (skipped no_symbols: {summary.docs_skipped_no_symbols})")
    print(f"chunks:     {summary.chunks_total} total, "
          f"{summary.chunks_added} added, "
          f"{summary.chunks_skipped_existing} already present, "
          f"{summary.chunks_failed} failed")
    if summary.failures:
        print("failures:")
        for f in summary.failures[:5]:
            err = f.get("error", "")
            print(f"  - {f.get('stage')}: {err[:120]}")
        if len(summary.failures) > 5:
            print(f"  ... and {len(summary.failures) - 5} more")


if __name__ == "__main__":
    main()
