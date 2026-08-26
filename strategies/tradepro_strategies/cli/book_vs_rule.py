"""Where does the book you actually hold stand against the Swing rule?

Owner drew the boundary: the platform's 244-name universe and his live holdings
are separate concerns. This does not cross it. It signals nothing and
recommends nothing — it answers one question about names he already owns:

    "if the Swing rule looked at this, what would it see?"

That differs from a candidate screen in a way worth stating. The screen
searches for names to BUY. This describes names already HELD, in the same
language as a signal, so a position can be read rather than being invisible
until it becomes a problem.

WHY IT EXISTS. SNDK is the largest single position in the live account —
25 shares at $1,577, about $2,500 under water — and no surface in this platform
had ever looked at it, because it holds 381 stored sessions against a
500-session universe requirement. That requirement is correct: a name with 381
sessions cannot be backtested, so the AUTOMATED sleeve must not trade it. But
"cannot be signalled" and "not worth looking at" are different facts, and
reporting them identically is the failure this codebase keeps producing.

So a name that cannot be graded says exactly that, with the number, instead of
appearing as a blank.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import statistics as st

from ..signals.mean_reversion import (BB_WINDOW, MIN_BARS, SIGMA,
                                      TREND_WINDOW, entry_signal, sma,
                                      stop_price, target_price)
from ..universe import poison_check, universe_symbols
from .build_universe import _load


def assess(sym: str, universe: set[str]) -> dict:
    d = _load(sym)
    if d is None or "close" not in d:
        return {"symbol": sym, "state": "NO DATA",
                "note": "no bars in the store for this symbol"}
    c = d["close"].dropna().tolist()
    dates = [str(x)[:10] for x in d.index]
    if len(c) < MIN_BARS:
        return {"symbol": sym, "state": "CANNOT GRADE",
                "note": (f"{len(c)} stored sessions, the rule needs {MIN_BARS} "
                         f"(a {TREND_WINDOW}-day trend floor plus run-up). No "
                         f"backtest exists for this name — a data limit, not a "
                         f"verdict on the stock.")}
    v = d["volume"].tolist() if "volume" in d.columns else None
    ok, phantom = poison_check(c, v)
    i = len(c) - 1
    w = c[i - BB_WINDOW + 1:i + 1]
    sd = st.pstdev(w)
    mean20 = sum(w) / BB_WINDOW
    s200 = sma(c, i, TREND_WINDOW)
    sig = (mean20 - c[i]) / sd if sd > 0 else 0.0
    # HOW OLD IS THIS PRICE? The first version of this tool printed SNDK at
    # 1596.08 with no date. That is the 21 AUGUST close — the store's last bar
    # for the name — while the stock was actually 1480.77, seven percent lower.
    #
    # SNDK is stale because the nightly harvest scopes to the 244-name universe,
    # and SNDK is not in it. Correct for the automated sleeve, and exactly wrong
    # for a view of what you HOLD: the names most likely to be missing from the
    # harvest are the ones outside the universe, which is precisely the set this
    # tool exists to describe.
    #
    # A price with no date is how Monday's fabricated TXN signal happened. Never
    # show one again.
    last = _dt.date.fromisoformat(dates[i])
    age = (_dt.date.today() - last).days
    stale = age > 4          # a long weekend is 3; more than that is not fresh
    return {"symbol": sym, "state": "GRADED", "bar": dates[i],
            "age_days": age, "stale": stale, "close": c[i],
            "sigma_below": sig, "gap": SIGMA - sig,
            "above_200": c[i] > s200,
            "vs_200_pct": 100 * (c[i] / s200 - 1) if s200 else None,
            "target": target_price(c, i), "stop": stop_price(c[i]),
            "fires": entry_signal(c, i), "in_universe": sym in universe,
            "poison_ok": ok, "phantom": phantom}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", help="comma-separated list of names you hold")
    a = ap.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    uni = set(universe_symbols())

    print(f"YOUR BOOK vs THE SWING RULE — {SIGMA}σ below the {BB_WINDOW}-day mean, "
          f"while above the {TREND_WINDOW}-day average\n")
    print(f"{'sym':<7}{'close':>10}{'as of':>12}{'σ below':>9}{'vs 200d':>9}"
          f"{'watched':>9}  what the rule sees")
    for s in syms:
        r = assess(s, uni)
        if r["state"] != "GRADED":
            print(f"{s:<7}{'—':>10}{'—':>12}{'—':>9}{'—':>9}{'no':>9}  {r['note']}")
            continue
        v200 = f"{r['vs_200_pct']:+.1f}%" if r["vs_200_pct"] is not None else "—"
        if r["stale"]:
            note = (f"STALE — no bar for {r['age_days']} days. This price is NOT "
                    f"current and nothing below it can be trusted.")
            print(f"{s:<7}{r['close']:>10.2f}{r['bar']:>12}{'—':>9}{'—':>9}"
                  f"{'yes' if r['in_universe'] else 'NO':>9}  {note}")
            continue
        if r["fires"]:
            note = "FIRES — this is a live Swing entry"
        elif not r["above_200"]:
            note = "below its 200-day average — the rule refuses to buy here"
        else:
            note = f"in an uptrend, {r['gap']:.2f}σ short of the trigger"
        if not r["poison_ok"]:
            note = f"SUSPECT HISTORY ({r['phantom']} phantom bars) — {note}"
        print(f"{s:<7}{r['close']:>10.2f}{r['bar']:>12}{r['sigma_below']:>9.2f}{v200:>9}"
              f"{'yes' if r['in_universe'] else 'NO':>9}  {note}")
    print("\nA DESCRIPTION of what you hold, not a recommendation. The rule was measured")
    print("on entries it chose itself — it says nothing about whether to keep a position")
    print("it never opened. 'watched = NO' means the name is outside the 244-name")
    print("universe, so the automated sleeve will never act on it — and, because the")
    print("nightly harvest scopes to that same universe, its bars may also be STALE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
