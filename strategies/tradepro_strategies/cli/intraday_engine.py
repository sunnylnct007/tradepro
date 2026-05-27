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
from datetime import datetime, timedelta, timezone
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
        "minRiskRewardRatio": 1.5,
        "maxSpreadPct": 0.3,
        "minConfidence": 0.70,
    },
    "autoPlaceConfidenceThreshold": 0.72,
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

        # `bypass_window` is the on-demand override the UI's "Scan now"
        # button sets. Scheduled intraday ticks (no params override)
        # still respect sessionStartUtc/sessionEndUtc so the live
        # watchlist doesn't fire off-hours; explicit user-triggered
        # scans run whenever the trader clicks the button.
        bypass_window = bool(params.get("bypass_window"))
        if not bypass_window and not _inside_window(
                cfg["sessionStartUtc"], cfg["sessionEndUtc"]):
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


def _effective_session_date(now: datetime, bypass_window: bool) -> datetime:
    """Pick the session_date the engine should fetch bars for.

    - In-window scheduled ticks (bypass_window=False, default) → ``now``
      verbatim. The engine is supposed to be running live during US
      market hours; yfinance has data and we want fresh bars.

    - On-demand /scan triggered off-hours (bypass_window=True) →
      roll back to the most recent completed trading day. We treat
      "trading day" as Mon–Fri after 13:30 UTC (≈ US market open);
      anything earlier in the day rolls back further. Holidays are
      tolerated by the existing holiday-aware-lookback path in
      sources/base.py — if the chosen day was a US holiday, the
      source falls back further.

    Without this, off-hours /scan returned 0 bars (yfinance 1m
    intraday is empty before US open) → strategy never fired →
    session completed instantly with 0 decisions.
    """
    if not bypass_window:
        return now
    # Roll back to the most recent weekday whose US session has ended.
    # 13:30 UTC = 09:30 ET (US open); if it's before that, the prior
    # day's 1m data is what's available. After 20:00 UTC (US close)
    # today's data is in the cache.
    candidate = now
    minutes_now = candidate.hour * 60 + candidate.minute
    if candidate.weekday() < 5 and minutes_now >= 20 * 60:
        # After US close — today is a complete session.
        return candidate
    # Walk back one day at a time until we land on Mon–Fri.
    while True:
        candidate = candidate - timedelta(days=1)
        if candidate.weekday() < 5:
            return candidate


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

    # /scan trigger sends `strategy` (singular) + `params` at the top
    # level — that's the trader explicitly saying "run THIS strategy
    # on this universe". Honour it: build a single-entry strategies
    # block from the singular field so the engine actually runs the
    # named strategy (and not the textbook default fan-out which
    # excludes ichimoku_equity — the trader's quant Ichimoku). Without
    # this, /scan ichimoku_equity silently runs orb + vwap +
    # bollinger + ma_crossover instead.
    explicit = cfg.get("strategy")
    if isinstance(explicit, str) and explicit.strip():
        name = explicit.strip()
        if name in available:
            params = cfg.get("params")
            return {name: dict(params) if isinstance(params, dict) else {}}
        log.warning("explicit strategy %r is not in the registry; falling back", name)

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
    # Off-hours /scan path: yfinance returns 0 bars for "today" 1m
    # before US market open, so the bus delivers nothing and the
    # strategy never fires. When bypass_window is set AND we're
    # outside the US session window (13:30..20:00 UTC), roll
    # session_date back to the last completed trading day so the
    # cache + yfinance can serve real bars. Scheduled intraday ticks
    # (bypass_window=false, by definition inside the window) keep
    # using "now" as their session_date.
    session_date = _effective_session_date(started_at, bool(cfg.get("bypass_window")))
    lookback_days = int(cfg.get("lookback_days") or 1)
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
            session_date=session_date,
            interval="1m",
            pace_seconds=None,
            lookback_days=lookback_days,
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

        # Use the effective session_date (rolled back off-hours), NOT
        # started_at. on_session_start receives this and the strategy
        # uses it to fetch its daily history slice. Passing "now"
        # off-hours would request a session that has no live bars and
        # the strategy would see an empty signal universe.
        #
        # Engine.run() returns a snapshot with decisions/bars/charts
        # already attached via attach_*. We re-snapshot with
        # include_fills=20 to surface recent fills (run() emits with
        # include_fills=0), then RE-APPLY the attach_* hooks because
        # Ledger.to_snapshot only knows strategy_ids and can't reach
        # back into the Strategy instances.
        #
        # The previous code looked up `snap["books"]` (a key that does
        # not exist — Ledger.to_snapshot returns `{strategies: [...]}`)
        # and silently zeroed every per-strategy entry: no decisions,
        # no bars, no charts, no fills, no positions, no P&L. Same
        # anti-pattern that bit paper_session.py until 719963c.
        asyncio.run(engine.run(session_date))
        snap = engine.ledger.to_snapshot(include_fills=20)
        engine.attach_decisions(snap)
        engine.attach_bars(snap)
        engine.attach_charts(snap)
        by_sid: dict[str, dict] = {
            entry.get("strategy_id"): entry
            for entry in (snap.get("strategies") or [])
            if isinstance(entry, dict) and entry.get("strategy_id")
        }

        per_strategy = []
        for name, sid in strategies_registered:
            entry = by_sid.get(sid) or {}
            per_strategy.append({
                "strategy": name,
                "strategy_id": sid,
                "fills": entry.get("fills_count", 0),
                "recent_fills": entry.get("recent_fills") or [],
                "open_positions": len(entry.get("positions") or []),
                "positions": entry.get("positions") or [],
                "realized_pnl_usd": entry.get("realised_pnl"),
                "unrealized_pnl_usd": entry.get("unrealised_pnl"),
                "equity": entry.get("equity"),
                "decisions": entry.get("decisions") or [],
                "bars_seen": entry.get("bars_seen") or [],
                "charts": entry.get("charts") or {},
            })

        # data_window_start = earliest date that contributed bars (from the
        # holiday-aware lookback). None when lookback_days=0 or pre-market.
        dws = getattr(bus, "data_window_start", None)
        return {
            "symbol": symbol,
            "ok": True,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "strategies": per_strategy,
            "register_errors": register_errors,
            "data_window_start": dws.date().isoformat() if dws else None,
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
