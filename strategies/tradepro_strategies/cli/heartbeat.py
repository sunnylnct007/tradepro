"""Periodic Mac → API heartbeat.

The compare push tells the website 'fresh results landed'. The heartbeat
tells it 'the box that produces those results is alive *right now*, and
here's the summary of its last run' — even when the next scheduled
comparator hasn't fired yet.

Payload includes:
- host, kernel, git sha (so we know which version ran)
- sent_at (UTC ISO)
- uptime_seconds (how long this Mac has been on)
- last_refresh: read from the most recent run manifest in
  ~/.tradepro/artefacts — kind, run_id, generated_at, status, stats
- recent_errors: tail of the latest refresh log file

Designed to be called frequently (e.g. every 15 min via launchd) and
also opportunistically at the end of refresh.sh so the success/failure
of each scheduled refresh is captured immediately.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import runstate
from ..observability import git_sha
from .push_to_api import load_credentials, push

ARTEFACT_ROOT = Path.home() / ".tradepro" / "artefacts"
LOG_ROOT = Path.home() / ".tradepro" / "logs"


def _uptime_seconds() -> int | None:
    """Best-effort host uptime. macOS exposes `kern.boottime`; fall back to
    parsing /proc/uptime on Linux. Returns None when neither works."""
    try:
        if platform.system() == "Darwin":
            import subprocess
            out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True)
            # Format: "{ sec = 1714000000, usec = 123 } Sun Apr 28 00:00:00 2024"
            sec = int(out.split("sec = ")[1].split(",")[0])
            return int(datetime.now(timezone.utc).timestamp() - sec)
        if Path("/proc/uptime").exists():
            return int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        return None
    return None


def _latest_manifest() -> dict | None:
    """Most recent manifest.json under ~/.tradepro/artefacts. Surfaces the
    last run's kind, run_id, inputs, and stats — what the heartbeat is
    actually proving with."""
    if not ARTEFACT_ROOT.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for p in ARTEFACT_ROOT.glob("*/manifest.json"):
        try:
            candidates.append((p.stat().st_mtime, p))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    latest = candidates[0][1]
    try:
        return json.loads(latest.read_text())
    except Exception:
        return None


def _recent_log_tail(n: int = 6) -> list[str]:
    """Tail of the latest refresh-*.log so a transient failure is visible
    in the heartbeat without opening the box."""
    if not LOG_ROOT.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for p in LOG_ROOT.glob("refresh-*.log"):
        try:
            candidates.append((p.stat().st_mtime, p))
        except OSError:
            continue
    if not candidates:
        return []
    candidates.sort(reverse=True)
    try:
        lines = candidates[0][1].read_text().splitlines()
        return lines[-n:]
    except Exception:
        return []


def build_payload() -> dict:
    manifest = _latest_manifest()
    last_refresh = None
    if manifest is not None:
        last_refresh = {
            "run_id": manifest.get("run_id"),
            "kind": manifest.get("kind"),
            "generated_at": manifest.get("generated_at") or manifest.get("started_at"),
            "ended_at": manifest.get("ended_at"),
            "status": manifest.get("status", "unknown"),
            "inputs": manifest.get("inputs"),
            "stats": manifest.get("stats"),
        }

    return {
        "host": socket.gethostname(),
        "kernel": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
        "git_sha": git_sha(),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": _uptime_seconds(),
        "last_refresh": last_refresh,
        # Mac is currently running something — surface it so the UI can
        # render 'Processing etf_us_core (10s in)' rather than 'last
        # heartbeat 14 min ago' (and let the user wonder if it's stuck).
        "current_task": runstate.read(),
        "recent_log_tail": _recent_log_tail(),
    }


def _local_target() -> tuple[str, str] | None:
    """If a local API is reachable on localhost:5080, return target +
    dev token so the launchd-installed heartbeat job lights up the
    local UI in addition to whatever's in the creds file. Skipped
    silently when localhost isn't running (i.e., normal prod machine
    with nothing on :5080)."""
    import requests
    base = os.environ.get("TRADEPRO_LOCAL_API_URL", "http://localhost:5080")
    try:
        r = requests.get(f"{base.rstrip('/')}/health", timeout=1.0)
        if r.status_code != 200:
            return None
    except Exception:  # noqa: BLE001
        return None
    token = os.environ.get("TRADEPRO_LOCAL_API_TOKEN", "dev-ingest-token")
    return base.rstrip("/"), token


def _push_to(target: str, token: str, payload: dict) -> None:
    """Best-effort POST that never raises — heartbeat is a fire-and-
    forget signal."""
    try:
        push("heartbeat", payload, target, token)
    except Exception:  # noqa: BLE001
        pass


def send() -> None:
    """Build + POST a heartbeat. Dual-target: file-creds (typically
    prod) AND localhost when reachable. Other CLIs (run_comparison)
    call this directly at task start/end so the UI gets updates
    without waiting for the periodic launchd job. Failures swallowed —
    a missed heartbeat never fails the calling job."""
    payload = build_payload()
    try:
        base, token = load_credentials()
        _push_to(base, token, payload)
    except SystemExit:
        pass
    local = _local_target()
    if local is not None:
        _push_to(local[0], local[1], payload)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload, do not POST",
    )
    p.add_argument(
        "--no-local",
        action="store_true",
        help="skip the localhost fallback target",
    )
    args = p.parse_args()

    payload = build_payload()
    if args.dry_run:
        print(json.dumps(payload, indent=2, default=str))
        return

    targets: list[tuple[str, str, str]] = []  # (label, base, token)
    try:
        base, token = load_credentials()
        targets.append(("file-creds", base, token))
    except SystemExit:
        print("heartbeat: no file credentials configured", file=sys.stderr)

    if not args.no_local:
        local = _local_target()
        if local is not None:
            targets.append(("localhost", local[0], local[1]))

    if not targets:
        print("heartbeat: no reachable targets", file=sys.stderr)
        sys.exit(0)

    for label, base, token in targets:
        try:
            push("heartbeat", payload, base, token)
            print(f"heartbeat → {label} ({base}): ok", file=sys.stderr)
        except SystemExit:
            # push() exits on persistent failure; don't let one
            # target's failure stop the next.
            print(f"heartbeat → {label} ({base}): failed", file=sys.stderr)


if __name__ == "__main__":
    main()
