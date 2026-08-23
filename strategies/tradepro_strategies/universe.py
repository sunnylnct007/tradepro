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
import re
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
"""Historical max vs recent median, used only as a first-pass hint.

NOT sufficient on its own. BILL genuinely traded $256.90 in February 2022 on
1.3M shares and fell to $40 — a real 84% drawdown, and a ratio test calls that
corruption. Meanwhile the actual corruption is not always extreme in price.
See PHANTOM_* below for the test that decides."""

MAX_PHANTOM_BARS = 4
"""A PHANTOM bar is an unchanged close on ZERO volume. Count them; more than
a handful means the series is not this instrument.

Chosen by MEASURING candidate tests against symbols whose truth was
established by inspection, rather than by reasoning about what ought to work:

    MTUM 31 · QUAL 34 · USMV 26 · VLUE 15   <- verified wrong-contract
    STX 1 · AMD 1 · everything else 0        <- verified fine

Complete separation, which no price-based test achieved. Two earlier attempts
failed and are recorded so they are not retried:

  * max/recent-median ratio — flags BILL, which really did fall from $256.90
    to $40 on 1.3M shares, and VIXY, whose decay is what a VIX futures ETF
    does. Both are facts about the world, not data faults.
  * "far from the price level AND thin volume" — flags MU, because a stock
    that rose 10x has old bars that are legitimately both cheaper and
    quieter. It quarantined a clean series on the strength of having grown.

The tell is that nobody traded it: a real collapse happens on HEAVY volume,
while a mis-mapped contract sits unchanged because no ticks arrive for it."""

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


def harvest_symbols(store_dir: "str | os.PathLike | None" = None) -> list[str]:
    """What the daily harvest should REFRESH — the one definition of it.

    Harvesting is not screening, and the two want different sets. A screen must
    only ever consider the committed universe (`universe_symbols`), which is why
    `load_universe` refuses to fall back to a directory listing. A harvest also
    wants to keep refreshing names that have recently DROPPED out of the
    universe, so their history stays current if they qualify again — re-seeding
    years of bars is expensive, and a name near the liquidity floor crosses it in
    both directions.

    So: the union of the committed universe and whatever the store already
    holds, put through the same `_instrument_ok` filter as everything else.

    Why this exists at all. `scripts/bar-cache-harvest-daily.sh` derived its list
    by `ls`-ing the cache directory and re-implementing the exclusions in grep.
    Two consequences:

    1. **A new universe member was never harvested.** No directory yet, so `ls`
       could not see it, so it got no daily bars — silently, until somebody
       noticed and ran a separate seed step. The script's own comment conceded
       this ("adding new symbols is a separate seed step").
    2. **The two filters had already drifted.** The shell pattern
       `^[A-Z0-9.-]+$` admits a `-USD` crypto pair; `_instrument_ok` rejects it.
       Nobody would notice until a crypto directory appeared in the us_etf tree.

    The directory listing also once produced a phantom `US_ETF` "symbol" from a
    mis-nested folder and marked 37 consecutive daily harvests FAILED while every
    real symbol was fine. Anything that is not a plausible ticker is dropped here
    rather than handed to a provider to 404 on.
    """
    out: dict[str, None] = {}          # ordered set

    for sym in universe_symbols(strict=False):
        ok, _ = _instrument_ok(sym)
        if ok:
            out.setdefault(sym.upper(), None)

    if store_dir is not None:
        p = Path(store_dir)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if not child.is_dir():
                    continue
                sym = child.name.strip()
                # A directory name is not a ticker just because it is a
                # directory. Reject the asset-class folder appearing inside
                # itself, and anything not ticker-shaped.
                if sym.upper() == p.name.upper():
                    continue
                if not re.fullmatch(r"[A-Za-z0-9.\-^=]+", sym):
                    continue
                ok, _ = _instrument_ok(sym)
                if ok:
                    out.setdefault(sym.upper(), None)

    return sorted(out)


def exclusion_reason(symbol: str) -> str | None:
    """Why is this name not on the screen? Always answerable."""
    u = load_universe(strict=False)
    for r in u.get("excluded", []):
        if r["symbol"] == symbol.upper():
            return r["reason"]
    return None


def poison_check(closes, volumes=None):
    """Is this series really this instrument? Returns (ok, phantom_bar_count).

    ONE implementation, imported by both screens and the universe builder.
    It previously existed as three near-copies, which is how the screens and
    the universe could disagree about whether a symbol was clean.

    See MAX_PHANTOM_BARS for the measurements behind the test and for the two
    earlier approaches that failed.
    """
    import statistics as _st
    if not closes:
        return True, 0
    if volumes and len(volumes) == len(closes):
        phantom = sum(1 for i in range(1, len(closes))
                      if volumes[i] == 0 and closes[i] == closes[i - 1])
        return phantom <= MAX_PHANTOM_BARS, phantom
    recent = closes[-120:] or closes
    med = _st.median(recent)
    if med <= 0:
        return False, 999
    ratio = round(max(closes) / med, 1)
    return ratio <= MAX_POISON_RATIO, ratio
