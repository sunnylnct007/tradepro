"""Shared text-formatting helpers with no internal dependencies (safe to
import from anywhere without circular-import risk)."""
from __future__ import annotations


def ordinal_suffix(n: float | int) -> str:
    """'st'/'nd'/'rd'/'th' for n. Handles the 11th/12th/13th special case
    (n % 100 in 10..20 is always 'th', including 111, 211, ...).

    Every percentile string in the codebase used to hardcode "th"
    unconditionally — not a modulo-10 bug, there was no ordinal logic at
    all, so "91th"/"93th" (ANET/XLF/SIZE) rendered next to "1th"/"2th" the
    same way. This is the one place that logic now lives."""
    n = int(round(n))
    if 10 <= abs(n) % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(abs(n) % 10, "th")
