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
import logging
import re
from pathlib import Path

_log = logging.getLogger("tradepro.universe")

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

PHANTOM_WINDOW = 500
"""How far back the phantom check looks — about two years.

A wrong-contract series shows up in the bars we would actually trade; an ETF's
first illiquid months do not. Counting the whole history conflates them and
throws away good symbols for having been young once."""
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
    # HARD BOUND on how far the store may widen the job. The union exists to
    # keep recently-dropped names fresh, which is worth a handful of extra
    # symbols and nothing like a multiple.
    #
    # 2026-08-25: a broad seed on 24 Aug took the us_etf tree from 250
    # directories to 991, and because this function unions universe with store,
    # the nightly harvest went from 250 symbols to 955 without anyone choosing
    # that. It ran for an hour, served 113 of its first 114 symbols from
    # yfinance rather than IBKR, and died. A lane whose scope is set by whatever
    # happens to be on disk is not a lane anyone controls.
    #
    # Over the bound: harvest the UNIVERSE ONLY and say so. The universe is the
    # definition of what we trade; everything else is nice-to-have and must not
    # be able to take the lane down.
    max_extra = int(os.environ.get("TRADEPRO_HARVEST_MAX_EXTRA", "60"))

    out: dict[str, None] = {}          # ordered set

    for sym in universe_symbols(strict=False):
        ok, _ = _instrument_ok(sym)
        if ok:
            out.setdefault(sym.upper(), None)

    _store_extra: list[str] = []
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
                    _store_extra.append(sym.upper())

    universe_only = sorted(out)
    extra = [s for s in _store_extra if s not in out]
    if len(extra) > max_extra:
        _log.warning(
            "harvest scope: store holds %d symbols beyond the %d-name universe "
            "(limit %d) — harvesting the UNIVERSE ONLY. Something seeded the "
            "store without widening the universe; raise TRADEPRO_HARVEST_MAX_EXTRA "
            "deliberately if the wider set really is wanted.",
            len(extra), len(universe_only), max_extra)
        return universe_only
    for s in extra:
        out.setdefault(s, None)
    return sorted(out)


def exclusion_reason(symbol: str) -> str | None:
    """Why is this name not on the screen? Always answerable."""
    u = load_universe(strict=False)
    for r in u.get("excluded", []):
        if r["symbol"] == symbol.upper():
            return r["reason"]
    return None


# The step change that makes a volume RATIO meaningless. A 20x jump between
# one half of a 20-session window and the other is not a busy fortnight; it is
# a units change. Real volume surges are large but they do not move a 10-day
# median by two orders of magnitude.
VOLUME_UNIT_STEP = 20.0


def volume_ratio(volumes, i, window=20):
    """Entry-bar volume against its own recent average — or None, with a reason.

    ONE implementation, imported by both screens. The ratio itself is trivial;
    what is not trivial is knowing when NOT to publish it.

    A ratio is immune to a uniform units error — multiply every bar by 100 and
    it cancels. It is NOT immune to a units error that starts partway through
    the window, and that is exactly what we have. IBKR reports 100-share lots,
    the conversion was applied at two points in one pipeline, and the resulting
    x100 inflation is patchy by month and by source vintage (data lane,
    6c22ebd). The 2026-08 partition is inflated; 2026-07 is not.

    Right now the window sits mostly inside the inflated month, so the ratio
    reads 0.95-1.41 — plausible, and wrong by about 17%. The damage arrives
    the other way round: the first CORRECT bar landing in a window that still
    holds inflated ones reads 0.011, and renders on the momentum screen as a
    99% volume collapse on every symbol at once.

    So: detect the discontinuity and return None. A field that says nothing is
    worth more than a field that says something false with two decimal places,
    and this one is labelled CONTEXT — nothing trades on it, so there is no
    cost to withholding it and a real cost to publishing a fiction.

    Returns (ratio, reason). reason is None when the ratio is trustworthy.
    """
    if not volumes or i < window or i >= len(volumes):
        return None, "not enough history"
    w = [x for x in volumes[i - window + 1:i + 1] if x is not None]
    if len(w) < window or sum(w) <= 0:
        return None, "no volume recorded"

    # Find the STEP, do not assume where it is. Comparing the median of the
    # first half against the second half is the obvious test and it fails on
    # the real case: the units change on 2026-08-03, only four of the twenty
    # bars precede it, and both half-medians land on the inflated side. So
    # locate the largest adjacent jump, split the window there, and compare
    # the two sides. A units change persists; a busy day does not.
    def _med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2]

    cut, jump = 0, 1.0
    for k in range(1, len(w)):
        a, b = w[k - 1], w[k]
        if a > 0 and b > 0:
            r = max(a / b, b / a)
            if r > jump:
                cut, jump = k, r
    if jump >= VOLUME_UNIT_STEP and cut >= 3 and len(w) - cut >= 3:
        lo, hi = _med(w[:cut]), _med(w[cut:])
        if lo > 0 and hi > 0:
            step = max(hi / lo, lo / hi)
            if step >= VOLUME_UNIT_STEP:
                return None, (
                    f"volume units change {step:.0f}x inside the {window}-session "
                    "window — the stored series is not on one scale, so a ratio "
                    "over it would be arithmetic on two different units"
                )
    avg = sum(w) / window
    return (round(w[-1] / avg, 2), None) if avg > 0 else (None, "no volume recorded")


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
        # COUNT PHANTOMS IN THE TRADING-RELEVANT WINDOW ONLY.
        #
        # The check was calibrated on wrong-contract splices, where the phantom
        # bars were RECENT — MTUM sat at 6,000 through June 2026. Applied to
        # deep history it produces false positives: when the data lane
        # backfilled USMV to 2011 and VLUE to 2013 overnight, both were
        # quarantined on phantoms dated 2011-12 and 2013-15 respectively —
        # which is when those ETFs had just launched and genuinely did not
        # trade some days. That is real history, not corruption, and dropping
        # a symbol for it is the cry-wolf failure this project has made before.
        #
        # A mis-mapped contract shows up in the bars we would actually trade.
        # Early illiquidity does not. So only the recent window counts, and
        # older ones are returned as information rather than a verdict.
        recent = closes[-PHANTOM_WINDOW:]
        recent_v = volumes[-PHANTOM_WINDOW:]
        phantom = sum(1 for i in range(1, len(recent))
                      if recent_v[i] == 0 and recent[i] == recent[i - 1])
        return phantom <= MAX_PHANTOM_BARS, phantom
    recent = closes[-120:] or closes
    med = _st.median(recent)
    if med <= 0:
        return False, 999
    ratio = round(max(closes) / med, 1)
    return ratio <= MAX_POISON_RATIO, ratio


# ── THE WHEEL SLEEVE (moved here 1 Sep 2026) ────────────────────────────────
#
# Owner: "its good to subclassify but we shd have unirkm list".
#
# This list lived inside cli/options_screen.py, which made it a FOURTH
# definition of "what we screen" alongside the committed 244, the DB-backed
# /api/universes/{name}, and a hardcoded 30 in ScreenerEndpoints. A screen
# running on a different list from its neighbours is how "0 of 30" and "21 of
# 82" looked like a strategy disagreement when it was a universe disagreement.
#
# It lives in the module that owns what we trade, is imported by both the screen
# and the universe builder, and is COPIED NOWHERE. Sub-classification (tags) is
# fine; a second list is not.
#
# STILL A HAND LIST, and that is the next thing to fix — it should be derived
# from optionable + liquidity + per-position affordability. Deriving it today
# would silently change which names get screened on a live trading surface, so
# that is a separate reviewable step, not a side effect of consolidation.
WHEEL_SLEEVE: tuple[str, ...] = (
    # original core
    "CVX", "XOM", "ABBV", "JNJ", "VZ", "MO", "PG", "DUK", "D", "PEP",
    # affordable, liquid chains (fit a £10k/pos pot)
    "KO", "T", "PFE", "F", "INTC", "BAC", "WFC", "CSCO", "MU", "GM",
    "SLB", "OXY", "KMI", "DVN", "GILD", "BMY", "CMCSA", "DOW", "WMB", "HPE",
    # mega-liquid chains — the deepest/tightest option markets there are. Their
    # strikes only fit a RAISED pot (TRADEPRO_WHEEL_PER_POSITION_GBP): the
    # notional gate decides affordability per the user's configured capital,
    # the universe just makes them CANDIDATES (config-driven, not pre-filtered).
    "NVDA", "GOOGL", "AAPL", "MSFT", "AMD", "QCOM",
    # expansion 36 → 66 (owner 2026-08-09: "we need more symbols to compare").
    # Same bar: liquid chains, names you'd accept assignment on.
    # financials / healthcare / consumer / tech / energy / industrials
    "IBM", "JPM", "C", "USB", "SCHW", "MRK", "CVS", "TGT", "SBUX", "NKE",
    "KHC", "MDLZ", "ORCL", "DELL", "HPQ", "HAL", "FCX", "NEM", "DAL", "UPS", "ON",
    # ETFs — natural wheel underlyings: deep chains and STRUCTURALLY no
    # earnings event inside any expiry window (see _ETF_UNDERLYINGS).
    "XLE", "XLF", "XLI", "XLU", "GDX", "SLV", "TLT", "IWM", "KRE",
    # owner's IBKR "TradePro-Screen" watchlist merge (10 Aug 2026 — "is the
    # list based on my IBKR watchlist?" — it is now): the equities from that
    # watchlist not already above. Watchlist edits still need a manual sync
    # here (auto-sync = future work; the MCP watchlist API is session-side).
    "ACN", "TSLA", "GS", "MS", "META", "UBER", "DIS", "HOOD", "MRVL",
    "APLD", "AMZN", "PLTR", "IBKR",
    # index ETFs (owner 11 Aug 2026: "add index on the option wheel screen").
    # ETF form, NOT SPX-style index options — those are cash-settled/European
    # so they can't assign shares, which breaks the wheel's assignment leg.
    # SPY/QQQ strikes only fit a raised per-position pot; the notional gate
    # reports that honestly rather than pre-filtering them out.
    "SPY", "QQQ", "DIA",
)


def universe_by_tag(tag: str, *, strict: bool = False) -> list[str]:
    """Symbols carrying `tag` — the ONE way to ask for a sleeve.

    Owner, 1 Sep 2026: "its good to subclassify but we shd have unirkm list".
    Tags are sub-classification OVER the single committed universe, never a
    second list. `build_universe` derives them (large_50 by median dollar
    volume, high_beta from beta_tier, wheel from WHEEL_SLEEVE) and writes them
    into tradeable.json.

    Returns [] rather than raising when the universe predates tags, so a caller
    can fall back to its own list during the migration and say so — a silently
    empty sleeve would be the screen-evaluates-nobody failure again.
    """
    rows = load_universe(strict=strict).get("symbols") or []
    return [r["symbol"] for r in rows
            if tag in (r.get("tags") or []) and r.get("symbol")]


def universe_tags() -> dict[str, int]:
    """Every tag in the committed universe and how many symbols carry it."""
    from collections import Counter
    c: Counter = Counter()
    for r in (load_universe(strict=False).get("symbols") or []):
        for t in (r.get("tags") or []):
            c[t] += 1
    return dict(sorted(c.items()))
