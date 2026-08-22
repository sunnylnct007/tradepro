"""THE tradeable universe — a definition, not a directory listing.

This file exists because for months every screen and every backtest decided
what to trade by listing `~/.tradepro/bar_cache/`. The harvester's output WAS
the universe. That is how copper futures (HG=F) and a European index (^STOXX)
came to be published as buyable rows with entry prices and stops, and how the
momentum backtest came to be measured over instruments the screen can no
longer emit.

The patch shipped on 22 Aug — `_tradeable()`, excluding symbols containing
"=", "^", "." or ending "-USD" — is string matching, not a definition. It
would still admit a $0.40 shell company with 900 shares a day traded. This
replaces it.

MEMBERSHIP IS EARNED, on evidence, against criteria written down here. Owner's
rule, 22 Aug: *"we only do this for stocks that are solid and not penny
stocks."* That is encoded below as price and liquidity floors rather than left
as a sentiment.

Every screen reads `load_universe()`. Nothing lists a directory. A symbol that
is not in the universe cannot be published, and the reason it was excluded is
recorded rather than discarded — an absent name should always be explicable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── criteria ──────────────────────────────────────────────────────────────
# Each is a number a person can argue with, which is the point.

MIN_PRICE = 5.00
"""Below $5 is the conventional penny-stock line, and it is also where
percentage stops stop meaning anything: a $0.80 stock ticking one cent is a
1.25% move, so an -8% stop is six ticks of noise."""

MIN_DOLLAR_VOLUME = 10_000_000
"""Median daily dollar volume. Liquidity is what makes a limit order a real
plan rather than a wish — at $10M/day a few thousand shares is invisible.
Median, not mean, so one earnings-day spike cannot buy membership."""

MIN_SESSIONS = 500
"""~2 years. Below this the per-symbol history in a drill-down is too thin to
say anything, and the analog/backtest work has nothing to stand on."""

MAX_POISON_RATIO = 6.0
"""Historical max vs recent median. Above this the stored series is a
different instrument — a wrong venue or wrong contract."""

MIN_RECENT_COVERAGE = 0.90
"""Fraction of the last 60 expected sessions actually present. A name the
harvester is quietly failing on must not sit on a screen looking current."""

_UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "universe" / "tradeable.json"


def universe_path() -> Path:
    return Path(os.environ.get("TRADEPRO_UNIVERSE_PATH", _UNIVERSE_PATH))


def _instrument_ok(sym: str) -> tuple[bool, str | None]:
    """Instrument-type exclusions. Kept from the 22 Aug patch because they are
    still correct — they are simply no longer the whole story."""
    if not sym:
        return False, "empty symbol"
    if "." in sym:
        return False, "foreign listing — no IBKR entitlement and the ticker is unmapped"
    if "=" in sym:
        return False, "futures contract — different mechanics entirely"
    if sym.startswith("^"):
        return False, "index — not directly tradeable"
    if sym.endswith("-USD"):
        return False, "crypto pair"
    return True, None


def load_universe(*, strict: bool = True) -> dict:
    """Load the committed universe. Fails LOUDLY if absent when strict.

    Deliberately no fallback to a directory listing. A missing universe file
    must stop a screen, not silently restore the behaviour this module exists
    to end.
    """
    p = universe_path()
    if not p.exists():
        if strict:
            raise FileNotFoundError(
                f"no universe at {p}. Screens must not fall back to listing the bar "
                f"cache — that is exactly how futures and indices became candidates. "
                f"Build it: python -m tradepro_strategies.cli.build_universe")
        return {"symbols": [], "excluded": [], "as_of": None}
    return json.loads(p.read_text())


def universe_symbols(**kw) -> list[str]:
    return [r["symbol"] for r in load_universe(**kw)["symbols"]]


def exclusion_reason(symbol: str) -> str | None:
    """Why is this name not on the screen? Always answerable."""
    u = load_universe(strict=False)
    for r in u.get("excluded", []):
        if r["symbol"] == symbol.upper():
            return r["reason"]
    return None
