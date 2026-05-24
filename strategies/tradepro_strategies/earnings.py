"""Family-4 signal: BEAT_AND_RETREAT post-earnings entry pattern.

The classic event-driven setup: a stock beats earnings → rallies →
pulls back 5-15% within a ~60-day window. That pullback IS the
entry signal — the market over-corrected after the rally faded but
the fundamental beat still stands. Day-5-of-60 vs day-55-of-60
matters; freshness decays.

Built on yfinance — no new API keys, no new SDKs. Earnings
announcements are public; yfinance scrapes the same Yahoo pages
the price data comes from. Network calls are wrapped so a fetch
failure produces a graceful no-signal envelope instead of breaking
the comparator.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

WINDOW_DAYS = 60
MIN_RETREAT_PCT = 5.0   # below 5% retreat → not yet a real entry
MAX_RETREAT_PCT = 15.0  # below -15% means the thesis is breaking

_log = logging.getLogger(__name__)


@dataclass
class EarningsEvent:
    """One earnings announcement with the surprise stat we care about."""

    symbol: str
    announce_date: str               # ISO date the report dropped
    eps_estimate: float | None
    eps_actual: float | None
    surprise_pct: float | None       # already a percent (+5.0 = beat by 5%)

    @property
    def beat(self) -> bool | None:
        if self.eps_actual is None or self.eps_estimate is None:
            return None
        return self.eps_actual > self.eps_estimate

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "announce_date": self.announce_date,
            "eps_estimate": self.eps_estimate,
            "eps_actual": self.eps_actual,
            "surprise_pct": self.surprise_pct,
            "beat": self.beat,
        }


def fetch_earnings_in_range(
    symbol: str,
    *,
    lookback_days: int = 1825,  # 5 years, matches the chart default
    ticker_factory=None,
) -> list[dict]:
    """Every reported earnings event for `symbol` within the lookback
    window, oldest-first. Used by the chart's earnings-marker overlay
    so the user can tell event-driven moves from trend-driven ones.

    Returns `[{"date": "YYYY-MM-DD", "surprise_pct": float | None,
    "eps_actual": float | None, "eps_estimate": float | None}, ...]`.
    Empty list on any failure mode — chart degrades to "no markers"
    cleanly instead of erroring out the row. (ETFs typically return
    empty here; only single stocks have earnings.)

    The `ticker_factory` indirection lets behave inject a fake ticker
    without hitting the network."""
    try:
        if ticker_factory is None:
            import yfinance as yf
            t = yf.Ticker(symbol)
        else:
            t = ticker_factory(symbol)
        df = t.earnings_dates
    except Exception as e:  # noqa: BLE001
        _log.warning("yfinance earnings_dates fetch failed for %s: %s", symbol, e)
        return []

    if df is None or getattr(df, "empty", True):
        return []

    # Keep only reported rows (future earnings haven't been announced
    # yet → Reported EPS is NaN; those belong to the upcoming-earnings
    # forward-calendar, not the historical chart layer).
    reported = df.dropna(subset=["Reported EPS"]).sort_index(ascending=True)
    if reported.empty:
        return []

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    out: list[dict] = []
    for ts, row in reported.iterrows():
        when_naive = ts.tz_convert("UTC").tz_localize(None) if getattr(ts, "tzinfo", None) else ts
        days_ago = (now_utc - when_naive).days
        if days_ago < 0 or days_ago > lookback_days:
            continue
        out.append({
            "date": when_naive.date().isoformat(),
            "eps_actual": _safe(row.get("Reported EPS")),
            "eps_estimate": _safe(row.get("EPS Estimate")),
            "surprise_pct": _safe(row.get("Surprise(%)")),
        })
    return out


def fetch_recent_earnings(
    symbol: str,
    *,
    lookback_days: int = 90,
    ticker_factory=None,
) -> EarningsEvent | None:
    """Most recent earnings event within the lookback window, or None.
    `ticker_factory` is the indirection point for behave to inject a
    fake ticker without hitting the network."""
    try:
        if ticker_factory is None:
            import yfinance as yf
            t = yf.Ticker(symbol)
        else:
            t = ticker_factory(symbol)
        df = t.earnings_dates
    except Exception as e:  # noqa: BLE001
        _log.warning("yfinance earnings_dates fetch failed for %s: %s", symbol, e)
        return None

    if df is None or getattr(df, "empty", True):
        return None

    # Drop rows where the company hasn't reported yet (Reported EPS is NaN)
    reported = df.dropna(subset=["Reported EPS"]).sort_index(ascending=False)
    if reported.empty:
        return None

    row = reported.iloc[0]
    when = reported.index[0]
    # Strip tz so we can subtract from a tz-naive 'now' below.
    when_naive = when.tz_convert("UTC").tz_localize(None) if when.tzinfo else when
    days_ago = (datetime.now(timezone.utc).replace(tzinfo=None) - when_naive).days
    if days_ago > lookback_days:
        return None

    return EarningsEvent(
        symbol=symbol.upper(),
        announce_date=when_naive.date().isoformat(),
        eps_estimate=_safe(row.get("EPS Estimate")),
        eps_actual=_safe(row.get("Reported EPS")),
        surprise_pct=_safe(row.get("Surprise(%)")),
    )


def beat_and_retreat_signal(
    symbol: str,
    prices: pd.DataFrame,
    *,
    window_days: int = WINDOW_DAYS,
    ticker_factory=None,
) -> dict:
    """Combines a recent earnings beat with post-earnings price action.

    Verdicts:
      STRONG       — beat AND price retreated 5-15% from post-earnings
                     peak AND we're inside the window
      MODERATE     — beat AND inside the window but retreat not yet 5%
                     OR retreat already exceeded 15%
      EXPIRED      — beat but the window has elapsed
      NO_BEAT      — earnings within lookback but missed estimates
      NO_RECENT    — no earnings within lookback
      NO_PRICES    — earnings found but post-earnings bars missing

    The envelope always carries the diagnostic fields — `fired`
    indicates whether the BUY hint should reach the user.
    """
    base = {
        "_source": f"live://earnings/{symbol.upper()}",
        "fired": False,
        "verdict": "NO_RECENT",
        "earnings": None,
        "days_since_earnings": None,
        "days_remaining_in_window": None,
        "post_earnings_peak": None,
        "current_price": None,
        "retreat_from_post_earnings_peak_pct": None,
        # Diagnostic: explains *why* the verdict isn't STRONG. Empty
        # on a successful fire. Used to be implicit — now surfaced so
        # "all stocks NO_RECENT" mysteries can be debugged from the
        # compare envelope alone instead of guessing yfinance shape.
        "diagnostic": None,
    }

    ev = fetch_recent_earnings(symbol, ticker_factory=ticker_factory)
    if ev is None:
        base["diagnostic"] = (
            "yfinance returned no recent earnings rows within the "
            "90-day lookback (Ticker.earnings_dates empty or "
            "all rows missing Reported EPS)."
        )
        return base
    base["earnings"] = ev.to_dict()
    days_since = (
        datetime.now(timezone.utc).date() - datetime.fromisoformat(ev.announce_date).date()
    ).days
    base["days_since_earnings"] = days_since
    base["days_remaining_in_window"] = max(0, window_days - days_since)

    if ev.beat is False:
        base["verdict"] = "NO_BEAT"
        base["diagnostic"] = (
            f"Earnings {days_since}d ago: actual {ev.eps_actual} ≤ "
            f"estimate {ev.eps_estimate} (missed)."
        )
        return base
    if ev.beat is None:
        # We have a date but Yahoo hasn't published the actual or
        # estimate — common right after a release before the data
        # propagates. Use a distinct verdict so the user can tell
        # this apart from "no earnings at all in lookback".
        base["verdict"] = "MISSING_DATA"
        base["diagnostic"] = (
            f"Earnings event found {days_since}d ago but Yahoo's "
            f"Reported EPS / Estimate fields are null — surprise pct "
            f"can't be computed yet. Will populate within ~24h of "
            f"the release."
        )
        return base

    # We have a beat. Slice price history to bars after the announce.
    if prices is None or prices.empty:
        base["verdict"] = "NO_PRICES"
        return base
    series = (
        prices["adj_close"] if "adj_close" in prices.columns else prices["close"]
    )
    after = series[series.index >= pd.Timestamp(ev.announce_date)]
    if after.empty or len(after) < 2:
        base["verdict"] = "NO_PRICES"
        return base
    post_peak = float(after.cummax().iloc[-1])
    current = float(after.iloc[-1])
    retreat_pct = (
        (current - post_peak) / post_peak * 100.0 if post_peak > 0 else 0.0
    )
    base["post_earnings_peak"] = post_peak
    base["current_price"] = current
    base["retreat_from_post_earnings_peak_pct"] = retreat_pct

    if base["days_remaining_in_window"] == 0:
        base["verdict"] = "EXPIRED"
        base["diagnostic"] = (
            f"Earnings beat confirmed {days_since}d ago but the "
            f"{window_days}-day post-earnings window has elapsed."
        )
        return base

    # The beat-and-retreat sweet spot: -15% ≤ retreat ≤ -5%.
    in_sweet_spot = -MAX_RETREAT_PCT <= retreat_pct <= -MIN_RETREAT_PCT
    if in_sweet_spot:
        base["verdict"] = "STRONG"
        base["fired"] = True
    else:
        base["verdict"] = "MODERATE"
        base["diagnostic"] = (
            f"Beat {days_since}d ago, retreat {retreat_pct:+.1f}% — "
            f"outside the {-MAX_RETREAT_PCT}% to {-MIN_RETREAT_PCT}% "
            f"entry zone "
            + ("(not enough pullback yet)" if retreat_pct > -MIN_RETREAT_PCT
               else "(thesis breaking — too steep)")
        )
    return base


def fetch_upcoming_earnings(
    symbol: str,
    api_base: str,
    *,
    days: int = 30,
    timeout: float = 10.0,
) -> dict | None:
    """Hit the local API's Finnhub-backed earnings calendar for the
    next N days. Returns the *next* upcoming announcement event (or
    None when nothing is scheduled / Finnhub is disabled / call
    fails). The API endpoint itself returns {enabled: false} when
    Finnhub isn't configured — we treat that as None rather than an
    error so a missing config never breaks the compare run.

    Output shape:
        {
            "date": "2026-07-29",
            "days_until": 85,
            "hour": "amc",          # "bmo" / "amc" / "" / null
            "eps_estimate": 4.22,
            "revenue_estimate": 73000000000,
        }
    """
    import requests
    from datetime import date

    url = f"{api_base.rstrip('/')}/api/integrations/finnhub/earnings-calendar"
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
    events = data.get("events") or []
    today = date.today()
    upcoming = []
    for ev in events:
        d = ev.get("date")
        if not d:
            continue
        try:
            ev_date = date.fromisoformat(d[:10])
        except ValueError:
            continue
        if ev_date < today:
            # Already reported — skip; the BEAT_AND_RETREAT path
            # already covers historical events via yfinance.
            continue
        upcoming.append((ev_date, ev))
    if not upcoming:
        return None
    upcoming.sort(key=lambda kv: kv[0])
    next_date, next_ev = upcoming[0]
    return {
        "date": next_date.isoformat(),
        "days_until": (next_date - today).days,
        "hour": next_ev.get("hour"),
        "eps_estimate": next_ev.get("epsEstimate"),
        "revenue_estimate": next_ev.get("revenueEstimate"),
        "_source": f"live://earnings_calendar/{symbol.upper()}",
    }


def earnings_trace_row(signal: dict) -> dict | None:
    """Decision-trace row representation of the earnings signal so it
    surfaces in the same Compare-expand-panel ladder as RSI / SMA /
    cross-basket. None when there's no recent earnings to discuss
    AND no upcoming earnings within the Finnhub lookahead window."""
    if not signal:
        return None
    verdict = signal.get("verdict")
    has_upcoming = (signal.get("upcoming") or {}).get("days_until") is not None
    if verdict in (None, "NO_RECENT", "NOT_APPLICABLE") and not has_upcoming:
        return None
    days_since = signal.get("days_since_earnings")
    days_left = signal.get("days_remaining_in_window")
    earnings = signal.get("earnings") or {}
    surprise = earnings.get("surprise_pct")
    retreat = signal.get("retreat_from_post_earnings_peak_pct")

    if verdict == "STRONG":
        status = "pass"
    elif verdict == "MODERATE":
        status = "warn"
    elif verdict in ("EXPIRED", "NO_BEAT", "NO_PRICES"):
        status = "fail"
    else:
        status = "warn"

    # Forward-earnings warning — when Finnhub gave us an upcoming
    # announcement within the lookahead window, escalate the trace
    # row to "warn" status because position-into-earnings volatility
    # is a real risk a long-term holder should know about.
    upcoming = signal.get("upcoming") or {}
    next_in_days = upcoming.get("days_until")
    if isinstance(next_in_days, int) and next_in_days <= 14:
        status = "warn"  # near-term reports outweigh a stale BEAT_AND_RETREAT

    bits: list[str] = []
    if surprise is not None:
        bits.append(f"beat {surprise:+.1f}%")
    if days_since is not None:
        bits.append(f"day {days_since}/{WINDOW_DAYS}")
    if days_left is not None and verdict not in ("EXPIRED", "NO_BEAT"):
        bits.append(f"{days_left}d remaining")
    if retreat is not None:
        bits.append(f"retreat {retreat:.1f}%")
    if isinstance(next_in_days, int):
        bits.append(f"NEXT EPS in {next_in_days}d")
    detail = (
        f"{verdict.lower().replace('_', ' ')} — " + ", ".join(bits)
        if bits else verdict.lower().replace("_", " ")
    )
    if verdict in (None, "NO_RECENT", "NOT_APPLICABLE") and isinstance(next_in_days, int):
        # No historic event but we DO have a forward report — render
        # the upcoming-only case cleanly instead of "no recent — NEXT in 5d"
        detail = f"reports in {next_in_days}d on {upcoming.get('date', '')}"

    return {
        "name": "Earnings beat-and-retreat",
        "status": status,
        "detail": detail,
    }


def batch_upcoming_earnings(
    symbols: list[str],
    api_base: str,
    *,
    days: int = 30,
    max_workers: int = 8,
) -> dict[str, dict | None]:
    """Fetch upcoming earnings for every symbol in `symbols` concurrently.

    Returns a dict keyed by uppercased symbol. Value is the same shape as
    `fetch_upcoming_earnings` — {date, days_until, hour, eps_estimate,
    revenue_estimate} — or None when nothing is scheduled / Finnhub is off.

    Use for nightly cron: call once with the full watchlist so the
    CATALYST detector and email digest both read from one fresh dict.

    Example:
        calendar = batch_upcoming_earnings(["MU", "NVDA", "AAPL"], api_base)
        for sym, ev in calendar.items():
            if ev and ev["days_until"] <= 14:
                print(f"{sym} reports in {ev['days_until']}d")
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, dict | None] = {}

    def _fetch_one(sym: str) -> tuple[str, dict | None]:
        return sym.upper(), fetch_upcoming_earnings(sym, api_base, days=days)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in symbols}
        for fut in as_completed(futures):
            try:
                sym, ev = fut.result()
                results[sym] = ev
            except Exception as exc:  # noqa: BLE001
                results[futures[fut].upper()] = None
                _log.warning("batch_upcoming_earnings failed for %s: %s", futures[fut], exc)

    return results


def _safe(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f
