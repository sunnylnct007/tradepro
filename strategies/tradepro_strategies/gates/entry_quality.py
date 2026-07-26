"""Entry-quality gate — don't BUY a laggard on no participation.

The ANET lesson: the technical Ichimoku read said BUY, but the name was a weak
relative-strength laggard (RS ~2/10) drifting up on thin volume (~0.5× its 20d
average) near its highs. A breakout with no relative strength and no volume behind
it fails on light selling and fills badly both ways — exactly the low-quality
entry a discretionary trader would pass on.

This gate sits on top of the technical verdict (systematic about RISK, the entry
itself stays discretionary): a BUY only keeps its conviction if the name is NOT a
relative-strength laggard AND today's participation clears a floor. Below either
floor, the BUY is demoted to WAIT and conviction capped — never silently passed.

Fail-loud (feedback_no_false_positives): when RS or volume is MISSING we do NOT
wave the BUY through — we cap its conviction and flag the gap, so a name is never
shown as a clean high-conviction BUY on data we could not check.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EntryQualityConfig:
    min_rs_score: float = 5.0        # sector-RS 0-10 (compute_sector_rs); < this = laggard
    min_volume_ratio: float = 0.8    # today's volume / 20d avg; < this = thin, no participation

    @staticmethod
    def from_env() -> "EntryQualityConfig":
        def _f(k: str, d: float) -> float:
            try:
                return float(os.environ.get(k, d))
            except (TypeError, ValueError):
                return d
        return EntryQualityConfig(
            min_rs_score=_f("TRADEPRO_ENTRY_MIN_RS", 5.0),
            min_volume_ratio=_f("TRADEPRO_ENTRY_MIN_VOLUME_RATIO", 0.8),
        )


@dataclass(frozen=True)
class EntryQualityGate:
    passed: bool                 # True only when BOTH floors are cleared on present data
    action: str                  # "clear" | "veto" (demote BUY→WAIT) | "flag_missing" (cap only)
    rs_score: float | None
    volume_ratio: float | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "action": self.action,
            "rs_score": self.rs_score,
            "volume_ratio": round(self.volume_ratio, 2) if self.volume_ratio is not None else None,
            "reasons": list(self.reasons),
            "summary": "; ".join(self.reasons) if self.reasons else "RS + volume OK",
        }


def evaluate_entry_quality(
    *,
    rs_score: float | None,
    volume_ratio: float | None,
    cfg: EntryQualityConfig,
) -> EntryQualityGate:
    """Classify a candidate entry's quality. Pure — no I/O.

    - Both present and above their floors → ``clear`` (passed).
    - Either present and BELOW its floor → ``veto`` (demote the BUY to WAIT).
    - A floor input MISSING (and none actively failing) → ``flag_missing``
      (cap conviction, surface the gap — never a clean BUY on unchecked data).
    """
    failing: list[str] = []
    missing: list[str] = []

    if rs_score is None:
        missing.append("RS")
    elif rs_score < cfg.min_rs_score:
        failing.append(f"weak relative strength {rs_score:.0f}/10 (< {cfg.min_rs_score:.0f})")

    if volume_ratio is None:
        missing.append("volume")
    elif volume_ratio < cfg.min_volume_ratio:
        failing.append(f"thin volume {volume_ratio:.2f}× 20d-avg (< {cfg.min_volume_ratio:.2f}×)")

    if failing:
        # An actively-failing floor is a hard quality veto — demote the BUY.
        return EntryQualityGate(False, "veto", rs_score, volume_ratio, failing + [
            m + " unknown" for m in missing])
    if missing:
        # Nothing failed, but we couldn't check a floor → cap, don't fabricate.
        return EntryQualityGate(False, "flag_missing", rs_score, volume_ratio,
                                [f"{'/'.join(missing)} unavailable — entry quality unverified"])
    return EntryQualityGate(True, "clear", rs_score, volume_ratio, [])
