"""Firestore-driven job worker. Runs on your Mac.

    uv run tradepro-worker

How it works:
1. Listens on the Firestore `jobs` collection for docs where status == "pending".
2. When a doc appears, marks it "running", executes the backtest/simulation,
   writes the result back into the same doc.
3. Every job produces a local artefact dir keyed by run_id (equity curve,
   trades, manifest) and a JSONL event log. The Firestore doc records the
   run_id so you can trace back to the Mac's copy.

Credentials:
    ~/.tradepro/firebase-sa.json    Firebase Admin service-account key
                                     (chmod 600; never commit).

This is a foreground process by design — you see every job in stdout, and
Ctrl-C cleanly unsubscribes. Promote to `launchd` later once it's proven.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as dtparser

from ..backtest import BacktestConfig, FeeModel, run_backtest
from ..ibkr_bars import golden_daily
from ..observability import RunLogger, git_sha
from ..strategies import resolve as resolve_strategy

CRED_PATH = Path.home() / ".tradepro" / "firebase-sa.json"


def _init_firebase():
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError as e:
        sys.exit(f"firebase-admin not installed: {e}")

    if not CRED_PATH.exists():
        sys.exit(
            f"missing credentials: {CRED_PATH}\n"
            "Copy your Firebase service-account JSON there and `chmod 600`."
        )

    if not firebase_admin._apps:
        cred = credentials.Certificate(str(CRED_PATH))
        firebase_admin.initialize_app(cred)
    return firestore.client()


def _run_backtest_job(req: dict, logger: RunLogger) -> dict:
    """Execute one backtest request. Returns dict of stats + artefact paths."""
    symbol = req["symbol"]
    provider = req.get("provider", "yahoo")
    start = dtparser.parse(req["from"])
    end = dtparser.parse(req["to"])
    strategy_name = req["strategy"]
    params = req.get("params") or {}

    logger.emit("load.start", symbol=symbol, provider=provider)
    prices = golden_daily(symbol, start, end, fetched_by="worker", legacy_provider=provider)
    logger.emit("load.done", bars=len(prices))
    if prices.empty:
        raise ValueError(f"no data for {symbol} on {provider}")

    signal_fn = resolve_strategy(strategy_name, params)
    fees = req.get("fees") or {}
    config = BacktestConfig(
        initial_capital=float(req.get("initial_capital", 10_000)),
        currency=req.get("currency", "GBP"),
        fees=FeeModel(
            commission_per_trade=float(fees.get("commission_per_trade", 0.0)),
            stamp_duty_rate=float(fees.get("stamp_duty_rate", 0.005)),
        ),
    )

    logger.emit("backtest.start")
    result = run_backtest(prices, signal_fn, config)
    logger.emit("backtest.done", trades=len(result.trades), **result.stats)

    if not result.equity_curve.empty:
        result.equity_curve.to_frame().to_parquet(logger.artefact_dir / "equity_curve.parquet")
    if not result.trades.empty:
        result.trades.to_parquet(logger.artefact_dir / "trades.parquet")

    return {"stats": result.stats, "trade_count": len(result.trades)}


# Screens the desk may trigger on demand. The VALUE is what actually runs, so
# adding a button never means re-implementing a screen — see the note in
# post_earnings_puts.build_artifact about the two that had drifted apart.
UI_SCREENS: tuple[str, ...] = ("post_earnings_puts", "options_screen")


def _run_screen_job(name: str, run_id_logger) -> dict:
    """Run a screen in-process and push its artifact.

    In-process, not a subprocess: the worker already has the environment and
    credentials, and a subprocess would need its own `uv run` resolution —
    which is exactly what broke the worker heartbeat for six days when a
    hardcoded `uv` path stopped existing.

    THIS USED TO RE-IMPLEMENT THE PUTS ARTIFACT (fixed 31 Aug 2026) and, in
    doing so, skipped `price_candidates` entirely. Pressing "Run now" therefore
    replaced a board carrying premium, yield, delta, break-even and IV with one
    carrying none of them — and dropped the rule/evidence block the UI renders
    underneath. The button made the screen worse, silently. Both paths now call
    the same builder.
    """
    if name not in UI_SCREENS:
        raise ValueError(f"unknown screen: {name!r} (known: {', '.join(UI_SCREENS)})")

    from .push_to_api import load_credentials
    import requests as _rq

    base, tok = load_credentials()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}

    if name == "post_earnings_puts":
        from .post_earnings_puts import build_artifact
        art, cands, _near = build_artifact(base, tok)
        art["triggered_by"] = "ui"
        pushed = None
        if base:
            try:
                r = _rq.post(f"{base.rstrip('/')}/api/ingest/today-setups",
                             json={"universe": "post_earnings_puts",
                                   "label": "latest", "artifact": art},
                             headers=headers, timeout=30)
                pushed = r.status_code
            except Exception as exc:  # noqa: BLE001 — a push failure must not lose the scan
                run_id_logger.emit("screen.push_failed", screen=name, error=str(exc)[:200])
        run_id_logger.emit("screen.done", screen=name, candidates=len(cands),
                           priced=art.get("priced"), pushed=pushed)
        return {"candidates": len(cands), "priced": art.get("priced"),
                "artifact": "today-setups/post_earnings_puts", "pushed_http": pushed}

    # ── the wheel / general option-candidate screen ──────────────────────
    #
    # ON DEMAND ONLY, DELIBERATELY. Its launchd agent was retired in the
    # 22 Aug desk cut (17 screens -> 7) and this does not resurrect it. The
    # owner asked to be able to run it when they want, which is what a button
    # is for.
    #
    # It pushes itself to /api/options/candidates, so there is nothing to
    # assemble here — running it IS the publish.
    from .options_screen import run_screen
    payload = run_screen() or {}
    rows = payload.get("candidates") or []
    eligible = sum(1 for r in rows if r.get("eligible"))
    # run_screen() also emails the owner, but only when the ELIGIBLE SET CHANGED
    # against the previous push — so a button press cannot spam, and a scheduled
    # run and a manual one behave identically. That equivalence is the point.
    run_id_logger.emit("screen.done", screen=name,
                       candidates=len(rows), eligible=eligible)
    return {"candidates": len(rows), "eligible": eligible,
            "market_open": payload.get("market_open"),
            "artifact": "options/candidates"}


def _execute(db, job_id: str, doc: dict) -> None:
    from firebase_admin import firestore as _fs

    run_id_logger = RunLogger()
    run_id = run_id_logger.run_id
    ref = db.collection("jobs").document(job_id)
    print(f"[{job_id}] pending → running  (run_id={run_id})")

    ref.update({
        "status": "running",
        "run_id": run_id,
        "started_at": _fs.SERVER_TIMESTAMP,
        "worker_host": os.uname().nodename,
        "worker_git_sha": git_sha(),
    })

    try:
        kind = doc.get("kind", "backtest")
        run_id_logger.emit("job.start", job_id=job_id, kind=kind, request=doc.get("request"))

        # RUN A SCREEN ON DEMAND (29 Aug 2026, owner: "ensure this can be
        # triggerd from UI as well").
        #
        # The screen runs HERE, on the Mac, because it reads the local bar
        # store — the API box cannot run it. This `jobs` collection is already
        # how the UI reaches this host, so the trigger reuses it rather than
        # adding a second mechanism. `kind` was already the dispatch point; it
        # simply only ever had one value.
        #
        # Unlike /api/screener/run this has NO market-hours guard, and must
        # not grow one: the post-earnings screen reads settled bars and an
        # earnings date, so it is equally valid at 3am on a Sunday. Gating it
        # on RTH would be copying a constraint from a screen that needs a live
        # IV feed to one that does not.
        if kind == "screen":
            name = (doc.get("request") or {}).get("screen", "post_earnings_puts")
            result = _run_screen_job(name, run_id_logger)
            ref.update({
                "status": "complete",
                "ended_at": _fs.SERVER_TIMESTAMP,
                "screen": name,
                "candidates": result.get("candidates", 0),
                "artifact": result.get("artifact"),
            })
            run_id_logger.emit("job.complete", job_id=job_id, kind=kind,
                               screen=name, candidates=result.get("candidates"))
            print(f"[{job_id}] running → complete  ({name}: "
                  f"{result.get('candidates')} candidate(s))")
            return

        if kind != "backtest":
            raise ValueError(f"unsupported job kind: {kind}")

        result = _run_backtest_job(doc["request"], run_id_logger)
        manifest = run_id_logger.write_manifest(inputs=doc["request"], stats=result["stats"])

        ref.update({
            "status": "complete",
            "ended_at": _fs.SERVER_TIMESTAMP,
            "stats": result["stats"],
            "trade_count": result["trade_count"],
            "manifest": manifest,
        })
        run_id_logger.emit("job.complete")
        print(f"[{job_id}] complete  return={result['stats'].get('total_return_pct', 0):.2f}%  trades={result['trade_count']}")

    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        run_id_logger.emit("job.failed", error=str(e), traceback=tb)
        ref.update({
            "status": "failed",
            "ended_at": _fs.SERVER_TIMESTAMP,
            "error": str(e),
            "traceback": tb,
        })
        print(f"[{job_id}] FAILED: {e}", file=sys.stderr)


def main() -> None:
    db = _init_firebase()
    from firebase_admin import firestore as _fs

    host = os.uname().nodename
    print(f"tradepro-worker on {host}")
    print(f"watching Firestore collection 'jobs' for status=pending …")
    print("(Ctrl-C to stop)\n")

    stop = {"flag": False}

    def on_snapshot(_col, changes, _read_time):
        for change in changes:
            if change.type.name != "ADDED":
                continue
            doc = change.document
            data = doc.to_dict() or {}
            if data.get("status") != "pending":
                continue
            try:
                _execute(db, doc.id, data)
            except Exception as e:  # noqa: BLE001
                print(f"[{doc.id}] worker exception: {e}", file=sys.stderr)

    query = db.collection("jobs").where(
        filter=_fs.FieldFilter("status", "==", "pending")
    )
    watch = query.on_snapshot(on_snapshot)

    def handle(*_):
        stop["flag"] = True
        print("\nshutting down…")
        try:
            watch.unsubscribe()
        except Exception:
            pass

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    while not stop["flag"]:
        time.sleep(1)


if __name__ == "__main__":
    main()
