"""Continuous-mode intraday engine — Task #69 step D.

Long-running CLI that the Mac launchd keeps alive. On each tick it:

  1. POSTs ``/api/ops/poll-intraday`` (IngestToken bearer). The server
     atomically flips one Pending session_request to Claimed and
     returns the payload — or returns ``{claimed: false}`` when the
     queue is empty.

  2. When a request is claimed, merges its ``params`` with the live
     ``AppSettings.intraday`` block (UI-editable, fetched per-claim
     via ``api_settings.get_settings``). Settings are the source of
     truth; ``params`` only override fields the operator wants to
     pin for that specific session.

  3. Checks the session window (``sessionStartUtc..sessionEndUtc``).
     Outside the window → mark Completed with a "skipped" summary
     instead of running anything. The poll cadence keeps the Mac
     alive but the engine itself stays idle until the user's
     configured market hours.

  4. Inside the window, walks the watchlist and runs one ``Engine``
     session per symbol against the ``t212/manual`` profile —
     yfinance bars in, T212 demo router with placement_mode=manual
     out. Manual mode means every order intent queues as Pending
     instead of touching T212; Step F replaces this with the auto-
     vs-pending router driven by the configured confidence
     threshold.

  5. POSTs ``/api/ops/complete-intraday/{request_id}`` with a per-
     symbol summary (orders emitted, fills, error message). Any
     uncaught exception during a per-symbol run is captured into
     that symbol's slot rather than failing the whole claim.

Cadence + safety knobs (env, all optional):

  - ``TRADEPRO_INTRADAY_POLL_SECONDS`` — idle poll interval (default 30s).
  - ``TRADEPRO_INTRADAY_HOST`` — identifier returned in the claim
    payload so the API knows which worker owns the row (default:
    ``socket.gethostname``).
  - ``~/.tradepro/intraday-engine.pause`` — touch to skip polls
    without unloading launchd; rm to resume.

Run-once mode for manual smoke-tests:

  ``tradepro-intraday-engine --once`` — single poll, run if claimed,
  then exit. Same code path as the loop, useful in CI / local QA.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("tradepro.intraday")

DEFAULT_POLL_SECONDS = 30
PAUSE_FILE = Path.home() / ".tradepro" / "intraday-engine.pause"

# Compiled defaults — used when the API returns no intraday block
# (fresh install, settings file truncated) so the engine still has a
# coherent shape to merge against. Match the Settings.tsx defaults.
_DEFAULT_INTRADAY: dict[str, Any] = {
    "symbols": [],
    "scanIntervalMinutes": 1,
    "sessionStartUtc": "13:30",
    "sessionEndUtc": "20:00",
    "gate": {
        "minRiskRewardRatio": 2.0,
        "maxSpreadPct": 0.3,
        "minConfidence": 0.70,
    },
    "autoPlaceConfidenceThreshold": 0.85,
    "riskPerTradeUsd": 100.0,
}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="tradepro-intraday-engine")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit instead of looping. Useful for smoke tests.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=int(os.environ.get("TRADEPRO_INTRADAY_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
        help="Idle interval between polls when nothing is claimed (default 30s).",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    from . import push_to_api
    base, token = push_to_api.load_credentials()
    host = os.environ.get("TRADEPRO_INTRADAY_HOST") or socket.gethostname()

    log.info("intraday-engine starting (host=%s, poll=%ss, once=%s)",
             host, args.poll_seconds, args.once)

    # Graceful shutdown — launchd's bootout sends SIGTERM; we want to
    # finish the current claim (if any) before exiting so we don't
    # leave session_requests in Claimed-but-stranded state.
    stop = {"requested": False}

    def _handle(signo, _frame):  # noqa: ARG001
        log.info("signal %s received — finishing current cycle then exiting", signo)
        stop["requested"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    while True:
        if PAUSE_FILE.exists():
            log.info("paused (touch %s — rm to resume)", PAUSE_FILE)
        else:
            try:
                _cycle(base, token, host)
            except Exception as e:  # noqa: BLE001 — keep loop alive on transient errors
                log.exception("cycle failed: %s", e)

        if args.once or stop["requested"]:
            break
        time.sleep(args.poll_seconds)

    log.info("intraday-engine exiting cleanly")
    return 0


def _cycle(base_url: str, token: str, host: str) -> None:
    """One poll → maybe-claim → maybe-run → maybe-complete pass."""
    claim = _poll(base_url, token, host)
    if not claim:
        log.debug("no claim")
        return

    request_id = claim["request_id"]
    params = claim.get("params") or {}
    log.info("claimed request %s — params=%s", request_id, json.dumps(params)[:200])

    try:
        cfg = _resolve_config(params)
        symbols = list(cfg.get("symbols") or [])
        if not symbols:
            _complete(base_url, token, request_id, "completed", {
                "skipped": "watchlist empty — nothing to scan",
                "config": cfg,
            })
            return

        if not _inside_window(cfg["sessionStartUtc"], cfg["sessionEndUtc"]):
            _complete(base_url, token, request_id, "completed", {
                "skipped": (
                    f"outside session window "
                    f"{cfg['sessionStartUtc']}..{cfg['sessionEndUtc']} UTC"
                ),
                "current_utc": datetime.now(timezone.utc).strftime("%H:%M"),
                "config": cfg,
            })
            return

        per_symbol = []
        for symbol in symbols:
            per_symbol.append(_run_one_symbol(symbol, cfg))

        _complete(base_url, token, request_id, "completed", {
            "host": host,
            "config": cfg,
            "symbols_scanned": len(symbols),
            "results": per_symbol,
        })
    except Exception as e:  # noqa: BLE001 — surface to the API rather than crash the loop
        log.exception("run failed for %s", request_id)
        _complete(base_url, token, request_id, "failed", None, error=str(e))


def _poll(base_url: str, token: str, host: str) -> dict | None:
    url = f"{base_url.rstrip('/')}/api/ops/poll-intraday"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"host": host},
            timeout=8.0,
        )
    except requests.RequestException as e:
        log.warning("poll failed: %s", e)
        return None
    if resp.status_code != 200:
        log.warning("poll non-200: %s %s", resp.status_code, resp.text[:200])
        return None
    body = resp.json()
    if not body.get("claimed"):
        return None
    return body.get("session") or None


def _complete(base_url: str, token: str, request_id: str, status: str,
              result_summary: dict | None, *, error: str | None = None) -> None:
    url = f"{base_url.rstrip('/')}/api/ops/complete-intraday/{request_id}"
    payload: dict[str, Any] = {"status": status}
    if result_summary is not None:
        payload["result_summary"] = result_summary
    if error is not None:
        payload["error"] = error
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10.0,
        )
    except requests.RequestException as e:
        log.error("complete-post failed for %s: %s", request_id, e)
        return
    if resp.status_code != 200:
        log.error("complete-post non-200 for %s: %s %s",
                  request_id, resp.status_code, resp.text[:200])
        return
    log.info("posted complete for %s (status=%s)", request_id, status)


def _resolve_config(params: dict) -> dict[str, Any]:
    """Merge claim params over AppSettings.intraday over compiled defaults.

    Params from the claim payload win — they're the operator's intent
    for *this* session. Settings cover the steady-state config. Compiled
    defaults are the last-resort floor so a brand-new install with no
    settings row still has a coherent shape."""
    merged = json.loads(json.dumps(_DEFAULT_INTRADAY))  # deep copy
    try:
        from ..api_settings import get_settings
        api = get_settings(force_refresh=True)
        intraday = (api or {}).get("intraday") if isinstance(api, dict) else None
        if isinstance(intraday, dict):
            _deep_merge(merged, intraday)
    except Exception as e:  # noqa: BLE001
        log.warning("settings fetch failed (using defaults): %s", e)
    if isinstance(params, dict):
        _deep_merge(merged, params)
    return merged


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def _inside_window(start_hhmm: str, end_hhmm: str) -> bool:
    now = datetime.now(timezone.utc)
    try:
        s_h, s_m = (int(x) for x in start_hhmm.split(":"))
        e_h, e_m = (int(x) for x in end_hhmm.split(":"))
    except (ValueError, AttributeError):
        # Bad HH:mm in config — fail open (we still try to run) rather
        # than silently never firing. Operator notices via the result
        # summary including the malformed value.
        return True
    minutes_now = now.hour * 60 + now.minute
    minutes_start = s_h * 60 + s_m
    minutes_end = e_h * 60 + e_m
    if minutes_start <= minutes_end:
        return minutes_start <= minutes_now <= minutes_end
    # Window wraps midnight (start > end). Rare for US/EU hours but
    # cheap to support — treat as "inside" if now ≥ start OR now ≤ end.
    return minutes_now >= minutes_start or minutes_now <= minutes_end


# Fallback list — every strategy registered in
# tradepro_strategies.paper.strategies. Used when settings has no
# per-strategy block yet (fresh install) so the engine still runs
# the full menu. The "orb" alias is filtered out at runtime to
# avoid registering the same class twice under different ids.
_INTRADAY_DEFAULT_STRATEGY_NAMES: tuple[str, ...] = (
    "orb",
    "vwap_mean_reversion",
    "bollinger_bounce",
    "ma_crossover",
)


def _resolve_enabled_strategies(cfg: dict) -> dict[str, dict]:
    """Return ``{name: params_override}`` for every strategy the
    engine should run on this scan.

    Source of truth:
      1. ``cfg["strategies"]`` (Intraday settings block, merged with
         claim params upstream). Each entry can set ``enabled: bool``
         and ``params: dict`` (param overrides merged into the
         strategy's ``default_params()``).
      2. If the settings block is empty / missing, fall back to the
         compiled default list (everything in
         ``_INTRADAY_DEFAULT_STRATEGY_NAMES``).

    Any name in the registry that is NOT in settings defaults to
    enabled=True with no param overrides. Matches the
    "run-everything-then-narrow" preference: a freshly registered
    strategy shows up in the engine as soon as it's wired into the
    registry, without needing the user to toggle it on first.
    """
    from ..paper.strategies import available as _registry_available
    available = set(_registry_available())
    # Drop the back-compat 'opening_range_breakout' alias if both it
    # and 'orb' resolve to the same class — the engine would otherwise
    # double-register.
    if "opening_range_breakout" in available and "orb" in available:
        available.discard("opening_range_breakout")

    settings_block = cfg.get("strategies")
    if not isinstance(settings_block, dict) or not settings_block:
        return {name: {} for name in _INTRADAY_DEFAULT_STRATEGY_NAMES
                if name in available}

    enabled: dict[str, dict] = {}
    for name in available:
        entry = settings_block.get(name)
        if isinstance(entry, dict):
            if entry.get("enabled", True):
                params = entry.get("params") or {}
                enabled[name] = dict(params) if isinstance(params, dict) else {}
        else:
            # Not in settings yet → run by default
            enabled[name] = {}
    return enabled


def _run_one_symbol(symbol: str, cfg: dict) -> dict:
    """Run one yfinance-bus session for one symbol against every
    enabled intraday strategy. Returns a per-symbol result blob with
    one nested entry per strategy so the leaderboard can roll
    cumulative P&L up by (symbol, strategy).

    All strategies share the same bus + router (one BarBus stream per
    session, one router per session), but each gets its own ledger
    book by virtue of a unique strategy_id. Per-strategy fills, open
    positions and realised P&L come straight out of the ledger
    snapshot.

    Manual placement mode means every order intent queues as Pending
    in the API's pending_orders table — nothing reaches T212 here.
    Step F will swap this for the auto-vs-pending router driven by
    ``autoPlaceConfidenceThreshold``."""
    from ..paper import RiskLimits
    from ..paper.engine import Engine
    from ..paper.profiles import build_session
    from ..paper.strategies import build as build_strategy

    started_at = datetime.now(timezone.utc)
    risk_per_trade = float(cfg.get("riskPerTradeUsd", 100.0))
    enabled = _resolve_enabled_strategies(cfg)
    log.info("running session for %s across %d strategies (%s)",
             symbol, len(enabled), ",".join(sorted(enabled.keys())) or "none")

    if not enabled:
        return {
            "symbol": symbol,
            "ok": False,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": (
                "no strategies enabled — flip at least one ON in "
                "Settings → Intraday → Strategies, or remove the "
                "per-strategy block to revert to the default fan-out"
            ),
        }

    try:
        bus, router = build_session(
            broker="t212",
            symbols=[symbol],
            session_date=started_at,
            interval="1m",
            pace_seconds=None,
            t212_mode="demo",
            t212_allow_real_orders=False,
            t212_placement_mode="manual",
        )
        engine = Engine(bus=bus, router=router)

        strategies_registered: list[tuple[str, str]] = []  # (name, strategy_id)
        register_errors: list[dict] = []
        for name, overrides in sorted(enabled.items()):
            sid = f"intraday-{name}-{symbol}"
            params: dict = {"risk_per_trade_usd": risk_per_trade}
            params.update(overrides)
            try:
                strat = build_strategy(name, strategy_id=sid, params=params)
                strat.risk = RiskLimits(
                    max_position_value_usd=5_000.0, allow_short=False,
                )
                engine.register_strategy(strat, symbols=[symbol])
                strategies_registered.append((name, sid))
            except Exception as e:  # noqa: BLE001 — one bad strategy shouldn't kill the run
                log.warning("strategy %s for %s failed to register: %s", name, symbol, e)
                register_errors.append({"strategy": name, "error": str(e)})

        if not strategies_registered:
            return {
                "symbol": symbol,
                "ok": False,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "no strategies registered",
                "register_errors": register_errors,
            }

        asyncio.run(engine.run(started_at))
        snap = engine.ledger.to_snapshot(include_fills=True)
        books = snap.get("books") or {}

        per_strategy = []
        for name, sid in strategies_registered:
            book = books.get(sid) or {}
            per_strategy.append({
                "strategy": name,
                "strategy_id": sid,
                "fills": len(book.get("fills") or []),
                "open_positions": len(book.get("positions") or []),
                "realized_pnl_usd": book.get("realized_pnl_usd"),
                "unrealized_pnl_usd": book.get("unrealized_pnl_usd"),
            })

        return {
            "symbol": symbol,
            "ok": True,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "strategies": per_strategy,
            "register_errors": register_errors,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("symbol %s failed", symbol)
        return {
            "symbol": symbol,
            "ok": False,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }


if __name__ == "__main__":
    sys.exit(main())
