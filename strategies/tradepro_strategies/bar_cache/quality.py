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
# ibkr_web = the OAuth Web API path via the central backend endpoint (Option B) —
# same broker-GOOD data as ibkr (Gateway), without the Gateway's session hangs.
_TRUSTED_PROVIDERS = {"ibkr", "ibkr_web", "ig"}


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


# ─────────────────────────────────────────────────────────────────────────
# FETCH tiers — the OTHER grading vocabulary, now owned here too.
#
# There are two, they answer different questions, and until 2026-08-23 they
# lived in different modules with nothing tying them together:
#
#   SymbolQuality (above)  "is this symbol's data good enough to DECIDE on
#                          today?"  → GOOD / BRONZE / PARTIAL / STALE / MISSING.
#                          Freshness is the point; STALE is its whole reason to
#                          exist.
#
#   fetch_tier (below)     "what did THIS fetch produce, and where did the bars
#                          actually come from?" → gold / silver / bronze /
#                          missing. Provenance is the point; it says nothing
#                          about staleness.
#
# Both are legitimate — a fetch can be gold (complete, IBKR) on a symbol whose
# series is STALE, because the harvest window asked for nothing recent. Merging
# them would lose that. But they share the word BRONZE with nearly-but-not-quite
# the same meaning, and one reader seeing "GOLD" in a harvest log and "PARTIAL"
# on the data screen for the same symbol had no way to reconcile them. Keeping
# both definitions in this file is what makes any future drift visible in a
# diff instead of six months later.
#
# The lowercase strings are load-bearing: TradePro.Api's DataReadinessEndpoints
# parses the "🥇 N GOLD  🥈 N SILVER …" summary line out of the harvest log.
# Renaming these to match SymbolQuality's vocabulary would break that endpoint,
# which is why they are documented as distinct rather than unified.
FETCH_TIERS = ("gold", "silver", "bronze", "missing")

_GOLDEN_BAR_SOURCES = frozenset({"ibkr", "ibkr_web", "g3"})


def fetch_tier(provider_used: str | None, complete: bool,
               df: "object | None" = None) -> str:
    """Provider + completeness → fetch tier.

    ``provider_used`` is the store's chain outcome — ``"<provider>_ok"`` (e.g.
    ``ibkr_web_ok``) or ``"cache"`` — NOT a bare provider name.

    Two bugs shaped this, both the same class one level apart:

    * Until 2026-08-09 it compared the suffixed value against ``"ibkr"``, so
      every IBKR fetch displayed bronze and the summary under-reported IBKR.
    * 2026-08-16: ``"cache"`` was graded bronze outright, and on a steady-state
      run almost every symbol is a cache hit — so the summary read "0 gold" and
      the data-readiness endpoint reported "0 from IBKR, 172 from the yfinance
      fallback", a false statement about the data. "Which provider answered THIS
      call" is not "where the data came from"; the stored ``source`` column is
      the only thing that knows, so grade on it whenever a frame is available.
    """
    if not provider_used or provider_used == "none":
        return "missing"
    p = (provider_used or "").lower().removesuffix("_ok")
    if p in ("ibkr", "ibkr_web"):
        return "gold" if complete else "silver"
    if p == "cache" and df is not None and not getattr(df, "empty", True) \
            and "source" in getattr(df, "columns", []):
        srcs = df["source"].dropna()
        if len(srcs):
            golden = int(srcs.isin(list(_GOLDEN_BAR_SOURCES)).sum())
            # Majority rule: a partition that is mostly IBKR is IBKR-grade. A
            # mixed one is reported by its dominant source; the exact split
            # stays visible in the row provenance (ibkr_bars.bars_provenance).
            if golden * 2 >= len(srcs):
                return "gold" if complete else "silver"
        return "bronze"
    # ig / yfinance, or cache with no frame to inspect.
    return "bronze"


def fetch_tier_icon(tier: str) -> str:
    return {"gold": "🥇", "silver": "🥈", "bronze": "🥉", "missing": "✗ "}.get(tier, "?")
