"""Per-symbol bar-data QUALITY SCORE — "is this data good enough to decide on TODAY?"

One honest verdict per symbol, rolled from the bar_cache_health fields, so a
minimal-staff operator can SEE at a glance whether today's decisions rest on
good data — and so the verdict pipeline can REFUSE a confident BUY on a symbol
whose data is stale/missing (no false positives: stale data ≠ "all clear").

Score tiers (worst wins):
  MISSING  — no coverage at all → NOT good for today.
  STALE    — last bar is > `stale_after_days` behind → NOT good for today
             ("pending N days"). The single most dangerous case: a confident
             decision on data that stopped updating days ago.
  PARTIAL  — covered + fresh but with internal gaps (missing_days) → usable,
             flagged; not good for a high-conviction call.
  BRONZE   — fresh + complete but yfinance-sourced (not IBKR) → usable for a
             first pass, NOT the credible-backtest bar (Yahoo divergence seen).
  GOOD     — fresh + complete + IBKR/IG. Good for today's decision.

`good_for_today` is True only for GOOD and BRONZE (fresh + complete); the UI can
still warn on BRONZE. STALE/PARTIAL/MISSING are False — and feed the verdict's
dataGaps ("bars" check could-not-compute) so conviction is capped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

# Calendar-day staleness allowance. 4 covers a normal weekend (Fri→Mon) plus a
# day's slack; a US long weekend (e.g. Juneteenth Fri) is handled by the caller
# passing a trading-calendar-aware `as_of`/`last_session` when it has one.
DEFAULT_STALE_AFTER_DAYS = 4
# yfinance = bronze (Yahoo-vs-IBKR divergence + garbage bars seen); IBKR/IG ok.
_TRUSTED_PROVIDERS = {"ibkr", "ig"}


@dataclass(frozen=True)
class SymbolQuality:
    canonical: str
    score: str            # GOOD | BRONZE | PARTIAL | STALE | MISSING
    good_for_today: bool
    days_behind: Optional[int]
    reason: str


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def score_symbol(
    *,
    canonical: str,
    coverage_end,          # last covered session date (str/date/datetime/None)
    provider: Optional[str],
    missing_days_count: Optional[int],
    as_of: date,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> SymbolQuality:
    """Pure roll-up of one symbol's health into a single decision-grade score.
    No I/O — the caller fetches bar_cache_health rows and hands them here."""
    end = _to_date(coverage_end)
    if end is None:
        return SymbolQuality(canonical, "MISSING", False, None,
                             "No cached bars — cannot decide on this symbol today.")
    days_behind = (as_of - end).days
    if days_behind > stale_after_days:
        return SymbolQuality(
            canonical, "STALE", False, days_behind,
            f"Last bar {days_behind}d old (> {stale_after_days}d) — data pending; "
            f"NOT good for today's decision.")
    prov = (provider or "").strip().lower()
    miss = int(missing_days_count or 0)
    if miss > 0:
        return SymbolQuality(
            canonical, "PARTIAL", False, days_behind,
            f"Fresh but {miss} session(s) missing inside coverage — gaps; "
            f"usable but not high-conviction.")
    if prov not in _TRUSTED_PROVIDERS:
        return SymbolQuality(
            canonical, "BRONZE", True, days_behind,
            f"Fresh + complete but {prov or 'unknown'}-sourced (not IBKR) — "
            f"ok for a first pass, not the credible-backtest bar.")
    return SymbolQuality(
        canonical, "GOOD", True, days_behind,
        f"Fresh ({days_behind}d) + complete + {prov.upper()} — good for today.")
