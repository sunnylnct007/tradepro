"""EPS revision tracker — weekly snapshots of forward EPS per symbol.

Alpha factor: earnings revision momentum.  Analysts raising estimates → prices
follow.  Analysts cutting → leading indicator of disappointment.

Mechanism:
  1. `record_snapshot(symbol)` — reads yfinance `Ticker.info` for forwardEps
     and appends a timestamped row to ~/.tradepro/eps_snapshots/<SYMBOL>.json.
     Meant to be called weekly (Sunday evening before the new week opens).

  2. `get_eps_revision(symbol)` — loads the snapshot history and returns the
     90-day delta: how much has the forward EPS estimate moved in 3 months?
     Returns a structured dict so COMPASS and the email digest can both consume
     it without re-fetching.

Storage: one JSON file per symbol at ~/.tradepro/eps_snapshots/.
Phase 2: migrate to Postgres time-series table (same schema).

Why yfinance forwardEps?
  - Free, already a dependency, no extra API key.
  - `forwardEps` is the consensus next-12-months EPS.  It lags Refinitiv/FactSet
    by ~24h but is directionally accurate for weekly-cadence tracking.
  - For stocks that don't have analyst coverage (ETFs, micro-caps), the field
    is None — handled gracefully as "no revision data".
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_SNAPSHOT_DIR = Path(os.environ.get("TRADEPRO_EPS_DIR", Path.home() / ".tradepro" / "eps_snapshots"))
_LOOKBACK_DAYS = 90


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_snapshot(symbol: str, *, ticker_factory=None) -> dict | None:
    """Fetch the current forwardEps for `symbol` and persist it.

    Returns the snapshot dict on success, None on failure (e.g. ETF with no
    analyst coverage).  `ticker_factory` is the test-injection seam.
    """
    sym = symbol.upper()
    eps = _fetch_forward_eps(sym, ticker_factory=ticker_factory)
    if eps is None:
        _log.debug("%s: no forwardEps available — skipping snapshot", sym)
        return None

    snapshot = {
        "symbol": sym,
        "date": date.today().isoformat(),
        "forward_eps": eps,
    }
    _append_snapshot(sym, snapshot)
    _log.info("%s: recorded forwardEps=%.4f on %s", sym, eps, snapshot["date"])
    return snapshot


def get_eps_revision(symbol: str) -> dict:
    """Load the snapshot history for `symbol` and compute the 90-day revision.

    Returns:
        {
            "symbol": "MU",
            "current_estimate": 19.88,
            "estimate_90d_ago": 15.10,
            "delta_90d": 4.78,
            "direction": "up",       # "up" | "down" | "flat" | "insufficient_data"
            "revision_pct": 31.6,    # percent change in the estimate
            "snapshots_count": 13,
            "as_of": "2026-05-23",
        }
    """
    sym = symbol.upper()
    snapshots = _load_snapshots(sym)
    today = date.today()
    cutoff = today - timedelta(days=_LOOKBACK_DAYS)

    if not snapshots:
        return _no_data(sym, "no snapshots recorded yet")

    # Most recent snapshot = current estimate
    latest = max(snapshots, key=lambda s: s["date"])
    current = latest.get("forward_eps")
    if current is None:
        return _no_data(sym, "latest snapshot has no forward_eps")

    # Find closest snapshot to 90 days ago (within ±7d window)
    candidates = [
        s for s in snapshots
        if abs((_parse_date(s["date"]) - cutoff).days) <= 7
        and _parse_date(s["date"]) <= today
    ]
    if not candidates:
        # Fall back: oldest snapshot available if it's at least 30 days old
        old_candidates = [
            s for s in snapshots
            if (_parse_date(s["date"]) - today).days <= -30
        ]
        if not old_candidates:
            return {
                "symbol": sym,
                "current_estimate": current,
                "estimate_90d_ago": None,
                "delta_90d": None,
                "direction": "insufficient_data",
                "revision_pct": None,
                "snapshots_count": len(snapshots),
                "as_of": latest["date"],
            }
        candidates = old_candidates

    baseline = max(candidates, key=lambda s: s["date"])
    past = baseline.get("forward_eps")
    if past is None or past == 0:
        return _no_data(sym, "baseline snapshot has no forward_eps")

    delta = current - past
    rev_pct = (delta / abs(past)) * 100.0
    if abs(delta) < 0.01:
        direction = "flat"
    elif delta > 0:
        direction = "up"
    else:
        direction = "down"

    return {
        "symbol": sym,
        "current_estimate": current,
        "estimate_90d_ago": past,
        "delta_90d": round(delta, 4),
        "direction": direction,
        "revision_pct": round(rev_pct, 2),
        "snapshots_count": len(snapshots),
        "as_of": latest["date"],
    }


def batch_record_snapshots(symbols: list[str], *, max_workers: int = 8) -> dict[str, dict | None]:
    """Record snapshots for a list of symbols concurrently.  Call weekly."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(record_snapshot, s): s.upper() for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[sym] = None
                _log.warning("snapshot failed for %s: %s", sym, exc)
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_forward_eps(symbol: str, *, ticker_factory=None) -> float | None:
    try:
        if ticker_factory is not None:
            t = ticker_factory(symbol)
        else:
            import yfinance as yf
            t = yf.Ticker(symbol)
        info: dict[str, Any] = t.info or {}
        val = info.get("forwardEps")
        if val is None:
            return None
        f = float(val)
        return f if not (f != f) else None  # NaN guard
    except Exception as exc:  # noqa: BLE001
        _log.debug("forwardEps fetch failed for %s: %s", symbol, exc)
        return None


def _snapshot_path(symbol: str) -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return _SNAPSHOT_DIR / f"{symbol.upper()}.json"


def _load_snapshots(symbol: str) -> list[dict]:
    p = _snapshot_path(symbol)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        _log.warning("corrupt EPS snapshot file %s: %s", p, exc)
        return []


def _append_snapshot(symbol: str, snapshot: dict) -> None:
    existing = _load_snapshots(symbol)
    # Deduplicate by date — overwrite same-day entry
    existing = [s for s in existing if s.get("date") != snapshot["date"]]
    existing.append(snapshot)
    # Keep only last 2 years of snapshots
    existing.sort(key=lambda s: s["date"])
    existing = existing[-104:]  # ~2 years of weekly snapshots
    _snapshot_path(symbol).write_text(json.dumps(existing, indent=2))


def _parse_date(d: str) -> date:
    return date.fromisoformat(d)


def _no_data(symbol: str, reason: str) -> dict:
    return {
        "symbol": symbol,
        "current_estimate": None,
        "estimate_90d_ago": None,
        "delta_90d": None,
        "direction": "insufficient_data",
        "revision_pct": None,
        "snapshots_count": 0,
        "as_of": None,
        "_reason": reason,
    }


__all__ = [
    "record_snapshot",
    "get_eps_revision",
    "batch_record_snapshots",
]
