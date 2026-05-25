"""tradepro-paper-watch — Mac-side daemon that polls the backend for
pending paper-session trigger requests and runs them as subprocesses.

Two operating modes
-------------------
  Continuous (default):
      tradepro-paper-watch
      Polls POST /api/ops/poll-paper every --interval seconds. Blocks
      indefinitely; intended to run as a launchd service.

  One-shot (--once):
      tradepro-paper-watch --once
      Poll exactly once, run the session if one was claimed, then exit.
      Designed for launchd WatchPaths triggers.

Authentication
--------------
  Reads the ingest bearer token from get_secret("ingest-api-token") or
  the TRADEPRO_INGEST_TOKEN env var. Exits 1 if neither is found.

API surface (added to .NET backend in parallel)
-----------------------------------------------
  POST /api/ops/poll-paper
      Request body: {}
      Response (no pending session): {"claimed": false}
      Response (session claimed):    {"claimed": true, "requestId": "...",
                                      "params": {...}}

  POST /api/ops/complete-intraday/{requestId}
      Body: {"status": "completed"|"failed",
             "result_summary": {...},   # on success
             "error": "..."}            # on failure
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time

import requests

from ..secrets import get_secret


log = logging.getLogger("tradepro.paper.daemon")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_token(override: str | None = None) -> str | None:
    """Resolve the ingest bearer token.

    Priority:
      1. Explicit CLI --token flag (rare, for scripts).
      2. get_secret("ingest-api-token") which itself checks TRADEPRO_INGEST_TOKEN
         then AWS Secrets Manager.
    """
    if override:
        return override
    return get_secret("ingest-api-token")


def _api_base(override: str | None = None) -> str:
    return (override or os.environ.get("TRADEPRO_API_URL", "http://localhost:5080")).rstrip("/")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------

def poll_once(api_base: str, token: str) -> dict | None:
    """POST /api/ops/poll-paper.

    Returns the response dict if a session was claimed (claimed=true),
    None if nothing was waiting, or None on network error (logged).
    """
    url = f"{api_base}/api/ops/poll-paper"
    try:
        resp = requests.post(url, headers=_headers(token), json={}, timeout=30)
        resp.raise_for_status()
        data: dict = resp.json()
    except requests.RequestException as exc:
        log.warning("poll-paper request failed (%s): %s", url, exc)
        return None

    if not data.get("claimed"):
        log.debug("poll-paper: nothing pending")
        return None

    log.info("poll-paper: claimed request_id=%s", data.get("requestId"))
    return data


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------

def _complete(api_base: str, token: str, request_id: str, payload: dict) -> None:
    url = f"{api_base}/api/ops/complete-intraday/{request_id}"
    try:
        resp = requests.post(url, headers=_headers(token), json=payload, timeout=30)
        resp.raise_for_status()
        log.info("complete-intraday OK: request_id=%s status=%s", request_id, payload.get("status"))
    except requests.RequestException as exc:
        log.warning("complete-intraday failed for %s: %s", request_id, exc)


def complete_success(api_base: str, token: str, request_id: str, command: str) -> None:
    _complete(api_base, token, request_id, {
        "status": "completed",
        "result_summary": {
            "exit_code": 0,
            "command": command,
        },
    })


def complete_failure(api_base: str, token: str, request_id: str, exit_code: int) -> None:
    _complete(api_base, token, request_id, {
        "status": "failed",
        "error": f"exit_code={exit_code}",
    })


# ---------------------------------------------------------------------------
# Build + run subprocess
# ---------------------------------------------------------------------------

def _parse_params(raw: dict, default_broker: str) -> dict:
    """Extract and normalise session params from the poll-paper response."""
    params = raw.get("params") or {}
    strategy = params.get("strategy", "ichimoku_equity")
    symbols_raw = params.get("symbols", ["AAPL", "MSFT", "NVDA"])
    # Accept both a list and a comma-separated string.
    if isinstance(symbols_raw, str):
        symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]
    else:
        symbols = list(symbols_raw)
    capital_usd = float(params.get("capital_usd", 100_000.0))
    broker = params.get("broker", default_broker)
    placement_mode = params.get("placement_mode", "manual")
    interval = params.get("interval") or None  # None → use strategy default
    return {
        "strategy": strategy,
        "symbols": symbols,
        "capital_usd": capital_usd,
        "broker": broker,
        "placement_mode": placement_mode,
        "interval": interval,
    }


def build_command(params: dict) -> list[str]:
    """Construct the tradepro-paper subprocess argument list."""
    args = [
        sys.executable, "-m", "tradepro_strategies.cli.paper_session",
        "--broker", params["broker"],
        "--strategy", params["strategy"],
        "--symbols", ",".join(params["symbols"]),
        "--capital-usd", str(params["capital_usd"]),
        "--placement-mode", params["placement_mode"],
        "--push",
    ]
    if params["interval"]:
        args += ["--interval", str(params["interval"])]
    return args


def run_session(args: list[str], dry_run: bool) -> int:
    """Run tradepro-paper as a subprocess. Returns the exit code."""
    cmd_str = " ".join(args)
    if dry_run:
        log.info("[dry-run] would run: %s", cmd_str)
        return 0

    log.info("launching paper session: %s", cmd_str)
    try:
        result = subprocess.run(args, check=False)
        log.info("paper session finished with exit_code=%d", result.returncode)
        return result.returncode
    except OSError as exc:
        log.error("failed to launch subprocess: %s", exc)
        return 1


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def daemon_loop(
    *,
    api_base: str,
    token: str,
    interval: int,
    default_broker: str,
    dry_run: bool,
    once: bool,
) -> None:
    log.info(
        "paper-daemon starting (api=%s interval=%ds once=%s dry_run=%s)",
        api_base, interval, once, dry_run,
    )

    while True:
        data = poll_once(api_base, token)

        if data is not None:
            request_id: str = data.get("requestId", "unknown")
            params = _parse_params(data, default_broker)
            args = build_command(params)
            exit_code = run_session(args, dry_run)
            cmd_str = " ".join(args)

            if exit_code == 0:
                complete_success(api_base, token, request_id, cmd_str)
            else:
                complete_failure(api_base, token, request_id, exit_code)

        if once:
            log.info("--once flag set, exiting after single poll")
            break

        log.debug("sleeping %ds until next poll", interval)
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tradepro-paper-watch",
        description="Daemon that polls the backend and runs paper trading sessions.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Poll interval in seconds (default: 60). Ignored with --once.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll exactly once, run if a session is pending, then exit.",
    )
    parser.add_argument(
        "--broker",
        default="t212",
        help="Default broker when the trigger request omits one (default: t212).",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        metavar="URL",
        help="Override TRADEPRO_API_URL (default: http://localhost:5080).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would run but do not actually launch subprocesses.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Explicit bearer token (rarely needed; prefer TRADEPRO_INGEST_TOKEN env var).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    token = _resolve_token(args.token)
    if not token:
        log.error(
            "No ingest token found. Set TRADEPRO_INGEST_TOKEN or store "
            "'ingest-api-token' in AWS Secrets Manager / ~/.tradepro/credentials."
        )
        sys.exit(1)

    api_base = _api_base(args.api_url)

    daemon_loop(
        api_base=api_base,
        token=token,
        interval=args.interval,
        default_broker=args.broker,
        dry_run=args.dry_run,
        once=args.once,
    )


if __name__ == "__main__":
    main()
