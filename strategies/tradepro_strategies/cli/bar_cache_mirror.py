"""tradepro-bar-cache-mirror — push the harvested bar store to S3.

Owner, 15 Aug 2026: *"we need to move data into s3 and not rely on mac"* —
and the standing storage policy has always been PG + S3, with local files
forbidden as a durable tier. The bar store has been violating that: ~297 MB
of harvested IBKR history (16 years for the older symbols) sitting on one
Mac's disk with no copy anywhere.

That store is the expensive asset. Re-fetching it from IBKR is precisely the
rate-limit wall we are designing around, so losing the disk means losing
years of history we cannot cheaply rebuild.

This wraps the existing `bar_cache.s3_mirror` in a runnable, schedulable
job that reports to the central run_log, so "is our data actually in S3, and
as of when" becomes answerable from the data-readiness screen rather than
assumed.

Credentials: standard boto3 chain (env / ~/.aws / instance role). SSO tokens
expire within hours and are NOT suitable here — use a dedicated IAM user with
write access limited to this bucket.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _bucket() -> str | None:
    return (os.environ.get("TRADEPRO_BAR_CACHE_S3_BUCKET") or "").strip() or None


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror the harvested bar store to S3")
    ap.add_argument("--base-dir", default=os.path.expanduser("~/.tradepro/bar_cache"))
    ap.add_argument("--prefix", default=os.environ.get("TRADEPRO_BAR_CACHE_S3_PREFIX", "bar_cache"))
    ap.add_argument("--dry-run", action="store_true",
                    help="report what WOULD upload, touch nothing")
    args = ap.parse_args()

    started = datetime.now(timezone.utc)
    base = Path(args.base_dir)
    bucket = _bucket()

    files = sorted(base.rglob("*.parquet"))
    total_bytes = sum(f.stat().st_size for f in files)
    summary_head = (f"{len(files)} parquet partitions, "
                    f"{total_bytes / 1_048_576:.0f} MB under {base}")

    if not bucket:
        # FAIL LOUD: silence here would mean believing the data is safe when
        # it exists in exactly one place.
        msg = ("TRADEPRO_BAR_CACHE_S3_BUCKET is not set — the harvested bar "
               "store is NOT backed up and exists only on this machine. "
               f"{summary_head}")
        print(f"bar-cache-mirror FAIL: {msg}", file=sys.stderr)
        _log_run("fail", msg, started)
        return 1

    if args.dry_run:
        print(f"bar-cache-mirror DRY RUN: would upload {summary_head} "
              f"to s3://{bucket}/{args.prefix}/")
        return 0

    try:
        from ..bar_cache.s3_mirror import S3Mirror
        mirror = S3Mirror(bucket=bucket, prefix=args.prefix, base_dir=base)
    except Exception as exc:  # noqa: BLE001
        msg = f"S3Mirror unavailable ({exc}) — bar store still unprotected"
        print(f"bar-cache-mirror FAIL: {msg}", file=sys.stderr)
        _log_run("fail", msg, started)
        return 1

    uploaded = failed = 0
    first_error: str | None = None
    for f in files:
        try:
            mirror.upload(f)
            uploaded += 1
        except Exception as exc:  # noqa: BLE001 — keep going, report the count
            failed += 1
            if first_error is None:
                first_error = f"{f.name}: {exc}"

    status = "ok" if failed == 0 else ("partial" if uploaded else "fail")
    summary = (f"s3://{bucket}/{args.prefix} — uploaded {uploaded}/{len(files)} "
               f"partitions ({total_bytes / 1_048_576:.0f} MB)"
               + (f"; {failed} failed, first: {first_error}" if failed else ""))
    print(f"bar-cache-mirror {status}: {summary}")
    _log_run(status, summary, started)
    return 0 if failed == 0 else 1


def _log_run(status: str, text: str, started: datetime) -> None:
    try:
        from ..run_log import log_run
        from .push_to_api import load_credentials
        base_url, token = load_credentials()
        log_run("bar-cache-mirror", "backup", status,
                summary=text if status == "ok" else None,
                error=None if status == "ok" else text,
                started=started, base=base_url, token=token)
    except Exception:  # noqa: BLE001 — never let reporting break the job
        pass


if __name__ == "__main__":
    sys.exit(main())
