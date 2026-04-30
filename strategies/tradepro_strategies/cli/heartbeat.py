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


def send() -> None:
    """Build + POST a heartbeat. Other CLIs (run_comparison) call this
    directly at task start/end so the UI gets updates without waiting for
    the periodic launchd job. Failures are swallowed — a missed heartbeat
    is never important enough to fail the calling job."""
    try:
        base, token = load_credentials()
    except SystemExit:
        return
    try:
        push("heartbeat", build_payload(), base, token)
    except Exception:
        # The heartbeat is best-effort. If the network's down, the next
        # one will land — don't propagate.
        pass


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload, do not POST",
    )
    args = p.parse_args()

    payload = build_payload()
    if args.dry_run:
        print(json.dumps(payload, indent=2, default=str))
        return

    try:
        base, token = load_credentials()
    except SystemExit:
        # load_credentials() exits the process on missing config; for a
        # heartbeat we want to fail quietly so launchd doesn't fill up
        # the error log on a fresh machine without creds yet.
        print("heartbeat skipped: no credentials configured", file=sys.stderr)
        sys.exit(0)
    push("heartbeat", payload, base, token)


if __name__ == "__main__":
    main()
