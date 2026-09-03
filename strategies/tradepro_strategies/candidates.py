"""ONE candidate record, emitted by every strategy.

Owner, 1 Sep 2026: *"we want a coherant and trustworthy data and not scattered
data ... as user i dont have to think many screens"*, and *"lets not have width
and have something concrete first"*.

Phase 3 of `docs/COHERENT_CANDIDATES_PLAN.md`.

## Why this exists

Four producers publish candidates and each invented its own shape:

    swing              symbol · close · sigma_from_mean · stop
    momentum           symbol · calcs{entry,stop,atr_pct} · close
    post_earnings_puts symbol · strike_indicative · premium_usd · yield_pct
    options_screen     symbol · suggested_strike · annualized_yield_pct · blocks

So the desk's combined Candidates view had to know a little about each — an
adapter per strategy, in the UI. That is the same defect one level up from the
one this repo keeps hitting: N definitions of a thing, drifting apart, with
nothing to fail when they disagree. Two producers were both called "wheel" and
gave 21 and 0 on the same afternoon.

## The contract

A candidate answers six questions, and refuses to be built when it cannot:

    WHAT     symbol, strategy
    DO WHAT  action, entry, level (strike or stop — LABELLED, never conflated)
    HOW GOOD metric + metric_label, ranked WITHIN a strategy
    WHEN     as_of — per candidate, because producers run on different schedules
    TRUSTED? tier — did this strategy pass its pre-registered gates
    FROM WHERE  provenance — per input: IBKR / cache / vendor / fallback / missing

`metric` is deliberately NOT comparable across strategies. A sigma and a %/yr
are different quantities; sorting them into one order would be a number that
means nothing, so `metric_label` travels with it and the UI ranks within a
strategy.

## What it refuses

* an empty symbol or strategy — a row nobody can act on
* an unknown `tier` — "is this proven?" may not be silently absent
* a `level` with no `level_label` — a bare number that might be a strike or a
  stop is worse than no number
* `as_of` missing — a candidate with no timestamp cannot be shown as stale, and
  the desk showed 31-Aug cards at 19:31 on 1 Sep precisely because freshness was
  a page-level fact rather than a row-level one

It does NOT refuse a missing metric or entry. Those are legitimately unknown
when a feed is dark, and the row still tells the reader what it does know —
`None` renders as an em-dash, never as a zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# THREE STATES, NOT TWO (2 Sep 2026).
#
# Owner: "unproven gves me low confidence" — looking at a board where 22 of 34
# rows carried that one word. They were right, and the label was doing too much
# work: it covered two situations that are not remotely the same.
#
#   gated   passed its own pre-registered gates. Swing, Momentum.
#
#   thin    PASSED its gates, but on evidence too narrow to lean on. The puts
#           screen: 229 trades, 89.5% win — and all of it from ~Oct 2020, one
#           market regime, with the "2022 was not a losing year" check resting
#           on NINE events. Calling that "unproven" OVERSTATES the problem.
#
#   failed  its backtest verdict was NEGATIVE. The wheel: v3 said DO NOT FUND,
#           the 200-SMA trend floor failed and META drew down -71.4%. Calling
#           THAT "unproven" UNDERSTATES it — "not yet shown to work" and "shown
#           not to work" are opposite claims.
#
# Collapsing them made a wall of one word, which is why it read as noise rather
# than as a signal. A reader cannot act on "unproven" x22; they can act on
# "this one failed its backtest" and "this one passed on thin evidence".
#
# `unproven` is still ACCEPTED so nothing breaks mid-migration, and maps to the
# most cautious reading.
TIERS = ("gated", "thin", "failed", "unproven")

# What each tier means on a row, in the reader's terms. The UI and the email
# both render from here so they cannot drift into different wordings.
# Actionability order, for any surface that ranks tiers. Part of the tier
# CONTRACT, defined once: the email and the desk must not order the same four
# words two different ways. Lower = more actionable.
TIER_RANK = {"gated": 0, "thin": 1, "unproven": 2, "failed": 3}

TIER_NOTE = {
    "gated": "passed its pre-registered gates",
    "thin": "passed its gates, but on thin evidence — size accordingly",
    "failed": "its BACKTEST FAILED — for study, not for size",
    "unproven": "not proven — for your judgement, not for size",
}


class CandidateError(ValueError):
    """A candidate that cannot be trusted enough to publish."""


@dataclass
class Candidate:
    symbol: str
    strategy: str
    tier: str
    action: str
    as_of: str
    entry: float | None = None
    level: float | None = None
    level_label: str | None = None
    metric: float | None = None
    metric_label: str | None = None
    eligible: bool = True
    why: str = ""
    # Per-input provenance, as produced by `provenance.describe(...).to_dict()`.
    # Empty is ALLOWED but recorded: a strategy that cannot yet say where its
    # numbers came from should be visibly worse than one that can, not silently
    # equal to it.
    provenance: list[dict[str, Any]] = field(default_factory=list)
    # What was checked and what blocked, when the strategy has a gate engine.
    gates: list[dict[str, Any]] = field(default_factory=list)
    # WHY this row cannot be acted on, when it cannot. The desk's "hide
    # blocked" toggle reads this. Empty means "nothing is stopping it" — which
    # must stay distinguishable from "we never checked".
    blocks: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.symbol or "").strip():
            raise CandidateError("candidate has no symbol")
        if not str(self.strategy or "").strip():
            raise CandidateError(f"{self.symbol}: candidate has no strategy")
        if self.tier not in TIERS:
            raise CandidateError(
                f"{self.symbol}/{self.strategy}: tier must be one of {TIERS}, "
                f"got {self.tier!r}. 'Is this strategy proven?' may not be absent "
                f"— a candidate from an ungated sleeve must not read like one "
                f"from a gated sleeve.")
        if self.level is not None and not str(self.level_label or "").strip():
            raise CandidateError(
                f"{self.symbol}/{self.strategy}: level {self.level} has no "
                f"level_label. A bare number that might be a strike or a stop is "
                f"worse than no number.")
        if not str(self.as_of or "").strip():
            raise CandidateError(
                f"{self.symbol}/{self.strategy}: no as_of. Freshness is a "
                f"PER-ROW fact — producers run on different schedules, and a "
                f"candidate with no timestamp cannot be shown as stale.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "strategy": self.strategy, "tier": self.tier,
            "action": self.action, "as_of": self.as_of,
            "entry": self.entry, "level": self.level, "level_label": self.level_label,
            "metric": self.metric, "metric_label": self.metric_label,
            "eligible": self.eligible, "why": self.why,
            "provenance": self.provenance, "gates": self.gates,
            **({"blocks": self.blocks} if self.blocks else {}),
            **({"extra": self.extra} if self.extra else {}),
        }


def emit(cands: list[Candidate]) -> list[dict[str, Any]]:
    """Serialise a batch. Kept separate from the artifact builders so a producer
    adds ONE line and cannot half-adopt the shape."""
    return [c.to_dict() for c in cands]


def validate(rows: list[dict[str, Any]]) -> list[str]:
    """Re-check serialised records, for an ingest guard or a test.

    Returns a list of problems rather than raising, so a caller can report every
    bad row at once instead of one per run — the difference between fixing a
    producer and playing whack-a-mole with it.
    """
    problems: list[str] = []
    for i, r in enumerate(rows):
        try:
            Candidate(
                symbol=r.get("symbol", ""), strategy=r.get("strategy", ""),
                tier=r.get("tier", ""), action=r.get("action", ""),
                as_of=r.get("as_of", ""), entry=r.get("entry"),
                level=r.get("level"), level_label=r.get("level_label"),
                metric=r.get("metric"), metric_label=r.get("metric_label"),
                eligible=bool(r.get("eligible", True)), why=r.get("why", ""),
            )
        except CandidateError as exc:
            problems.append(f"row {i}: {exc}")
    return problems
