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
import uuid
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


def poll_backtest_once(api_base: str, token: str) -> dict | None:
    """POST /api/ops/poll-backtest — sibling of poll-paper for quant
    backtests. Same envelope shape: ``{claimed: true, session: {...}}``.

    Mac runs both polls each tick so a single launchd agent services
    paper sessions AND on-demand backtests without needing a second
    daemon process. Backtest network calls (yfinance for SPY +
    sleeve symbols) can take 5-30 s — that's fine, polling stays on
    its 60 s cadence regardless.
    """
    url = f"{api_base}/api/ops/poll-backtest"
    try:
        resp = requests.post(url, headers=_headers(token), json={}, timeout=30)
        resp.raise_for_status()
        data: dict = resp.json()
    except requests.RequestException as exc:
        log.warning("poll-backtest request failed (%s): %s", url, exc)
        return None

    if not data.get("claimed"):
        log.debug("poll-backtest: nothing pending")
        return None

    log.info("poll-backtest: claimed request_id=%s",
             (data.get("session") or {}).get("request_id"))
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


# ---------------------------------------------------------------------------
# OMS audit-trail push (Phase 1d)
# ---------------------------------------------------------------------------

def _post_oms_fills_from_snapshot(
    api_base: str,
    token: str,
    snapshot: dict,
    strategy_name: str,
    broker: str,
) -> int:
    """For each fill in the snapshot, write an audit row to /api/oms.

    Best-effort: a single fill failing doesn't abort the rest, and the
    overall function never raises. Returns the count of fills posted so
    the daemon log shows traffic.

    For each fill we EnqueueAsync (PENDING_APPROVAL), Approve
    (SUBMITTED), and RecordFill (FILLED) in three calls. The OMS
    transitions are atomic per-call; in Phase 2 these collapse into
    real-time interception before the broker so the order is in OMS
    BEFORE it fills, not after.
    """
    if not snapshot:
        return 0
    strategies = snapshot.get("strategies") or []
    headers = {**_headers(token)}
    base_url = f"{api_base.rstrip('/')}/api/oms"
    posted = 0
    for entry in strategies:
        sid = entry.get("strategy_id") or strategy_name
        for fill in entry.get("recent_fills") or []:
            try:
                # 1) Enqueue intent. ClientOrderId derived from fill's
                # order_id so retries are idempotent (the OMS unique
                # index dedupes). Falls back to UUID when the fill
                # snapshot didn't include an order_id.
                client_id = fill.get("order_id") or str(uuid.uuid4())
                # OMS expects a UUID-typed ClientOrderId; if order_id
                # isn't UUID-shaped, hash it into one deterministically.
                try:
                    client_uuid = str(uuid.UUID(client_id))
                except Exception:
                    client_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, client_id))

                intent = {
                    "ClientOrderId": client_uuid,
                    "Broker": _broker_to_oms_label(broker),
                    "Symbol": fill.get("symbol") or "",
                    "Side": (fill.get("side") or "").upper(),
                    "Qty": float(fill.get("quantity") or 0),
                    "OrderType": "MKT",
                    "StrategyId": sid,
                    "PlacedBy": "STRATEGY_AUTO",
                }
                if intent["Qty"] <= 0:
                    continue
                r = requests.post(f"{base_url}/orders", json=intent, headers=headers, timeout=15)
                r.raise_for_status()
                order_id = r.json().get("id")
                if not order_id:
                    continue

                # 2) Auto-approve so the FILL transition is valid (Approve
                # requires PENDING_APPROVAL → SUBMITTED).
                r = requests.post(
                    f"{base_url}/orders/{order_id}/approve",
                    headers=headers, timeout=15,
                )
                if r.status_code == 409:
                    # Already approved on a previous retry — fine.
                    pass
                else:
                    r.raise_for_status()

                # 3) Record the fill itself.
                fill_payload = {
                    "qty": float(fill.get("quantity") or 0),
                    "price": float(fill.get("fill_price") or 0),
                    "fee": float(fill.get("commission") or 0),
                    "currency": "USD",
                    "brokerFillId": str(fill.get("order_id") or ""),
                    "actor": "daemon",
                }
                r = requests.post(
                    f"{base_url}/orders/{order_id}/fill",
                    json=fill_payload, headers=headers, timeout=15,
                )
                # POST /fill endpoint doesn't exist yet (Phase 2). Until
                # then, OMS records the order intent + approval; fills
                # arrive via the snapshot's recent_fills surfaced
                # separately. Treat 404 as benign.
                if r.status_code not in (200, 404):
                    r.raise_for_status()
                posted += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("OMS post failed for %s/%s: %s",
                            sid, fill.get("symbol"), exc)
    if posted:
        log.info("OMS: posted %d order(s) from snapshot", posted)
    return posted


def _broker_to_oms_label(broker: str) -> str:
    """Map the daemon's broker string to the OMS CHECK enum value."""
    mapping = {
        "t212": "T212_DEMO",
        "yfinance": "PAPER",
        "replay": "PAPER",
        "ibkr": "IBKR_PAPER",
    }
    return mapping.get(broker.lower(), "PAPER")


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


def _complete_backtest(
    api_base: str, token: str, request_id: str, payload: dict,
) -> None:
    """POST /api/ops/complete-backtest/{id} — sibling of /complete-paper."""
    url = f"{api_base}/api/ops/complete-backtest/{request_id}"
    try:
        resp = requests.post(url, headers=_headers(token), json=payload, timeout=30)
        resp.raise_for_status()
        log.info("complete-backtest OK: request_id=%s status=%s",
                 request_id, payload.get("status"))
    except requests.RequestException as exc:
        log.warning("complete-backtest failed for %s: %s", request_id, exc)


def complete_backtest_success(
    api_base: str, token: str, request_id: str, summary: dict,
) -> None:
    _complete_backtest(api_base, token, request_id, {
        "status": "completed", "result_summary": summary,
    })


def complete_backtest_failure(
    api_base: str, token: str, request_id: str, error: str,
) -> None:
    _complete_backtest(api_base, token, request_id, {
        "status": "failed", "error": error,
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
    # Honour the strategy's declared default_lookback_days when the
    # trigger payload didn't supply one. Pulled from the registry so
    # we don't hardcode per-strategy knowledge in the daemon — when a
    # trader ships a new strategy with `default_lookback_days = N`,
    # the daemon picks it up automatically. Import lazily so the
    # daemon's startup cost stays low; registry has internal caching.
    if lookback_days == 0:
        try:
            from ..paper import registry as _registry
            import tradepro_strategies.paper.strategies  # noqa: F401  triggers registration
            spec = _registry.get(strategy)
            lookback_days = int(getattr(spec.cls, "default_lookback_days", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not resolve default_lookback_days for %s: %s", strategy, exc)
            lookback_days = 0
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


def run_session_for_params(
    params: dict,
    dry_run: bool,
    *,
    api_base: str | None = None,
    token: str | None = None,
) -> tuple[int, dict, str]:
    """Run a single subprocess for the params.

    The bar bus multiplexes N symbols (MultiSymbolSourceBackedBus) so
    one subprocess handles the whole request. Returns exit code,
    result_summary, and command string for logging.

    `api_base` + `token` enable the OMS audit push (Phase 1d): after
    the session completes, every fill in the snapshot is replayed
    into /api/oms/orders so the OMS UI sees the full trade history.
    Best-effort — OMS being unreachable does NOT fail the session.
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
    # Embed the per-strategy snapshot blocks so the Session Detail
    # page's Bars / Decisions / Fills / Positions tabs render — they
    # read result_summary.strategies[].{bars_seen,decisions,recent_fills,
    # positions} directly. Bounded by Strategy.bar_buffer_size (300)
    # and decision_buffer_size (50) per symbol so the JSONB blob stays
    # manageable (~20-50 KB per session).
    summary = {
        "strategy": params["strategy"],
        "symbols": symbols,
        "symbols_requested": requested_symbols,
        "symbols_run": len(symbols),
        "strategies": snapshot.get("strategies") or [],
        **_summarize_snapshot(snapshot),
    }
    # OMS audit push (Phase 1d). Phase 2 will intercept BEFORE the
    # broker call so OMS sees PENDING_APPROVAL state before fills land.
    if api_base and token and exit_code == 0:
        try:
            posted = _post_oms_fills_from_snapshot(
                api_base, token, snapshot, params["strategy"], params["broker"],
            )
            if posted:
                summary["oms_orders_posted"] = posted
        except Exception as exc:  # noqa: BLE001
            log.warning("OMS audit push failed (continuing): %s", exc)
    return exit_code, summary, " ".join(args)


# ---------------------------------------------------------------------------
# Backtest dispatch
# ---------------------------------------------------------------------------

# Markers emitted by ``tradepro-quant-backtest`` around the
# result_summary JSON block so the daemon can extract a clean dict
# from stdout regardless of yfinance / plotly noise.
_BT_BEGIN = "BEGIN_QUANT_BACKTEST_RESULT"
_BT_END = "END_QUANT_BACKTEST_RESULT"


def _extract_backtest_result(stdout: str) -> dict | None:
    """Parse the marker-delimited result_summary from CLI stdout.

    Returns the parsed dict, or None if either marker is missing /
    the embedded payload is not valid JSON. Last-occurrence wins (so
    a retry inside the same subprocess still surfaces the final
    summary, though today we only run one).
    """
    if not stdout:
        return None
    begin = stdout.rfind(_BT_BEGIN)
    end = stdout.rfind(_BT_END)
    if begin < 0 or end < 0 or end <= begin:
        return None
    raw = stdout[begin + len(_BT_BEGIN): end].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("backtest result JSON decode failed: %s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def build_backtest_command(request_id: str, payload: dict) -> list[str]:
    """Construct the tradepro-quant-backtest subprocess argument list.

    Payload is passed inline via --payload so the daemon doesn't have
    to write a temp file. Safe even for fat payloads — argv has a
    generous limit (~256 KB on macOS) and our payloads are <2 KB
    (lists of symbols + a couple of ints).
    """
    return [
        sys.executable, "-m", "tradepro_strategies.cli.quant_backtest",
        "--request-id", request_id,
        "--payload", json.dumps(payload),
    ]


def run_backtest_for_params(
    request_id: str, payload: dict, dry_run: bool,
) -> tuple[int, dict | None, str]:
    """Run tradepro-quant-backtest as a subprocess and parse its result.

    Returns ``(exit_code, result_summary, command_string)``. On failure
    the result_summary is None and the caller should post completion
    as 'failed' with the exit code as the error code.
    """
    args = build_backtest_command(request_id, payload)
    cmd_str = " ".join(args[:5]) + " --payload <" + str(len(args[-1])) + " bytes>"

    if dry_run:
        log.info("[dry-run] would run: %s", cmd_str)
        return 0, None, cmd_str

    log.info("launching quant backtest: %s", cmd_str)
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True)
    except OSError as exc:
        log.error("failed to launch quant-backtest subprocess: %s", exc)
        return 1, None, cmd_str

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    log.info("quant backtest finished with exit_code=%d", result.returncode)
    summary = _extract_backtest_result(result.stdout or "")
    return result.returncode, summary, cmd_str


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
            exit_code, summary, cmd_str = run_session_for_params(
                params, dry_run, api_base=api_base, token=token,
            )
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
            exit_code, summary, cmd_str = run_session_for_params(
                params, dry_run, api_base=api_base, token=token,
            )

            if exit_code == 0:
                complete_success(api_base, token, request_id, cmd_str, summary)
            else:
                complete_failure(api_base, token, request_id, exit_code)

        # Drain one quant-backtest request per tick too. Additive —
        # if the API doesn't yet expose /poll-backtest, the network
        # call logs a warning and returns None, which is harmless.
        bt = poll_backtest_once(api_base, token)
        if bt is not None:
            session = bt.get("session") or {}
            bt_request_id: str = session.get("request_id", "unknown")
            bt_payload: dict = session.get("params") or {}
            bt_exit, bt_summary, _ = run_backtest_for_params(
                bt_request_id, bt_payload, dry_run,
            )
            if bt_exit == 0 and bt_summary is not None:
                complete_backtest_success(api_base, token, bt_request_id, bt_summary)
            else:
                complete_backtest_failure(
                    api_base, token, bt_request_id,
                    f"exit_code={bt_exit}; result_summary missing"
                    if bt_summary is None
                    else f"exit_code={bt_exit}",
                )

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
        default="yfinance",
        help=(
            "Default broker when the trigger request omits one. "
            "yfinance = PaperOrderRouter (sim fills locally, no API key needed). "
            "t212 = real T212 demo (needs TRADEPRO_T212_API_KEY; otherwise "
            "every order is rejected and you see fills=0)."
        ),
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
