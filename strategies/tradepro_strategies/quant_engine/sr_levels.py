"""Pivot-based support/resistance levels — a VERBATIM port of the shipped chart.

Source of truth: `frontend/src/components/desk/CandleIchimokuChart.tsx::pivotLevels`.
This must stay a faithful port, not an improvement: the whole point of
SR_LEVEL_STUDY_GATES_V1.md is to grade the lines actually drawn on the desk.
Any "better" idea belongs in a separate function with its own gates file.

The TypeScript, for reference:

    for (let i = win; i < c.length - win; i++) {
      let isHi = true, isLo = true;
      for (let j = i - win; j <= i + win; j++) {
        if (c[j].high > c[i].high) isHi = false;
        if (c[j].low  < c[i].low)  isLo = false;
      }
      if (isHi) rawHi.push(c[i].high);
      if (isLo) rawLo.push(c[i].low);
    }

Note the comparison is STRICT (`>` / `<`), so a flat-topped double high counts
as a pivot at both bars. Preserved deliberately — it is what ships.
"""
from __future__ import annotations

from dataclasses import dataclass

WIN = 5
CLUSTER_PCT = 0.005
MAX_SCAN = 240


@dataclass
class SRLevel:
    level: float
    touches: int


def _collapse(xs: list[float], cluster_pct: float) -> list[SRLevel]:
    """Collapse near-equal pivots, tracking how many folded in.

    Mirrors the TS `collapse`: sort ascending, and merge into the PREVIOUS
    output level when within `cluster_pct` of it, updating that level to the
    running mean. The comparison divides by the incoming value `v`, not by the
    existing level — kept as-is.
    """
    out: list[SRLevel] = []
    for v in sorted(xs):
        prev = out[-1] if out else None
        # JS semantics at v == 0: `0/0` is NaN and `x/0` is Infinity, and
        # `NaN > cluster_pct` is FALSE while `Infinity > cluster_pct` is TRUE.
        # Python raises instead, so the branch is written out explicitly rather
        # than left to differ from the shipped chart. Real prices are never 0;
        # this exists so the port cannot diverge, not because it will fire.
        if prev is None:
            new_level = True
        elif v == 0:
            new_level = abs(v - prev.level) != 0     # NaN → merge, Inf → split
        else:
            new_level = abs(v - prev.level) / v > cluster_pct
        if new_level:
            out.append(SRLevel(level=v, touches=1))
        else:
            prev.level = (prev.level * prev.touches + v) / (prev.touches + 1)
            prev.touches += 1
    return out


def pivot_levels(
    highs: list[float], lows: list[float], *,
    win: int = WIN, cluster_pct: float = CLUSTER_PCT, max_scan: int = MAX_SCAN,
) -> tuple[list[SRLevel], list[SRLevel]]:
    """Return (resistance_levels, support_levels) from the last `max_scan` bars.

    CAUSALITY: a pivot at bar i is only confirmed at bar i+win, and the loop
    bound `i < len - win` already enforces that — the final `win` bars can
    never produce a level. Callers doing a point-in-time study must slice the
    series to the decision bar and call this with that slice; see
    `levels_asof`.
    """
    hi = highs[-max_scan:] if max_scan else list(highs)
    lo = lows[-max_scan:] if max_scan else list(lows)
    n = len(hi)
    raw_hi: list[float] = []
    raw_lo: list[float] = []
    for i in range(win, n - win):
        is_hi = True
        is_lo = True
        for j in range(i - win, i + win + 1):
            if hi[j] > hi[i]:
                is_hi = False
            if lo[j] < lo[i]:
                is_lo = False
        if is_hi:
            raw_hi.append(hi[i])
        if is_lo:
            raw_lo.append(lo[i])
    return _collapse(raw_hi, cluster_pct), _collapse(raw_lo, cluster_pct)


def levels_asof(
    highs: list[float], lows: list[float], t: int, *,
    win: int = WIN, cluster_pct: float = CLUSTER_PCT, max_scan: int = MAX_SCAN,
) -> tuple[list[SRLevel], list[SRLevel]]:
    """Levels knowable at the CLOSE of bar `t`, using no information after it.

    The slice ends at `t + 1` (inclusive of bar t). Inside `pivot_levels` the
    `i < len - win` bound then guarantees the newest usable pivot sits at least
    `win` bars back — exactly the confirmation lag a live desk faces. This is
    the function every study must call; using `pivot_levels` on the full series
    and then testing reactions to it is look-ahead.
    """
    return pivot_levels(highs[: t + 1], lows[: t + 1],
                        win=win, cluster_pct=cluster_pct, max_scan=max_scan)


__all__ = ["SRLevel", "pivot_levels", "levels_asof", "WIN", "CLUSTER_PCT", "MAX_SCAN"]
