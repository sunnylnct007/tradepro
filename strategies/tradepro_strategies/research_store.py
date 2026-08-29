"""Research artifacts belong in S3, not only on this laptop.

Owner, 29 Aug 2026: *"data harvesting is key and i keep on repeating"* and
*"we shd leverage broker platform but shd start storing in our cheap s3 so we
can leverage"*.

He is right and this was a real gap. Bars have mirrored to S3 since 22 August,
but everything harvested SINCE went to `~/.tradepro/research/*.json` and nowhere
else:

    earnings_history.json   5,062 events across 205 symbols
    fundamentals.json       P/E, ROE, margins, 4y EPS for 244 symbols
    india_index.json, index_strangle_paper.json

Local-only, on a machine whose battery died twice this week. That also breaks the
standing storage policy (Postgres + S3; local files are a cache, never the
durable tier) -- a rule this project wrote after losing work exactly this way.

Reuses `bar_cache.s3_mirror.S3Mirror` rather than adding a second uploader: same
bucket, same credential chain (boto3 default, then the scoped keys that let
launchd daemons work without an SSO session), different prefix. A second
implementation of the same rule is how the first one stops being followed.

Fail-safe throughout. A missing bucket or dead network leaves the local file
untouched and returns False -- harvesting must never fail because a mirror is
down, but it must SAY so rather than pretend it saved.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("tradepro.research_store")

RESEARCH_DIR = Path(os.path.expanduser("~/.tradepro/research"))
S3_PREFIX = os.environ.get("TRADEPRO_RESEARCH_S3_PREFIX", "research")


def _mirror():
    """An S3Mirror rooted at the research dir, or None when unconfigured."""
    try:
        from .bar_cache.s3_mirror import S3Mirror, _scoped_conf  # noqa: PLC0415
        if (os.environ.get("TRADEPRO_BAR_CACHE_S3_DISABLE") or "").strip().lower() \
                in ("1", "true", "yes", "on"):
            return None
        bucket = (os.environ.get("TRADEPRO_BAR_CACHE_S3_BUCKET") or "").strip() \
            or (_scoped_conf("bar-cache-s3-bucket") or "").strip()
        if not bucket:
            return None
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        return S3Mirror(bucket, S3_PREFIX, RESEARCH_DIR)
    except Exception as exc:  # noqa: BLE001 — never block a harvest
        _log.warning("research S3 mirror unavailable: %s", exc)
        return None


def save(name: str, obj: Any) -> bool:
    """Write an artifact locally AND push it to S3. Returns True if MIRRORED.

    The return value is deliberately about the mirror, not the local write: the
    local write is not in question, and a caller that logs "saved" on a failed
    upload is the exact silent-gap shape this codebase keeps paying for.
    """
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path = RESEARCH_DIR / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)                      # atomic: never a half-written artifact
    m = _mirror()
    if m is None:
        _log.warning("%s written LOCAL ONLY — no S3 bucket configured", name)
        return False
    ok = bool(m.upload(path))
    _log.info("%s -> %s", name, f"s3://{m.bucket}/{S3_PREFIX}/{name}" if ok
              else "LOCAL ONLY (upload failed)")
    return ok


def load(name: str, default: Any = None) -> Any:
    """Read an artifact, pulling from S3 on a local miss.

    Read-through is the half that makes the mirror worth having: a fresh machine,
    or one whose disk was cleared, gets the data instead of silently harvesting
    it all again.
    """
    path = RESEARCH_DIR / name
    if not path.exists():
        m = _mirror()
        if m is not None and m.download(path):
            _log.info("%s restored from S3", name)
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def sync_all() -> tuple[int, int]:
    """Push every local artifact up. Returns (uploaded, failed)."""
    m = _mirror()
    if m is None:
        _log.error("no S3 bucket configured — nothing mirrored")
        return 0, 0
    up = bad = 0
    for p in sorted(RESEARCH_DIR.glob("*.json")):
        if m.upload(p):
            up += 1
            print(f"  ✓ {p.name:<34} -> s3://{m.bucket}/{S3_PREFIX}/{p.name}")
        else:
            bad += 1
            print(f"  ✗ {p.name:<34} upload FAILED (kept local)")
    return up, bad


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(f"research artifacts in {RESEARCH_DIR}")
    up, bad = sync_all()
    print(f"\n{up} mirrored, {bad} failed")
    return 0 if bad == 0 and up > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
