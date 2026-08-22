"""Analog evaluation — "what happened last time this looked like this?"

Graded against ANALOG_EVALUATION_GATES_V1.md (committed c221e95 BEFORE the
run). Shared by the study harness and, if it passes, the desk.

THE STATE VECTOR is deliberately scale-free so a $12 stock and a $960 stock
are comparable, and deliberately small — six dimensions over ~4,000 bars per
symbol. Adding dimensions makes neighbours look more specific while making
them less meaningful, which is the classic way this technique flatters itself.

NO LOOKAHEAD is structural, not a convention: analogs for bar i come only from
bars earlier than `i - horizon`, so no analog's outcome window can overlap the
moment being evaluated. `assert_no_lookahead` proves it per evaluation rather
than trusting the loop bounds.
"""
from __future__ import annotations

import math

DIMS = ("vs200", "vs50", "vs20", "atr_pct", "below_52w", "ret20")


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def state_at(c, h, l, i):
    """Scale-free state vector at bar i, or None if there is too little history."""
    if i < 252 or c[i] <= 0:
        return None
    s200, s50, s20 = _sma(c, i, 200), _sma(c, i, 50), _sma(c, i, 20)
    if min(s200, s50, s20) <= 0:
        return None
    trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])) for j in range(i - 13, i + 1)]
    atr = sum(trs) / 14
    hi52 = max(h[i - 251:i + 1])
    if hi52 <= 0 or c[i - 20] <= 0:
        return None
    return (
        100 * (c[i] / s200 - 1),
        100 * (c[i] / s50 - 1),
        100 * (c[i] / s20 - 1),
        100 * atr / c[i],
        100 * (hi52 - c[i]) / hi52,
        100 * (c[i] / c[i - 20] - 1),
    )


def outcome(c, h, l, i, *, up_pct, dn_pct, horizon):
    """Did it reach +up before -dn within `horizon`? 1 / 0, or None if unresolved.

    Same conservative tie-break as the odds calculator: a session touching
    BOTH barriers counts as the DOWN outcome, because daily bars cannot order
    the high and the low.
    """
    if i + horizon >= len(c) or c[i] <= 0:
        return None
    up, dn = c[i] * (1 + up_pct), c[i] * (1 + dn_pct)
    for j in range(i + 1, i + horizon + 1):
        hit_u, hit_d = h[j] >= up, l[j] <= dn
        if hit_d:
            return 0
        if hit_u:
            return 1
    return 0


def assert_no_lookahead(i, pool_idx, horizon):
    """Prove it for THIS evaluation rather than trusting the loop bounds.

    A gate that is enforced by an assertion cannot be quietly lost to a later
    refactor the way an off-by-one in a range() can — and this session has
    already produced two off-by-one bugs in exactly that shape.
    """
    if pool_idx and max(pool_idx) >= i - horizon:
        raise AssertionError(
            f"lookahead: analog at {max(pool_idx)} is not strictly earlier than "
            f"{i} - {horizon}; its outcome window overlaps the evaluated bar")
    return True


def zscore_stats(states):
    """Per-dimension mean/sd over a pool. Computed from the POOL ONLY, so the
    scaling itself never sees the evaluated bar."""
    n = len(states)
    if n < 2:
        return None
    means, sds = [], []
    for d in range(len(DIMS)):
        col = [s[d] for s in states]
        m = sum(col) / n
        var = sum((x - m) ** 2 for x in col) / (n - 1)
        means.append(m)
        sds.append(math.sqrt(var) if var > 0 else 1.0)
    return means, sds


def nearest(target, pool_states, pool_outcomes, pool_idx, stats, k):
    """K nearest neighbours by z-scored Euclidean distance → predicted P(up)."""
    means, sds = stats
    tz = [(target[d] - means[d]) / sds[d] for d in range(len(DIMS))]
    scored = []
    for n_i, s in enumerate(pool_states):
        dist = 0.0
        for d in range(len(DIMS)):
            diff = tz[d] - (s[d] - means[d]) / sds[d]
            dist += diff * diff
        scored.append((dist, n_i))
    scored.sort()
    top = scored[:k]
    if not top:
        return None, 0, None
    outs = [pool_outcomes[n_i] for _, n_i in top]
    return (sum(outs) / len(outs), len(outs),
            [pool_idx[n_i] for _, n_i in top])
