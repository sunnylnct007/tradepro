"""Sector stress — is a stock's sector (or the market) in a sell-off right now?

SELLOFF_PLAYBOOK_GATES_V1 (graded 10 Nov 2026, 4,143 trades, 2006–2026) found
ONE thing worth shipping, and it is a negative: a 10%-OTM 21-session put sold
while the stock's sector ETF sits ≥ 8% below its 20-session high expires
worthless 86% of the time, against 96% on a quiet day. One in seven assigned
versus one in twenty-five. That is the state the live book was sold into in
November, and nothing on any screen said so.

So the sell-off is NAMED on every short-option row the same way a dated event
is: through the `events` block (events.py), as kind `sector_selloff`, with the
number in the title. It is not a gate — OWNER BASICS 3 applies: weighed, not
auto-rejected, the owner decides.

HOW IT IS COMPUTED, AND WHERE. The ETF bars live in the settled daily store on
the Mac. The lanes that render rows (options screen on the Mac, puts board on
Lambda, live-risk lane on the Mac, MCP on the box) do not all have that store,
so this module computes ONCE per day after the harvest — `tradepro-sector-stress
--push` — and publishes a `sector-stress` today-setups artifact; `events.py`
reads the artifact. A missing or stale artifact is a NAMED gap in every events
window, never "no stress".

Definitions are the study's, frozen: drawdown from the rolling 20-session high
(including today), stressed at ≤ −8%. The GICS→SPDR map is the study's too; a
test pins the two copies together.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger("tradepro.sector_stress")

#: GICS sector → SPDR sector ETF. Same table as backtests/studies/selloff_playbook_v1.py.
SECTOR_ETF: dict[str, str] = {
    "Materials": "XLB", "Communication Services": "XLC", "Energy": "XLE",
    "Financials": "XLF", "Industrials": "XLI", "Information Technology": "XLK",
    "Consumer Staples": "XLP", "Real Estate": "XLRE", "Utilities": "XLU",
    "Health Care": "XLV", "Consumer Discretionary": "XLY",
}
MARKET_ETFS = ("SPY", "QQQ")
HIGH_WIN = 20
STRESS_DD = -0.08
ARTIFACT_UNIVERSE = "sector-stress"
#: Study numbers quoted on the row — S2 of SELLOFF_PLAYBOOK_GATES_V1.
S2_STRESSED_PCT, S2_QUIET_PCT = 86.0, 96.1
MAX_AGE_DAYS = 4   # an artifact older than this (a long weekend) is stale

_UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "universe" / "tradeable.json"


def sector_of_universe(path: Path | None = None) -> dict[str, str]:
    """symbol → sector ETF for every tradeable.json row that carries a GICS sector."""
    raw = json.loads((path or _UNIVERSE_PATH).read_text())
    rows = raw.get("symbols", []) if isinstance(raw, dict) else raw
    out: dict[str, str] = {}
    for r in rows:
        if isinstance(r, dict) and r.get("sector") in SECTOR_ETF and r.get("symbol"):
            out[str(r["symbol"]).upper()] = SECTOR_ETF[r["sector"]]
    return out


def drawdown_from_high(closes, window: int = HIGH_WIN) -> float | None:
    """Last close vs the max close of the last `window` sessions (inclusive)."""
    c = [float(x) for x in closes if x is not None]
    if len(c) < 2:
        return None
    hi = max(c[-window:])
    return c[-1] / hi - 1.0 if hi > 0 else None


def etf_state(symbol: str, closes, dates) -> dict:
    dd = drawdown_from_high(closes)
    c = [float(x) for x in closes]
    return {
        "symbol": symbol,
        "close": c[-1] if c else None,
        "high_20": max(c[-HIGH_WIN:]) if c else None,
        "dd_pct": round(dd * 100, 2) if dd is not None else None,
        "stressed": (dd is not None and dd <= STRESS_DD),
        "bars_as_of": str(dates[-1])[:10] if len(dates) else None,
    }


def build(load=None, *, today: date | None = None) -> dict:
    """The artifact. `load(symbol) -> (closes, dates) | None` is injectable."""
    if load is None:
        from .daily_bars import load_daily

        def load(sym):  # noqa: E306
            df = load_daily(sym)
            if df is None or len(df) < HIGH_WIN:
                return None
            return df["close"].tolist(), [str(x)[:10] for x in df.index]

    etfs: dict[str, dict] = {}
    missing: list[str] = []
    for e in list(dict.fromkeys(list(SECTOR_ETF.values()) + list(MARKET_ETFS))):
        got = load(e)
        if not got:
            missing.append(e)
            continue
        etfs[e] = etf_state(e, *got)
    stressed = sorted(e for e, s in etfs.items() if s["stressed"])
    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_date": (today or date.today()).isoformat(),
        "rule": {"window_sessions": HIGH_WIN, "stress_dd_pct": STRESS_DD * 100,
                 "source": "SELLOFF_PLAYBOOK_GATES_V1 §S2",
                 "s2_expire_worthless_pct": {"stressed": S2_STRESSED_PCT, "quiet": S2_QUIET_PCT}},
        "etfs": etfs,
        "missing_etfs": missing,
        "stressed": stressed,
        "sector_of": sector_of_universe(),
    }


def artifact_is_fresh(art: dict | None, *, today: date | None = None) -> bool:
    if not art:
        return False
    try:
        d = date.fromisoformat(str(art.get("as_of_date"))[:10])
    except (TypeError, ValueError):
        return False
    return ((today or date.today()) - d).days <= MAX_AGE_DAYS


def stress_events_for(symbol: str, art: dict | None, *, today: date | None = None) -> tuple[list[dict], str | None]:
    """(events, gap). Events are plain dicts in events.Event field order so
    events.py can wrap them without importing this module's internals.
    gap is a sentence when the artifact cannot be trusted; never silent."""
    if not artifact_is_fresh(art, today=today):
        return [], ("sector stress not published or stale — run `tradepro-sector-stress --push` "
                    "after the daily harvest")
    assert art is not None
    out: list[dict] = []
    etfs = art.get("etfs") or {}
    on = str(art.get("as_of_date"))[:10]

    def _ev(e: str, scope: str) -> dict | None:
        s = etfs.get(e)
        if not s or not s.get("stressed"):
            return None
        return {
            "kind": "sector_selloff", "on": on, "scope": scope,
            "source": "selloff_playbook_v1", "severity": "high",
            "title": (f"{e} is {abs(s['dd_pct']):.1f}% below its 20-day high — a 10%-OTM put "
                      f"sold here expired worthless {S2_STRESSED_PCT:.0f}% of the time vs "
                      f"{S2_QUIET_PCT:.0f}% on a quiet day (SELLOFF_PLAYBOOK_GATES_V1 S2)"),
            "etf": e, "dd_pct": s["dd_pct"],
        }

    sec = (art.get("sector_of") or {}).get(symbol.upper())
    if sec:
        ev = _ev(sec, sec)
        if ev:
            out.append(ev)
    # Market ETFs keep their own ticker as scope so the row reads "QQQ sector
    # sell‑off", not an anonymous "sector sell‑off"; the title says the rest.
    for m in MARKET_ETFS:
        ev = _ev(m, m)
        if ev:
            out.append(ev)
    return out, None


__all__ = ["SECTOR_ETF", "MARKET_ETFS", "ARTIFACT_UNIVERSE", "HIGH_WIN", "STRESS_DD",
           "build", "drawdown_from_high", "etf_state", "sector_of_universe",
           "artifact_is_fresh", "stress_events_for"]
