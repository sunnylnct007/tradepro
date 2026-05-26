"""tradepro-refresh-universes — scrape every Wikipedia symbol universe
in the registry and (optionally) push the result up to the TradePro API.

Default behaviour (no flags): scrape all, log per-universe counts,
exit cleanly. Safe to wire into launchd / cron / Github Actions; it
neither writes to Postgres nor calls the API unless explicitly asked.

  Flags
  -----
    --push           POST the result to ``/api/ingest/universes`` on the
                     API base resolved from credentials. Atomic from the
                     API side (single transaction wipes + re-inserts).
    --out PATH       Also dump the full payload to a local JSON file.
                     Useful for diffing across daily runs / debugging.
    --only N[,M...]  Limit the scrape to the named universes (comma-
                     separated). Skipped when not provided.
    --timeout S      Per-HTTP-fetch timeout in seconds (default 20).

  Credentials (only when --push)
  ------------------------------
    Same chain as the rest of the worker: env vars
    (TRADEPRO_API_BASE_URL + TRADEPRO_INGEST_TOKEN) → AWS Secrets
    Manager → ~/.tradepro/credentials.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..secrets import get_secret
from ..universes import wikipedia as wu


log = logging.getLogger("tradepro.cli.refresh_universes")


def _resolve_creds() -> tuple[str | None, str | None]:
    """Resolve (api_base_url, ingest_token). Returns (None, None) when
    neither side is configured — the caller decides whether that's a
    fatal error (--push) or a no-op (default).
    """
    base = get_secret("api-base-url") or get_secret("api-url")
    token = get_secret("ingest-api-token") or get_secret("ingest-token")
    return (base.rstrip("/") if base else None), token


def _build_payload(batch: dict[str, object]) -> dict[str, object]:
    """Shape the scraper output as the API ingest body.

    Layout::

        {
          "generated_at_utc": "...",
          "universes": [
            {"name": "sp500", "source_url": "...", "symbols": [{...}, ...]},
            ...
          ],
          "errors": {"ftse100": "..."}
        }
    """
    universes: list[dict] = []
    errors = batch.get("_errors") or {}
    for name, symbols in batch.items():
        if name == "_errors":
            continue
        defn = wu.WIKI_UNIVERSES[name]
        universes.append({
            "name": name,
            "source_url": defn.url,
            "symbol_count": len(symbols),  # type: ignore[arg-type]
            "symbols": [s.to_dict() for s in symbols],  # type: ignore[union-attr]
        })
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "universes": universes,
        "errors": errors,
    }


def _push(payload: dict, base: str, token: str, *, timeout: int = 60) -> bool:
    """POST the payload to ``/api/ingest/universes``.

    Returns True on a 2xx, False otherwise (logs the error). We don't
    retry from this CLI — launchd will run it again tomorrow; ingest
    is idempotent (wipe + re-insert) so a missed day costs nothing.
    """
    url = f"{base}/api/ingest/universes"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.error("push failed: %s", exc)
        return False
    if not (200 <= resp.status_code < 300):
        log.error("push HTTP %d: %s", resp.status_code, resp.text[:300])
        return False
    log.info("push ok: %s", resp.text[:200])
    return True


def main() -> None:
    p = argparse.ArgumentParser(
        prog="tradepro-refresh-universes",
        description="Scrape every Wikipedia symbol universe and optionally push to the API.",
    )
    p.add_argument(
        "--push", action="store_true",
        help="POST the result to /api/ingest/universes (requires credentials).",
    )
    p.add_argument(
        "--out", metavar="PATH", default=None,
        help="Also write the full payload as JSON to this path.",
    )
    p.add_argument(
        "--only", metavar="NAMES", default=None,
        help="Comma-separated subset of universes to scrape (default: all in registry).",
    )
    p.add_argument(
        "--timeout", type=int, default=wu.DEFAULT_TIMEOUT_S,
        metavar="S", help=f"Per-fetch timeout (default {wu.DEFAULT_TIMEOUT_S}s).",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",") if s.strip()]

    log.info("scraping universes: %s", only or "all")
    batch = wu.fetch_all_universes(timeout=args.timeout, only=only)

    # Always log the per-universe counts — main visibility surface
    # for an operator running the CLI by hand.
    errors = batch.get("_errors") or {}
    for name, symbols in batch.items():
        if name == "_errors":
            continue
        log.info("  %s: %d symbols", name, len(symbols))  # type: ignore[arg-type]
    if errors:
        for name, msg in errors.items():
            log.warning("  %s: ERROR — %s", name, msg)

    payload = _build_payload(batch)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2))
        log.info("wrote %s", out_path)

    if args.push:
        base, token = _resolve_creds()
        if not base or not token:
            log.error(
                "--push requested but credentials missing "
                "(set TRADEPRO_API_BASE_URL + TRADEPRO_INGEST_TOKEN)",
            )
            sys.exit(2)
        ok = _push(payload, base, token)
        if not ok:
            sys.exit(1)

    # Exit non-zero if every universe failed — the operator running
    # this by hand wants a clear signal. A partial failure (some up,
    # some down) is the expected "degraded" state and exits 0 with
    # the warnings logged.
    real_universes = [k for k in batch if k != "_errors"]
    if not real_universes:
        log.error("no universes parsed successfully")
        sys.exit(1)


if __name__ == "__main__":
    main()
