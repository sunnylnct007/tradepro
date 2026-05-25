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

SQS mode (preferred when TRADEPRO_PAPER_SQS_URL is set):
      tradepro-paper-watch --sqs-url https://sqs.eu-west-1.amazonaws.com/...
      OR export TRADEPRO_PAPER_SQS_URL=...
      Long-polls SQS (WaitTimeSeconds=20) instead of REST polling.
      Falls back to REST polling if boto3 is not installed.

Authentication
--------------
  Reads the ingest bearer token from get_secret("ingest-api-token") or
  the TRADEPRO_INGEST_TOKEN env var. Exits 1 if neither is found.

API surface (added to .NET backend in parallel)
-----------------------------------------------
  POST /api/ops/poll-paper
      Request body: {}
      Response (no pending session): {"claimed": false}
      Response (session claimed):    {"claimed": true,
                                      "session": {"request_id": "...",
                                                  "params": {...}, ...}}

  POST /api/ops/complete-paper/{requestId}
      Body: {"status": "completed"|"failed",
             "result_summary": {...},   # on success
             "error": "..."}            # on failure
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date

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
    url = f"{api_base}/api/ops/complete-paper/{request_id}"
    try:
        resp = requests.post(url, headers=_headers(token), json=payload, timeout=30)
        resp.raise_for_status()
        log.info("complete-paper OK: request_id=%s status=%s", request_id, payload.get("status"))
    except requests.RequestException as exc:
        log.warning("complete-paper failed for %s: %s", request_id, exc)


def complete_success(
    api_base: str,
    token: str,
    request_id: str,
    command: str,
    summary: dict | None = None,
) -> None:
    payload: dict = {"exit_code": 0, "command": command}
    if summary:
        payload.update(summary)
    _complete(api_base, token, request_id, {
        "status": "completed",
        "result_summary": payload,
    })


def complete_failure(
    api_base: str,
    token: str,
    request_id: str,
    exit_code: int,
    error: str | None = None,
) -> None:
    _complete(api_base, token, request_id, {
        "status": "failed",
        "error": error or f"exit_code={exit_code}",
    })


# ---------------------------------------------------------------------------
# Snapshot extraction
# ---------------------------------------------------------------------------

def _extract_snapshots_from_stdout(stdout: str) -> list[dict]:
    """Best-effort: parse any `paper-snapshot` JSON blocks printed to stdout.

    The subprocess pretty-prints the snapshot to stdout before pushing to the
    backend. We scan for top-level JSON objects whose `kind == "paper-snapshot"`.
    """
    if not stdout:
        return []
    decoder = json.JSONDecoder()
    snapshots: list[dict] = []
    pos = 0
    while pos < len(stdout):
        idx = stdout.find("{", pos)
        if idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(stdout, idx)
            if isinstance(obj, dict) and obj.get("kind") == "paper-snapshot":
                snapshots.append(obj)
            pos = end
        except json.JSONDecodeError:
            pos = idx + 1
    return snapshots


# ---------------------------------------------------------------------------
# Build + run subprocess
# ---------------------------------------------------------------------------

def _parse_params(raw: dict, default_broker: str) -> dict:
    """Extract and normalise session params from the poll-paper response.

    The backend wraps the session in a "session" envelope:
      {"claimed": true, "session": {"request_id": "...", "params": {...}}}
    Fall back to reading directly from raw for SQS messages which use a
    flat structure.
    """
    session = raw.get("session") or {}
    params = session.get("params") or raw.get("params") or {}
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
    # session_date is opaque to the daemon — we just forward whatever the
    # trigger payload sent (UI date picker for "run against last Friday").
    # None means: paper_session falls back to today.
    session_date = params.get("session_date") or None
    # lookback_days extends the bar fetch backwards for warmup-hungry
    # strategies. 0 = single session only.
    try:
        lookback_days = int(params.get("lookback_days") or 0)
    except (TypeError, ValueError):
        lookback_days = 0
    # Strategy-specific minimums. ichimoku_fx_mr's reversion signal
    # needs ~2573 hourly bars before it produces non-zero output
    # (max(horizons)*4 + max(smooths) + 5 = 624*4+72+5). 200 calendar
    # days of FX 1h bars clears that. PaperLive + scheduled launchd
    # jobs don't expose a lookback knob — applying it here keeps the
    # quant strategy usable regardless of trigger source.
    if lookback_days == 0 and strategy == "ichimoku_fx_mr":
        lookback_days = 200
    return {
        "strategy": strategy,
        "symbols": symbols,
        "capital_usd": capital_usd,
        "broker": broker,
        "placement_mode": placement_mode,
        "interval": interval,
        "session_date": session_date,
        "lookback_days": lookback_days,
    }


def build_command(params: dict) -> list[str]:
    """Construct the tradepro-paper subprocess argument list."""
    # User-supplied session_date wins; otherwise default to today so a
    # missing flag from older clients still produces a runnable session.
    session_date = params.get("session_date") or date.today().isoformat()
    args = [
        sys.executable, "-m", "tradepro_strategies.cli.paper_session",
        "--broker", params["broker"],
        "--strategy", params["strategy"],
        "--symbols", ",".join(params["symbols"]),
        "--capital-usd", str(params["capital_usd"]),
        "--placement-mode", params["placement_mode"],
        "--date", session_date,  # paper_session flag is --date
        "--push",
    ]
    if params["interval"]:
        args += ["--interval", str(params["interval"])]
    if params.get("lookback_days"):
        args += ["--lookback-days", str(params["lookback_days"])]
    return args


def run_session(args: list[str], dry_run: bool) -> tuple[int, dict]:
    """Run tradepro-paper as a subprocess.

    Returns ``(exit_code, snapshot)`` where ``snapshot`` is the last
    ``paper-snapshot`` JSON block printed to stdout (or ``{}`` if none).
    Stdout / stderr are mirrored to the daemon log so existing tooling
    that watches /tmp/tradepro-paper-watch.log still works.
    """
    cmd_str = " ".join(args)
    if dry_run:
        log.info("[dry-run] would run: %s", cmd_str)
        return 0, {}

    log.info("launching paper session: %s", cmd_str)
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True)
    except OSError as exc:
        log.error("failed to launch subprocess: %s", exc)
        return 1, {}

    # Tee output to keep launchd log readable for humans.
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    log.info("paper session finished with exit_code=%d", result.returncode)
    snapshots = _extract_snapshots_from_stdout(result.stdout or "")
    snapshot = snapshots[-1] if snapshots else {}
    return result.returncode, snapshot


def _per_symbol_breakdown(strategies: list[dict]) -> list[dict]:
    """Collapse per-strategy open positions into a per-symbol view.

    Only `positions` (not fills) are exposed per-symbol because the
    snapshot's recent_fills list is empty unless paper_session is run
    with --push-fills, and fills_count is per-strategy not per-symbol.
    """
    by_symbol: dict[str, dict] = {}
    for s in strategies:
        for p in s.get("positions") or []:
            symbol = p.get("symbol")
            if not symbol:
                continue
            entry = by_symbol.setdefault(symbol, {
                "symbol": symbol,
                "quantity": 0,
                "unrealised_pnl": 0.0,
            })
            entry["quantity"] += int(p.get("quantity") or 0)
            entry["unrealised_pnl"] += float(p.get("unrealised_pnl") or 0.0)
    return list(by_symbol.values())


def _resolved_symbols(snapshot: dict) -> list[str]:
    """The set of symbols the strategy actually received bars for.

    Walks snapshot.strategies[].bars_seen because that captures every
    symbol the strategy's on_bar saw, regardless of whether it ended
    the session with a position. Falls back to position symbols (for
    older snapshots without bars_seen). Returns sorted for stable
    UI rendering.
    """
    seen: set[str] = set()
    for s in snapshot.get("strategies") or []:
        for b in s.get("bars_seen") or []:
            sym = b.get("symbol")
            if sym:
                seen.add(sym)
        for p in s.get("positions") or []:
            sym = p.get("symbol")
            if sym:
                seen.add(sym)
    return sorted(seen)


def _summarize_snapshot(snapshot: dict) -> dict:
    """Flatten a paper-snapshot into result_summary-friendly fields."""
    if not snapshot:
        return {"fills": 0, "equity": 0.0, "positions": 0, "per_symbol": []}
    strategies = snapshot.get("strategies") or []
    fills = sum(int(s.get("fills_count") or 0) for s in strategies)
    equity = sum(float(s.get("equity") or 0.0) for s in strategies)
    realised = sum(float(s.get("realised_pnl") or 0.0) for s in strategies)
    positions = sum(len(s.get("positions") or []) for s in strategies)
    return {
        "fills": fills,
        "equity": equity,
        "realised_pnl": realised,
        "positions": positions,
        "per_symbol": _per_symbol_breakdown(strategies),
        "session_label": snapshot.get("session_label"),
    }


def run_session_for_params(params: dict, dry_run: bool) -> tuple[int, dict, str]:
    """Run a single subprocess for the params.

    The bar bus multiplexes N symbols (MultiSymbolSourceBackedBus) so
    one subprocess handles the whole request. Returns exit code,
    result_summary, and command string for logging.
    """
    requested_symbols = params["symbols"] or []
    args = build_command(params)
    exit_code, snapshot = run_session(args, dry_run)
    # Prefer the resolved symbol set (what the strategy actually saw)
    # over the trigger payload's. For ichimoku_fx_mr the payload is
    # empty (the strategy expands to all G10 pairs), and displaying
    # `symbols: []` in result_summary looks like nothing ran.
    resolved = _resolved_symbols(snapshot)
    symbols = resolved or requested_symbols
    summary = {
        "strategy": params["strategy"],
        "symbols": symbols,
        "symbols_requested": requested_symbols,
        "symbols_run": len(symbols),
        **_summarize_snapshot(snapshot),
    }
    return exit_code, summary, " ".join(args)


# ---------------------------------------------------------------------------
# SQS helpers
# ---------------------------------------------------------------------------

def _sqs_client(sqs_url: str):  # noqa: ARG001
    """Return a boto3 SQS client, or None if boto3 is not installed."""
    try:
        import boto3  # noqa: PLC0415
        return boto3.client("sqs")
    except ImportError:
        log.warning("boto3 not installed — falling back to REST polling")
        return None


def receive_sqs_message(sqs_client, sqs_url: str) -> tuple[dict | None, str | None]:
    """Long-poll SQS for one trigger message.

    Returns (session_info_dict, receipt_handle) or (None, None) if no message.
    receipt_handle must be passed to delete_sqs_message() after processing.
    WaitTimeSeconds=20 keeps the connection open for up to 20 s — near-instant
    delivery, minimal API calls (1 call per 20 s when queue is empty).
    """
    try:
        resp = sqs_client.receive_message(
            QueueUrl=sqs_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        messages = resp.get("Messages", [])
        if not messages:
            return None, None
        msg = messages[0]
        receipt_handle = msg["ReceiptHandle"]
        try:
            body = json.loads(msg["Body"])
        except (json.JSONDecodeError, KeyError):
            log.warning(
                "SQS message body is not valid JSON — deleting and skipping: %s",
                msg.get("Body", "")[:200],
            )
            sqs_client.delete_message(QueueUrl=sqs_url, ReceiptHandle=receipt_handle)
            return None, None
        # params may be top-level or nested under a "params" key
        raw_params = body.get("params", body)
        request_id = body.get("request_id", f"sqs_{int(time.time())}")
        return {"request_id": request_id, "raw_params": raw_params}, receipt_handle
    except Exception as exc:  # noqa: BLE001
        log.warning("SQS receive failed: %s — will retry", exc)
        return None, None


def delete_sqs_message(sqs_client, sqs_url: str, receipt_handle: str) -> None:
    """Delete a processed SQS message (prevents re-delivery)."""
    try:
        sqs_client.delete_message(QueueUrl=sqs_url, ReceiptHandle=receipt_handle)
    except Exception as exc:  # noqa: BLE001
        log.warning("SQS delete failed (message may re-appear): %s", exc)


def sqs_loop(
    *,
    sqs_client,
    sqs_url: str,
    api_base: str,
    token: str,
    default_broker: str,
    dry_run: bool,
    once: bool,
) -> None:
    """Event-driven loop: block on SQS long-poll, run session on message."""
    log.info("SQS mode: long-polling %s (WaitTimeSeconds=20)", sqs_url)
    while True:
        session_info, receipt_handle = receive_sqs_message(sqs_client, sqs_url)
        if session_info is not None:
            request_id = session_info["request_id"]
            params = _parse_params(session_info["raw_params"], default_broker)
            log.info("SQS trigger received: request_id=%s params=%s", request_id, params)
            # Delete BEFORE running so message is not re-delivered if the
            # process crashes mid-session. The DB row is the authoritative record.
            delete_sqs_message(sqs_client, sqs_url, receipt_handle)
            exit_code, summary, cmd_str = run_session_for_params(params, dry_run)
            if exit_code == 0:
                complete_success(api_base, token, request_id, cmd_str, summary)
            else:
                complete_failure(api_base, token, request_id, exit_code)
            if once:
                return
        else:
            # No message after 20 s long-poll timeout — loop immediately.
            # SQS handles the wait internally via WaitTimeSeconds; no sleep needed.
            if once:
                log.info("SQS: no pending trigger found (--once mode, exiting)")
                return


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
    sqs_url: str = "",
) -> None:
    log.info(
        "paper-daemon starting (api=%s interval=%ds once=%s dry_run=%s sqs=%s)",
        api_base, interval, once, dry_run, sqs_url or "disabled",
    )

    # ------------------------------------------------------------------
    # SQS mode (preferred)
    # ------------------------------------------------------------------
    if sqs_url:
        client = _sqs_client(sqs_url)
        if client is not None:
            sqs_loop(
                sqs_client=client,
                sqs_url=sqs_url,
                api_base=api_base,
                token=token,
                default_broker=default_broker,
                dry_run=dry_run,
                once=once,
            )
            return
        # boto3 missing — fall through to REST polling
        log.warning(
            "TRADEPRO_PAPER_SQS_URL is set but boto3 unavailable — "
            "falling back to REST polling"
        )

    # ------------------------------------------------------------------
    # REST polling fallback (original behaviour)
    # ------------------------------------------------------------------
    while True:
        data = poll_once(api_base, token)

        if data is not None:
            # Backend wraps the session in a "session" envelope with snake_case keys.
            request_id: str = (data.get("session") or {}).get("request_id", "unknown")
            params = _parse_params(data, default_broker)
            exit_code, summary, cmd_str = run_session_for_params(params, dry_run)

            if exit_code == 0:
                complete_success(api_base, token, request_id, cmd_str, summary)
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
        "--sqs-url",
        default=None,
        metavar="URL",
        help=(
            "SQS queue URL for event-driven triggers. "
            "Overrides TRADEPRO_PAPER_SQS_URL. "
            "Requires boto3; falls back to REST polling if boto3 is missing."
        ),
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

    sqs_url = args.sqs_url or os.environ.get("TRADEPRO_PAPER_SQS_URL", "")

    daemon_loop(
        api_base=api_base,
        token=token,
        interval=args.interval,
        default_broker=args.broker,
        dry_run=args.dry_run,
        once=args.once,
        sqs_url=sqs_url,
    )


if __name__ == "__main__":
    main()
