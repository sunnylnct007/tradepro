"""Derive which names the options screen looks at, instead of typing them.

WHY THIS EXISTS
---------------
`WHEEL_SLEEVE` is 82 hand-typed symbols, and universe.py has carried its own
indictment for weeks:

    "STILL A HAND LIST, and that is the next thing to fix — it should be
     derived from optionable + liquidity + per-position affordability."

Measured 21 Oct 2026, the cost of not doing it:

    tradeable universe (harvested, bars kept)   956
    screened for options                         82
    harvested but NEVER looked at               874

The options board sees 8.6% of the names we already hold data for. The owner
noticed because MDB — "best put option i have see is mongo db" — was absent.
MDB is not blocked, not refused, not illiquid: it was never typed in. Of a
16-name sample drawn from the 874, FIFTEEN clear the ownership gate, including
PANW, CRWD, LRCX, SHOP and ANET.

This is the shape that has cost this desk repeatedly — a hand-maintained list
describing the past, with no mechanism to notice what it omits. A rule can be
argued with. A list someone forgot to update cannot.

WHAT IT DECIDES, AND WHAT IT DOES NOT
-------------------------------------
Ownership only, via the SAME `is_ownable` the router uses — price floor,
market-cap floor, currency check. One implementation, two callers; a second
copy of "is this a name we would own" is exactly what we removed from routing.

It does NOT judge premium, liquidity or tradeability. The screen already gates
those per-row on the instruments that decide them — open interest, bid-ask
spread, the chain existing at all — and publishes its reason. A name with no
chain is refused by the screen with a stated reason, which is better than being
invisible.

REVIEWABLE, NOT SILENT. universe.py warns that deriving this "would silently
change which names get screened on a live trading surface". So this WRITES A
FILE that is committed and diffed: every addition and removal shows up in a
PR, with the measurement beside it. The owner approves a list, not a function
that ran once.

    tradepro-wheel-universe --dry-run      # report, write nothing
    tradepro-wheel-universe --write        # emit the derived list
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time

from ..quant_engine.options.bucket import (
    MIN_MARKET_CAP_USD,
    MIN_PRICE_USD,
    is_ownable,
)
from ..universe import WHEEL_SLEEVE, universe_path

log = logging.getLogger("tradepro.wheel_universe")

DERIVED_PATH = pathlib.Path(__file__).resolve().parents[2] / "universe" / "wheel_sleeve.json"

#: Be kind to the fundamentals source — this walks ~950 symbols.
PACE_S = 0.35

#: HOW MANY NAMES THE SCREEN ACTUALLY RUNS. Owner's call, 28 Oct 2026:
#: "we can reduce symbols to max 60-100 i think ... we dont have to verstrecth".
#:
#: This is a RUNTIME ceiling before it is a taste judgement. At ~180s/symbol
#: measured, 338 names is about three hours of compute — and this Mac sleeps
#: through the night, so a run that long straddles the sleep boundary, finishes
#: the next morning in a different network state, and loses its publish. Four
#: boards went that way in a week. A hundred names finishes inside the evening
#: and none of that happens.
#:
#: Ranked by market cap, biggest first. The owner chose this over a hybrid with
#: a hand-kept exception list, explicitly for having nothing to maintain —
#: THE COST IS REAL AND IS NOT HIDDEN: the floor lands near $150bn, so MDB
#: ($31bn) and every mid-cap drop off the board. MDB is the name that prompted
#: deriving the universe at all. Raise --max-names, or switch to a hybrid, if
#: that trade stops being worth it.
#:
#: ETFs are kept outside the cap: there are 15, they carry no market cap to
#: rank by, and they are the index and sector underlyings the sleeve holds
#: deliberately.
MAX_NAMES = int(os.environ.get("TRADEPRO_WHEEL_MAX_NAMES", "100"))


def _candidates() -> list[str]:
    u = json.loads(universe_path().read_text())
    return sorted({(s["symbol"] if isinstance(s, dict) else s) for s in u["symbols"]})


#: ETFs ARE NOT COMPANIES AND HAVE NO MARKET CAP.
#:
#: `is_ownable` asks for a market cap, and an ETF has none — yfinance returns
#: marketCap=None for SPY, QQQ, XLE, SLV, GDX. Run naively, this tool DROPPED
#: every ETF on the board, including the index and sector underlyings the
#: sleeve carries deliberately and which the screen already treats specially
#: (they are exempt from the earnings blackout because they have no earnings).
#:
#: They are kept by identity rather than measurement, and that is honest: the
#: question "would I accept being assigned SPY" is not answered by a company
#: size test. Sized differently, judged differently, so not pushed through a
#: gate built for single names.
def _etf_underlyings() -> frozenset[str]:
    from .options_screen import _ETF_UNDERLYINGS
    return _ETF_UNDERLYINGS


def _measure(sym: str) -> tuple[bool, str, dict]:
    """(ownable, why, facts). A fetch failure is a NO with its reason, never a
    silent drop — a name missing from the board for an unstated reason is the
    thing this tool exists to end."""
    import yfinance as yf
    try:
        i = yf.Ticker(sym).info or {}
    except Exception as exc:  # noqa: BLE001
        return False, f"fundamentals unavailable ({type(exc).__name__})", {}
    px = i.get("currentPrice") or i.get("regularMarketPrice")
    mc = i.get("marketCap")
    ccy = i.get("financialCurrency")
    ok, why = is_ownable(price=px, market_cap_usd=mc, reporting_currency=ccy, symbol=sym)
    return ok, why, {"price": px, "market_cap_usd": mc, "reporting_currency": ccy,
                     "name": i.get("shortName")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="emit the derived list")
    ap.add_argument("--dry-run", action="store_true", help="report only (default)")
    ap.add_argument("--limit", type=int, help="stop after N symbols (for a quick look)")
    ap.add_argument("--max-names", type=int, default=MAX_NAMES,
                    help=f"cap the sleeve at N names, biggest first (default {MAX_NAMES})")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # A PARTIAL SCAN IS NOT A UNIVERSE. With --limit the tool reaches only the
    # first N symbols alphabetically, so every name after them looks "dropped"
    # — a trial run reported GOOGL, MSFT and NVDA as removals purely because it
    # stopped at the A's. Writing that would have emptied the board. --limit is
    # for a quick look and cannot produce a list.
    if a.limit and a.write:
        print("--limit is a sample, not a universe: it would report every symbol "
              "it did not reach as a REMOVAL. Refusing to write a partial list.",
              file=sys.stderr)
        return 2

    syms = _candidates()
    if a.limit:
        syms = syms[:a.limit]
    print(f"{len(syms)} tradeable symbol(s); current hand list is {len(WHEEL_SLEEVE)}\n"
          f"gate: price >= ${MIN_PRICE_USD:,.0f}, market cap >= "
          f"${MIN_MARKET_CAP_USD/1e9:,.0f}bn, reported in USD\n")

    etfs = _etf_underlyings()
    keep: list[dict] = []
    drop_reasons: dict[str, int] = {}
    for i, sym in enumerate(syms, 1):
        if sym in etfs:
            keep.append({"symbol": sym, "name": None, "price": None,
                         "market_cap_usd": None, "reporting_currency": None,
                         "why": "ETF — kept by identity; a fund has no market cap "
                                "and is not judged by a company-size gate"})
            continue
        ok, why, facts = _measure(sym)
        if ok:
            keep.append({"symbol": sym, **facts, "why": why})
        else:
            head = why.split("—")[0].strip()[:48]
            drop_reasons[head] = drop_reasons.get(head, 0) + 1
        if i % 50 == 0:
            print(f"  [{i}/{len(syms)}] {len(keep)} qualifying so far")
        time.sleep(PACE_S)

    # ETFs first and unranked (no market cap), then the biggest companies up
    # to the cap. Sorting before the cut is what makes "biggest first" true.
    etf_rows = [r for r in keep if not r.get("market_cap_usd")]
    co_rows = sorted((r for r in keep if r.get("market_cap_usd")),
                     key=lambda r: -r["market_cap_usd"])
    room = max(a.max_names - len(etf_rows), 0)
    capped_out = co_rows[room:]
    keep = etf_rows + co_rows[:room]
    derived = [r["symbol"] for r in keep]
    if capped_out:
        floor = co_rows[room - 1]["market_cap_usd"] / 1e9 if room else 0
        print(f"\ncapped at {a.max_names} names: {len(capped_out)} qualifying "
              f"name(s) left OFF the board, everything below ${floor:,.0f}bn. "
              f"They passed every gate and are excluded for RUNTIME, not merit.")
        notable = [r["symbol"] for r in capped_out[:12]]
        print(f"  biggest excluded: {', '.join(notable)}")
    added = sorted(set(derived) - set(WHEEL_SLEEVE))
    # Under --limit, "removed" is dominated by names never reached, so the
    # comparison is suppressed rather than printed with a caveat nobody reads.
    removed = ([] if a.limit
               else sorted(set(WHEEL_SLEEVE) - set(derived)))

    print(f"\n{len(derived)} qualify of {len(syms)}")
    print(f"  + {len(added)} would be ADDED to the board")
    print(f"  - {len(removed)} currently screened would be DROPPED")
    print("\nwhy the rest were excluded:")
    for r, n in sorted(drop_reasons.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {n:4d}  {r}")
    if added:
        print(f"\nsample of what we have been missing: {', '.join(added[:20])}")
    if a.limit:
        print("\n(--limit: a SAMPLE of the first "
              f"{len(syms)} symbols. Additions and removals are not meaningful "
              "— run without --limit for the real comparison.)")
    if removed:
        # A name LEAVING the board is the more dangerous direction — it could
        # be holding an open position — so it is listed in full, never summarised.
        print(f"\nDROPPED IN FULL (check none holds an open position):\n  {', '.join(removed)}")

    if not a.write:
        print("\n(dry run — nothing written; pass --write to emit the list)")
        return 0

    DERIVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    DERIVED_PATH.write_text(json.dumps({
        "criteria": {"min_price_usd": MIN_PRICE_USD,
                     "min_market_cap_usd": MIN_MARKET_CAP_USD,
                     "reporting_currency": "USD"},
        "counts": {"scanned": len(syms), "qualified": len(derived)},
        "symbols": derived,
        "detail": keep,
    }, indent=1) + "\n")
    print(f"\nwrote {DERIVED_PATH} — commit it so the change is reviewable as a diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
