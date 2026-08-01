"""Earnings-proximity gate — make earnings a first-class veto/penalty input.

The swing scanner reads earnings-driven price action as ordinary Ichimoku
geometry: a stock that gapped on a report gets scored as a clean "support hold
at the kijun", because the engine has no awareness a catalyst just fired. This
gate classifies each candidate by earnings proximity and routes it to
veto / penalty+flag / clear, and fails LOUD when the earnings feed is unreliable
rather than silently treating a missing date as "all clear".

Two things it fixes:
  • gap #4 — no earnings/catalyst awareness in the signal.
  • the ATR-contamination mislabel: an earnings gap inflates ATR(14) for ~14
    sessions, so a genuine post-earnings decline reads as "0.1 ATR above the
    kijun, support hold". This gate MUST run BEFORE any ATR-based calc so veto
    names never reach the ATR stage (see §2 of the spec / ordering).

All windows are TRADING SESSIONS, never calendar days (an exchange calendar,
XNYS, is used to count them — 3 sessions after a Wednesday report skips the
weekend). classify() is pure (sessions in, state out) so the spec fixtures pin
it; the date→session counting lives in the helpers below.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger("tradepro.gates.earnings_proximity")


class EarningsGate(Enum):
    CLEAR = "CLEAR"
    PRE_BLACKOUT = "PRE_BLACKOUT"
    POST_DIGEST = "POST_DIGEST"
    POST_DRIFT = "POST_DRIFT"
    UNKNOWN = "UNKNOWN"


VETO = frozenset({EarningsGate.PRE_BLACKOUT, EarningsGate.POST_DIGEST})
PENALIZE = frozenset({EarningsGate.POST_DRIFT, EarningsGate.UNKNOWN})


@dataclass(frozen=True)
class EarningsGateConfig:
    """Thresholds — all in TRADING SESSIONS. Config, not code (overridable via
    TRADEPRO_EARNINGS_* env). Defaults are the spec's, with the §12 calls we
    signed off: DIGEST_VOLUME_OVERRIDE ships OFF, drift is a rank-cap + score
    multiplier."""
    pre_blackout_sessions: int = 5      # veto if next report within this many sessions
    post_digest_sessions: int = 3       # veto for this many sessions after a report
    post_drift_sessions: int = 10       # penalise + flag out to this many sessions after
    drift_score_mult: float = 0.5       # composite-score multiplier in the drift window
    estimated_date_buffer: int = 2      # extra pre-sessions when the date is a provider estimate
    digest_volume_override: bool = False  # OFF: a firing volume score can't buy a name out of digest veto
    unknown_score_mult: float = 0.5     # UNKNOWN is penalised like drift (can't verify → cap)

    @staticmethod
    def from_env() -> "EarningsGateConfig":
        def _i(k: str, d: int) -> int:
            try: return int(os.environ.get(k, d))
            except (TypeError, ValueError): return d
        def _f(k: str, d: float) -> float:
            try: return float(os.environ.get(k, d))
            except (TypeError, ValueError): return d
        def _b(k: str, d: bool) -> bool:
            v = os.environ.get(k)
            return d if v is None else v.strip().lower() in ("1", "true", "yes", "on")
        return EarningsGateConfig(
            pre_blackout_sessions=_i("TRADEPRO_EARNINGS_PRE_BLACKOUT_SESSIONS", 5),
            post_digest_sessions=_i("TRADEPRO_EARNINGS_POST_DIGEST_SESSIONS", 3),
            post_drift_sessions=_i("TRADEPRO_EARNINGS_POST_DRIFT_SESSIONS", 10),
            drift_score_mult=_f("TRADEPRO_EARNINGS_DRIFT_SCORE_MULT", 0.5),
            estimated_date_buffer=_i("TRADEPRO_EARNINGS_ESTIMATED_DATE_BUFFER", 2),
            digest_volume_override=_b("TRADEPRO_EARNINGS_DIGEST_VOLUME_OVERRIDE", False),
            unknown_score_mult=_f("TRADEPRO_EARNINGS_UNKNOWN_SCORE_MULT", 0.5),
        )


def classify(
    sessions_to_next: int | None,
    sessions_since_last: int | None,
    date_is_estimated: bool,
    cfg: EarningsGateConfig,
    *,
    has_earnings: bool = True,
) -> EarningsGate:
    """Five mutually-exclusive states, evaluated in THIS exact order (first
    match wins). A missing date NEVER falls through to CLEAR — it is UNKNOWN
    (penalise + flag), so a stale feed can't launder earnings names into
    "pristine" setups.

    `has_earnings=False` (an ETF / non-equity with no earnings concept) maps to
    CLEAR — distinct from "feed failed", which is UNKNOWN.
    """
    # A symbol that genuinely has no earnings (ETF/future) is CLEAR, not UNKNOWN.
    if not has_earnings:
        return EarningsGate.CLEAR
    # 1. UNKNOWN — both dates missing → penalise + flag + raise the canary.
    if sessions_to_next is None and sessions_since_last is None:
        return EarningsGate.UNKNOWN
    # 2. PRE_BLACKOUT — next report imminent (wider window if the date is an estimate).
    pre = cfg.pre_blackout_sessions + (cfg.estimated_date_buffer if date_is_estimated else 0)
    if sessions_to_next is not None and 0 <= sessions_to_next <= pre:
        return EarningsGate.PRE_BLACKOUT
    # 3. POST_DIGEST — just reported; ATR still contaminated → veto.
    if sessions_since_last is not None and 0 <= sessions_since_last <= cfg.post_digest_sessions:
        return EarningsGate.POST_DIGEST
    # 4. POST_DRIFT — still digesting; survive but penalise + flag.
    if sessions_since_last is not None and 0 <= sessions_since_last <= cfg.post_drift_sessions:
        return EarningsGate.POST_DRIFT
    return EarningsGate.CLEAR


@dataclass(frozen=True)
class GateDecision:
    state: EarningsGate
    action: str          # "veto" | "penalize" | "clear"
    flag: str | None     # "EARNINGS_DRIFT" | "EARNINGS_UNKNOWN" | None
    score_mult: float     # composite multiplier (1.0 = unchanged)
    rank_cap: bool        # True → cannot be a top-N / ⭐ pick
    reason: str


def route(state: EarningsGate, cfg: EarningsGateConfig, *,
          sessions_to_next: int | None = None,
          sessions_since_last: int | None = None) -> GateDecision:
    """Map a state to (action, flag, score effect). Veto = dropped before
    scoring/sizing; penalise = survives with a flag + score/rank penalty."""
    if state == EarningsGate.PRE_BLACKOUT:
        return GateDecision(state, "veto", None, 0.0, True,
                            f"earnings in {sessions_to_next} session(s) — pre-report blackout, "
                            f"a binary print can gap it through the stop.")
    if state == EarningsGate.POST_DIGEST:
        return GateDecision(state, "veto", None, 0.0, True,
                            f"reported {sessions_since_last} session(s) ago — still digesting; "
                            f"ATR is gap-contaminated, technicals unreliable.")
    if state == EarningsGate.POST_DRIFT:
        return GateDecision(state, "penalize", "EARNINGS_DRIFT", cfg.drift_score_mult, True,
                            f"reported {sessions_since_last} session(s) ago — post-earnings drift; "
                            f"ATR mildly inflated, score/rank penalised.")
    if state == EarningsGate.UNKNOWN:
        return GateDecision(state, "penalize", "EARNINGS_UNKNOWN", cfg.unknown_score_mult, True,
                            "earnings date unavailable — cannot verify proximity; penalised (never "
                            "scored CLEAR on a missing date).")
    return GateDecision(state, "clear", None, 1.0, False, "no earnings within the windows.")


# ── date → trading-session counting (XNYS) ───────────────────────────────────

_CAL = None


def _cal():
    global _CAL
    if _CAL is None:
        import exchange_calendars as xcals
        _CAL = xcals.get_calendar("XNYS")
    return _CAL


def _to_date(d) -> _dt.date | None:
    if d is None:
        return None
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d
    try:
        return _dt.date.fromisoformat(str(d)[:10])
    except (ValueError, TypeError):
        return None


def sessions_between(past, future) -> int | None:
    """Trading sessions from `past` (exclusive) to `future` (inclusive), signed.
    Positive when future is after past. None if either date can't be parsed.
    Uses the XNYS calendar so weekends/holidays are skipped correctly."""
    a, b = _to_date(past), _to_date(future)
    if a is None or b is None:
        return None
    if a == b:
        return 0
    sign = 1 if a < b else -1
    lo, hi = (a, b) if a < b else (b, a)
    try:
        idx = _cal().sessions_in_range(lo.isoformat(), hi.isoformat())
    except Exception as exc:  # noqa: BLE001
        log.debug("session count failed (%s→%s): %s", lo, hi, exc)
        return None
    n = len(idx)
    # We want sessions strictly AFTER `lo` up to and including `hi`; drop `lo`
    # itself if it was a session (it's the anchor, session 0).
    if n and idx[0].date() == lo:
        n -= 1
    return sign * n


def sessions_since(last_report, *, today=None) -> int | None:
    """Trading sessions since the last report (0 = reported today)."""
    return sessions_between(last_report, today or _dt.date.today())


def sessions_to(next_report, *, today=None) -> int | None:
    """Trading sessions until the next report (0 = reports today)."""
    return sessions_between(today or _dt.date.today(), next_report)


# ── stale-feed canary ─────────────────────────────────────────────────────────

# Guaranteed quarterly reporters — mega-caps whose report dates sit on every
# public calendar weeks ahead. If the feed returns NO date for one of THESE,
# the FEED is degraded, not the name (the MA case: reported July 30, feed
# returned nothing, and a next-session post-earnings name surfaced as a
# merely-penalised BUY instead of a POST_DIGEST veto).
CANARY_SYMBOLS = ("MA", "V", "AXP", "JPM", "MSFT", "AAPL")


def escalate_unknown_when_degraded(decision: GateDecision, feed_degraded: bool) -> GateDecision:
    """Close the §9 hole live-demonstrated by MA: UNKNOWN is deliberately
    flag-only when the feed is healthy (one odd name must not nuke the run),
    but when the CANARY reporters came back dateless the whole feed is
    degraded — and a just-reported name would slip through as merely
    penalised. In that state UNKNOWN escalates to a hard veto."""
    if decision.state != EarningsGate.UNKNOWN or not feed_degraded:
        return decision
    return GateDecision(
        decision.state, "veto", "EARNINGS_FEED_DEGRADED", 0.0, True,
        "earnings feed DEGRADED this run (canary reporters returned no dates) — "
        "proximity unverifiable; vetoing so a just-reported name can't slip "
        "through as merely penalised (the MA hole).")


def stale_feed_canary(canary_results: dict[str, tuple]) -> tuple[bool, list[str]]:
    """Assert a small set of guaranteed quarterly reporters returned plausible
    dates this run. `canary_results` maps symbol → (sessions_to_next,
    sessions_since_last). If a canary symbol comes back (None, None), the whole
    earnings feed is degraded for this run — return (degraded=True, [names]) so
    the caller treats every name conservatively rather than scoring on a feed
    that's silently returning nothing (the known Finnhub-returns-0 failure)."""
    dead = [
        sym for sym, (to_next, since_last) in canary_results.items()
        if to_next is None and since_last is None
    ]
    return (len(dead) > 0, dead)
