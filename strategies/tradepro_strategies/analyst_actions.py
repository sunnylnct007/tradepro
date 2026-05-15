"""Analyst rating actions (upgrade / downgrade / initiate / reiterate).

New signal family that augments the strategy-consensus + sentiment
layers with a "what are the sell-side desks saying lately?" read.
Hits the local API's Finnhub-backed
/api/integrations/finnhub/upgrades endpoint per symbol — same pattern
as earnings.fetch_upcoming_earnings. Empty / disabled cleanly when
the FINNHUB_API_KEY env isn't set on the API box.

Surfaced on the compare row as `analyst_actions`:

    {
        "window_days":      30,
        "event_count":      5,
        "upgrade_count":    3,
        "downgrade_count":  1,
        "init_count":       1,
        "net_delta":        2,           # upgrades - downgrades
        "most_recent":      {            # the freshest event
            "date":      "2026-05-08",
            "from":      "Hold",
            "to":        "Buy",
            "company":   "Goldman Sachs",
            "action":    "up",
        },
        "events":           [...]        # up to 6 raw events for the
                                         # expand panel's mini-table
    }

Pure best-effort: any HTTP failure / disabled integration / parse
error returns None and the compare run continues with no analyst
context. Never raises.
"""
from __future__ import annotations

from datetime import datetime, timezone


def fetch_analyst_actions(
    symbol: str,
    api_base: str,
    *,
    days: int = 30,
    timeout: float = 10.0,
) -> dict | None:
    import requests

    url = f"{api_base.rstrip('/')}/api/integrations/finnhub/upgrades"
    try:
        resp = requests.get(
            url,
            params={"symbol": symbol, "days": days},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except requests.RequestException:
        return None

    if not data.get("enabled"):
        return None

    events_raw = data.get("events") or []
    if not events_raw:
        return {
            "window_days": days,
            "event_count": 0,
            "upgrade_count": 0,
            "downgrade_count": 0,
            "init_count": 0,
            "net_delta": 0,
            "most_recent": None,
            "events": [],
        }

    # Finnhub returns gradeTime as a unix-epoch second. Convert to
    # ISO date so the UI can render without epoch math.
    def _to_iso(epoch_sec) -> str | None:
        if epoch_sec is None:
            return None
        try:
            return (
                datetime.fromtimestamp(int(epoch_sec), tz=timezone.utc)
                .date().isoformat()
            )
        except (TypeError, ValueError, OverflowError):
            return None

    normalised = []
    for ev in events_raw:
        normalised.append({
            "date":    _to_iso(ev.get("gradeTime")),
            "from":    ev.get("fromGrade") or "",
            "to":      ev.get("toGrade") or "",
            "company": ev.get("company") or "",
            "action":  (ev.get("action") or "").lower(),
        })
    # Sort newest-first so most_recent is at index 0 and the row's
    # events[] slice naturally shows the freshest first.
    normalised.sort(key=lambda e: e["date"] or "", reverse=True)
    most_recent = normalised[0] if normalised else None

    return {
        "window_days": days,
        "event_count": data.get("eventCount", len(normalised)),
        "upgrade_count": data.get("upgradeCount", 0),
        "downgrade_count": data.get("downgradeCount", 0),
        "init_count": data.get("initCount", 0),
        "net_delta": data.get("netDelta", 0),
        "most_recent": most_recent,
        "events": normalised[:6],
    }


def fetch_analyst_recommendations(
    symbol: str,
    api_base: str,
    *,
    timeout: float = 10.0,
) -> dict | None:
    """Monthly buy/hold/sell counts from Finnhub's free
    /stock/recommendation endpoint. Cheap fallback for symbols where
    per-event upgrade/downgrade data is gated (free tier returns
    empty for /stock/upgrade-downgrade).

    Returns:
        {
            "latest_period":     "2026-04-01",
            "strong_buy":        14,
            "buy":               24,
            "hold":              7,
            "sell":              0,
            "strong_sell":       0,
            "bull_score":        38,   # (sb+buy) - (sell+ssell)
            "mom_change":        +3,   # change vs prior period
            "periods":           [...up to 12 monthly snapshots]
        }

    None when Finnhub is disabled / call fails / no data."""
    import requests

    url = f"{api_base.rstrip('/')}/api/integrations/finnhub/recommendations"
    try:
        resp = requests.get(url, params={"symbol": symbol}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json() or {}
    except requests.RequestException:
        return None
    if not data.get("enabled"):
        return None
    if data.get("periodCount", 0) == 0:
        return None
    return {
        "latest_period": data.get("latestPeriod"),
        "strong_buy": data.get("latestStrongBuy", 0),
        "buy": data.get("latestBuy", 0),
        "hold": data.get("latestHold", 0),
        "sell": data.get("latestSell", 0),
        "strong_sell": data.get("latestStrongSell", 0),
        "bull_score": data.get("bullScoreLatest", 0),
        "mom_change": data.get("momChange", 0),
        "periods": data.get("periods", []),
    }
