"""Combined verdict — fuses technical bucket + catalyst overlay + analyst
flow into a single annotated recommendation. Phase 17.5 of the
catalyst sprint (DATA_ROADMAP §13.5).

The Ecopetrol (EC) case 2026-05-21 was the design driver:
    Technical signal:   WAIT  (89th percentile)
    News catalyst:      STRONG BUY (election 10d, oil $105)
    Analyst flow:       MIXED (1 buy, 4 sell)
    Combined verdict:   BUY with tight stop
    Confidence:         Medium-High
    Catalyst expiry:    June 21 (runoff date)

The combined verdict NEVER replaces the technical bucket — the spec
is explicit: "Don't replace the technical bucket — annotate it. User
wants BOTH views so they can reason about why they disagree." So this
module emits a STRUCTURED verdict with three separate layer signals
plus a combined recommendation, leaving the technical bucket
untouched in the row.

Inputs come straight off a Compare row dict:
    bucket               BUY / WAIT / AVOID
    bucket_reason        human-readable explainer
    catalysts            list of Catalyst dicts (Phase 17.2)
    news                 list of news items with `sentiment` floats
    analyst_recommendations  {strong_buy, buy, hold, sell, strong_sell, bull_score, ...}

Output (the new `combined_verdict` field on the row):
    {
      "technical": {"signal": "WAIT", "reason": "..."},
      "catalyst": {"signal": "STRONG_BUY", "reasons": [...], "soonest_date": ...},
      "analyst": {"signal": "MIXED", "reason": "1 buy / 4 sell"},
      "combined": "BUY with tight stop",
      "combined_kind": "BUY_WITH_RISK",   # machine-readable enum
      "confidence": "Medium-High",
      "reasoning": [...],                  # ordered sentences, surface verbatim
    }

The logic is intentionally **rule-based and explainable** — no ML, no
hidden weights. Each rule has a one-line audit string that the UI
surfaces under "Reasoning". When the user reports a missed trade,
adding a new rule (or pinning the existing geometry with a behave
scenario) is the natural extension point.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Catalyst-sentiment heuristics — each catalyst kind maps to a default
# directional bias, OVERRIDDEN by the sentiment score on its source
# headline when one is available. Election / FOMC are inherently
# bidirectional (good and bad outcomes possible), so they only count
# as "soon catalyst" magnifiers rather than directional signals.
_DIRECTIONAL_BY_KIND = {
    # Earnings beats are bullish, misses are bearish — but we read the
    # actual sentiment of the headline, not just the kind.
    "earnings": "headline",
    # FDA approval / antitrust block / SEC charges — directional.
    "regulatory": "headline",
    # Oil/gold/copper move — directional via headline sentiment.
    "commodity": "headline",
    # Elections + central bank — bidirectional. They affect timing
    # / volatility but the direction depends on outcome (which we
    # can't predict from the headline alone).
    "election": "bidirectional",
    "central_bank": "bidirectional",
}

_CATALYST_HORIZON_DAYS = 30
"""How many days out a dated catalyst stays "near-term" enough to
influence the verdict. Beyond this the catalyst is informational,
not actionable for current entry."""


def derive_combined_verdict(row: dict) -> dict:
    """Produce the combined recommendation from the row's existing
    fields. Pure function — no I/O, no side effects."""
    technical = _technical_layer(row)
    catalyst = _catalyst_layer(row)
    analyst = _analyst_layer(row)
    combined_kind, combined_label, confidence, reasoning = _combine(
        technical, catalyst, analyst,
    )
    return {
        "technical": technical,
        "catalyst": catalyst,
        "analyst": analyst,
        "combined": combined_label,
        "combined_kind": combined_kind,
        "confidence": confidence,
        "reasoning": reasoning,
    }


# --- layer derivations ---------------------------------------------------

def _technical_layer(row: dict) -> dict:
    """Lift the existing technical bucket onto the combined-verdict
    envelope verbatim. The combined verdict NEVER overrides this; it
    just sits alongside."""
    signal = (row.get("bucket") or "WAIT").upper()
    reason = row.get("bucket_reason") or ""
    return {"signal": signal, "reason": reason}


def _catalyst_layer(row: dict) -> dict:
    """Aggregate catalysts into a single directional verdict + a list
    of one-line reasons. Soonest near-term catalyst's date is exposed
    so the UI can render a countdown."""
    catalysts = row.get("catalysts") or []
    news = row.get("news") or []
    if not catalysts:
        return {
            "signal": "NONE",
            "reasons": [],
            "soonest_date": None,
            "soonest_kind": None,
        }

    today = datetime.now(timezone.utc).date()
    sentiment_by_title = {
        (n.get("title") or "").strip(): n.get("sentiment")
        for n in news
        if isinstance(n, dict)
    }

    score = 0.0   # signed: positive = bullish catalyst pressure, negative = bearish
    reasons: list[str] = []
    soonest_date: str | None = None
    soonest_kind: str | None = None
    soonest_days: int | None = None

    for c in catalysts:
        if not isinstance(c, dict):
            continue
        kind = c.get("kind", "")
        title = (c.get("title") or "").strip()
        occurs = c.get("occurs_on")
        days_away = _days_until(occurs, today) if occurs else None
        sent = sentiment_by_title.get(title)
        bias_mode = _DIRECTIONAL_BY_KIND.get(kind, "headline")

        # Headline-driven catalysts read sentiment of the source.
        # Bidirectional ones (elections, FOMC) contribute neutral
        # pressure regardless of headline sentiment — what they DO
        # is amplify near-term significance.
        bias = 0.0
        rationale: str
        if bias_mode == "bidirectional":
            rationale = f"{kind}: {_kind_label(kind)}"
            if days_away is not None and days_away <= _CATALYST_HORIZON_DAYS:
                # Near-term election/FOMC is a magnifier — pushes the
                # verdict toward "watch closely with defined risk".
                rationale += f" in {days_away}d"
        else:
            if isinstance(sent, (int, float)):
                if sent >= 0.3:
                    bias = 1.0
                    rationale = f"{kind}: positive headline (s={sent:+.2f})"
                elif sent <= -0.3:
                    bias = -1.0
                    rationale = f"{kind}: negative headline (s={sent:+.2f})"
                else:
                    rationale = f"{kind}: neutral headline (s={sent:+.2f})"
            else:
                rationale = f"{kind}: no sentiment score"

        # Weight near-term catalysts more. A catalyst within 7 days
        # counts 2x; within 30 days 1x; beyond, 0.25x.
        weight = 0.25
        if days_away is None:
            weight = 1.0
        elif days_away <= 7:
            weight = 2.0
        elif days_away <= _CATALYST_HORIZON_DAYS:
            weight = 1.0

        score += bias * weight
        reasons.append(rationale)

        # Track soonest near-term catalyst for the countdown header.
        if days_away is not None and days_away >= 0 and days_away <= _CATALYST_HORIZON_DAYS:
            if soonest_days is None or days_away < soonest_days:
                soonest_days = days_away
                soonest_date = occurs
                soonest_kind = kind

    # Translate signed score → discrete signal.
    if score >= 1.5:
        signal = "STRONG_BUY"
    elif score >= 0.5:
        signal = "BUY"
    elif score <= -1.5:
        signal = "STRONG_AVOID"
    elif score <= -0.5:
        signal = "AVOID"
    else:
        # Has catalysts but no directional consensus = MIXED.
        signal = "MIXED"

    return {
        "signal": signal,
        "reasons": reasons,
        "soonest_date": soonest_date,
        "soonest_kind": soonest_kind,
    }


def _analyst_layer(row: dict) -> dict:
    """Read analyst_recommendations counts into a discrete signal."""
    ar = row.get("analyst_recommendations") or {}
    sb = int(ar.get("strong_buy") or 0)
    b = int(ar.get("buy") or 0)
    h = int(ar.get("hold") or 0)
    s = int(ar.get("sell") or 0)
    ss = int(ar.get("strong_sell") or 0)
    total = sb + b + h + s + ss
    if total == 0:
        return {"signal": "NO_COVERAGE", "reason": "no analyst data"}
    bull = sb + b
    bear = s + ss
    if bull >= 2 * (bear + h) and bull > 0:
        signal = "STRONG_BUY"
    elif bull > bear and bull > 0:
        signal = "BUY"
    elif bear > 2 * (bull + h) and bear > 0:
        signal = "STRONG_AVOID"
    elif bear > bull and bear > 0:
        signal = "AVOID"
    else:
        signal = "MIXED"
    reason = f"{bull} buy / {h} hold / {bear} sell"
    return {"signal": signal, "reason": reason}


# --- combine + confidence -----------------------------------------------

def _combine(
    technical: dict, catalyst: dict, analyst: dict,
) -> tuple[str, str, str, list[str]]:
    """Apply the rule table that fuses three layers into a single
    recommendation. Returns (kind_enum, label, confidence, reasoning_lines)."""
    t = technical["signal"]
    c = catalyst["signal"]
    reasoning: list[str] = []
    reasoning.append(
        f"Technical: {t} — {technical['reason'] or 'rule chain'}"
    )
    if c == "NONE":
        reasoning.append("Catalyst: no fresh dated events on this symbol.")
    else:
        reasoning.append(f"Catalyst: {c} — {', '.join(catalyst['reasons'][:3])}.")
    a = analyst["signal"]
    if a == "NO_COVERAGE":
        reasoning.append("Analyst: no coverage data.")
    else:
        reasoning.append(f"Analyst: {a} — {analyst['reason']}.")

    # The interesting case: technical says WAIT/AVOID but a near-term
    # bullish catalyst sits within 30 days. This is the Ecopetrol
    # geometry — the trade exists because of the catalyst, not the
    # trend. Use a "BUY with tight stop" rec so the user knows they
    # have to manage risk explicitly.
    soonest = catalyst.get("soonest_date")
    if t == "WAIT" and c in ("STRONG_BUY", "BUY") and soonest:
        return (
            "BUY_WITH_RISK",
            "BUY with tight stop (catalyst-driven, technical lagging)",
            "Medium-High",
            reasoning + [
                "Trend isn't confirmed yet, but the catalyst window is "
                "real and dated — size small, define risk tight, exit "
                "on catalyst expiry if signal hasn't followed through.",
            ],
        )

    # Aligned bullish — technical + catalyst + analyst all positive.
    if t == "BUY" and c in ("STRONG_BUY", "BUY") and a in ("STRONG_BUY", "BUY"):
        return ("STRONG_BUY", "STRONG BUY (all layers aligned)", "High", reasoning)

    # Technical BUY without catalyst confirmation — still a buy.
    if t == "BUY":
        if c in ("AVOID", "STRONG_AVOID"):
            return (
                "WAIT",
                "WAIT (technical BUY but bearish catalyst — wait for catalyst to clear)",
                "Medium",
                reasoning + [
                    "A negative catalyst sitting on the symbol means "
                    "the technical signal is at risk of an event-driven "
                    "reversal. Better to wait.",
                ],
            )
        return ("BUY", "BUY (technical signal confirmed)", "Medium-High", reasoning)

    # Technical AVOID + bullish catalyst — interesting but not enough
    # to override. Surface it explicitly so the user knows the
    # disagreement exists.
    if t == "AVOID" and c in ("STRONG_BUY", "BUY"):
        return (
            "AVOID_DESPITE_CATALYST",
            "AVOID despite catalyst (technical broken — wait for trend reclaim)",
            "Medium",
            reasoning + [
                "Catalyst is real, but the technical signal is genuinely "
                "broken. Catalysts can fade; broken trends rarely reverse "
                "on a single event. Skip.",
            ],
        )

    # Technical AVOID — default to that.
    if t == "AVOID":
        return ("AVOID", "AVOID (technical signal broken)", "Medium-High", reasoning)

    # Technical WAIT, no actionable catalyst — quiet day.
    return ("WAIT", "WAIT (no fresh edge in any layer)", "Medium", reasoning)


# --- helpers ------------------------------------------------------------

def _days_until(occurs_on: str | None, today) -> int | None:
    """Return integer days between today (date) and the ISO-8601
    `occurs_on`. Positive = future. None on parse failure."""
    if not occurs_on:
        return None
    try:
        target = datetime.fromisoformat(occurs_on.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None
    return (target - today).days


def _kind_label(kind: str) -> str:
    return {
        "election": "Election",
        "earnings": "Earnings",
        "central_bank": "Central bank decision",
        "commodity": "Commodity move",
        "regulatory": "Regulatory event",
    }.get(kind, kind)


__all__ = ["derive_combined_verdict"]
