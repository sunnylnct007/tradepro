"""What do we actually know about THIS name? — decision support, not a system.

WHY THIS EXISTS
---------------
Owner, 28 Aug 2026: *"I am manually doing swing trading on MU and making money
but technically that is not a right one"* and *"market condition, sector
condition, fundamental — so many things are there"*.

Both are correct, and no screen served either. The Swing rule is price-only: a
2.5-sigma drop above the 200-day mean, nothing else. It fires on MU about ONCE
A YEAR (14 signals in 16 years, +6.18%/trade). Someone trading MU actively is
not running that strategy, and quoting the rule's backtest as evidence for
their trading was a category error.

This is the opposite of the screen. It does not rank, score or recommend. It
states what is known about one name, what is NOT known, and where the rule
stands — so the judgement stays with the owner and has the context behind it.

WHAT IT REFUSES TO DO
---------------------
No verdict, no conviction score, no BUY/SELL. Every line is a measurement or an
explicit gap. A tool that scores a name invites the score to be trusted.

WHAT IS MISSING, PRINTED ON EVERY RUN
-------------------------------------
No fundamentals feed and no sector feed exist in this repo. Relative strength
versus SPY is a PROXY for market fit, not a sector read. Earnings history is
effectively post-2020. A blank space reads as "fine", so the gaps are named.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
EARN_PATH = os.path.expanduser("~/.tradepro/research/earnings_history.json")


def _load(sym: str):
    fs = sorted(glob.glob(f"{BASE}/{sym}/1d/*.parquet"))
    if not fs:
        return None
    import pandas as pd
    try:
        df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception:
        return None
    return df[~df.index.duplicated(keep="last")]


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def _rule_history(c, h, l, o, d):
    """How has the rule ACTUALLY done on this name — the per-name record."""
    from ..signals.mean_reversion import (entry_signal, BB_WINDOW, STOP_PCT,
                                          MAX_HOLD, MIN_BARS)
    out = []
    i = MIN_BARS
    while i < len(c) - 1:
        if not entry_signal(c, i):
            i += 1
            continue
        entry = c[i]; stop = entry * (1 - STOP_PCT); res = None
        for j in range(i + 1, min(len(c), i + MAX_HOLD + 1)):
            tgt = _sma(c, j, BB_WINDOW)
            hs, ht = l[j] <= stop, h[j] >= tgt
            if hs and ht: res = (100 * (min(stop, o[j]) / entry - 1), j - i); break
            if ht:        res = (100 * (max(tgt, o[j]) / entry - 1), j - i); break
            if hs:        res = (100 * (min(stop, o[j]) / entry - 1), j - i); break
        if res is None:
            j = min(len(c) - 1, i + MAX_HOLD)
            res = (100 * (c[j] / entry - 1), j - i)
        out.append((d[i], res[0]))
        i += max(1, res[1]) + 1
    return out


def describe(sym: str, spy) -> None:
    from ..signals.mean_reversion import (SIGMA, BB_WINDOW, TREND_WINDOW,
                                          MIN_BARS, entry_signal,
                                          target_price, stop_price)
    print(f"\n{'=' * 68}\n{sym}")
    df = _load(sym)
    if df is None:
        print("  NO BARS AT ALL. Nothing here can be judged — not a weak read,")
        print("  an absent one. Harvest it before trading it.")
        return

    c = df["close"].tolist(); h = df["high"].tolist()
    l = df["low"].tolist(); o = df["open"].tolist()
    d = [str(x)[:10] for x in df.index]

    # ── 1. CAN WE JUDGE IT AT ALL ────────────────────────────────────────
    # This block exists because of SNDK: 383 bars from Feb 2025, never a
    # signal, and the owner tried to trade it. Nothing on screen said the
    # name was unjudgeable — it just looked quiet.
    yrs = len(c) / 252
    print(f"  history      {d[0]} → {d[-1]}   {len(c)} bars (~{yrs:.1f}y)")
    if len(c) < MIN_BARS:
        print(f"  ✗ UNJUDGEABLE — the rule needs {MIN_BARS} bars for its 200-day floor.")
        print("    No signal here means 'cannot say', NOT 'no opportunity'.")
        return
    # Clearing MIN_BARS is not the same as being judgeable. SNDK has 383 bars:
    # enough to COMPUTE a 200-day mean, but the mean is then built from nearly
    # the whole record, and there is no independent history left to judge the
    # rule's behaviour on the name. The owner traded it and nothing on screen
    # said the silence meant "cannot say" rather than "no opportunity".
    if yrs < 3:
        print(f"  ⚠ THIN — {yrs:.1f}y of history. The 200-day floor consumes most of it,")
        print("    so the rule has had almost no independent record here. Silence from")
        print("    this name means CANNOT SAY, not 'no opportunity'.")
    if d[0] > "2020-02-01":
        print("  ⚠ never traded through 2020 — no crash in this name's record.")

    # ── 2. WHERE THE RULE STANDS ─────────────────────────────────────────
    i = len(c) - 1
    w = c[i - BB_WINDOW + 1:i + 1]
    m = sum(w) / len(w); sd = st.pstdev(w)
    sig = (m - c[i]) / sd if sd > 0 else 0.0
    s200 = _sma(c, i, TREND_WINDOW)
    fires = entry_signal(c, i)
    print(f"\n  RULE ({d[i]} close {c[i]:.2f})")
    # Sign convention: sigma is measured as (mean - close)/sd, so a NEGATIVE
    # value means the close sits ABOVE the mean. Printing "-0.28σ below the
    # mean" for a name trading above it is the kind of line that gets read
    # backwards at speed, so the word is chosen from the sign.
    where = "below" if sig >= 0 else "ABOVE"
    print(f"    {abs(sig):.2f}σ {where} the 20-day mean {m:.2f}   (fires at {SIGMA}σ below)")
    print(f"    200-SMA {s200:.2f} — {'ABOVE' if c[i] > s200 else 'BELOW (trend floor blocks)'}")
    if fires:
        print(f"    ✓ FIRES — target {target_price(c, i):.2f}  stop {stop_price(c, i):.2f}")
    else:
        need = m - SIGMA * sd
        print(f"    · does not fire — would need a close near {need:.2f} "
              f"({100 * (need / c[i] - 1):+.1f}% from here)")

    # ── 3. THE PER-NAME RECORD ───────────────────────────────────────────
    hist = _rule_history(c, h, l, o, d)
    if hist:
        v = [x[1] for x in hist]
        print(f"\n  THIS NAME'S RULE RECORD   {len(v)} signals over ~{yrs:.0f}y "
              f"(~{len(v) / max(yrs, 0.1):.1f}/yr)")
        print(f"    win {100 * sum(1 for x in v if x > 0) / len(v):.0f}%   "
              f"mean {sum(v) / len(v):+.2f}%   best {max(v):+.1f}%   worst {min(v):+.1f}%")
        print("    last: " + ", ".join(f"{dt} {r:+.1f}%" for dt, r in hist[-4:]))
        if len(v) < 10:
            print(f"    ⚠ {len(v)} signals is a small sample — treat the average loosely.")
    else:
        print("\n  THIS NAME'S RULE RECORD   never fired in the whole record.")

    # ── 4. MARKET FIT (proxy, and labelled as one) ───────────────────────
    if spy is not None:
        sc, sd_ = spy
        smkt = "RISK-ON (SPY above its 200-day)" if sc[-1] > _sma(sc, len(sc) - 1, TREND_WINDOW) \
            else "RISK-OFF (SPY BELOW its 200-day)"
        print(f"\n  MARKET       {smkt}")
        n = min(63, len(c) - 1, len(sc) - 1)
        if n > 20:
            rs = (c[-1] / c[-1 - n] - 1) - (sc[-1] / sc[-1 - n] - 1)
            print(f"    vs SPY over {n} sessions: {100 * rs:+.1f}pt "
                  f"({'outperforming' if rs > 0 else 'lagging'})")
            print("    NOTE: relative strength is a PROXY. There is no sector feed here,")
            print("    so this cannot tell you whether the SECTOR or the name is moving.")

    # ── 5. EARNINGS ──────────────────────────────────────────────────────
    # Measured 28 Aug (EARNINGS_BOUNCE_GATES_V1, commit 512f5af): signals near
    # an earnings event won 59.3% versus 69.6% otherwise. An earnings drop is
    # information; the rule buys noise. So this is a stand-aside flag.
    try:
        earn = json.load(open(EARN_PATH)).get(sym) or []
    except Exception:
        earn = []
    print("\n  EARNINGS")
    if not earn:
        print("    none held for this symbol — proximity CANNOT be checked.")
    else:
        today = d[-1]
        past = [x for x in earn if x <= today]
        nxt = [x for x in earn if x > today]
        print(f"    last reported {past[-1] if past else 'unknown'}"
              + (f"   next {nxt[0]}" if nxt else "   next unknown"))
        import datetime as _dt
        if past:
            gap = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(past[-1])).days
            if gap <= 5:
                print(f"    ⚠ reported {gap}d ago — measured: signals near earnings win 59% vs 70%.")
                print("      An earnings drop is information, not noise. Stand aside.")
        if nxt:
            gap = (_dt.date.fromisoformat(nxt[0]) - _dt.date.fromisoformat(today)).days
            if gap <= 10:
                print(f"    ⚠ reports in {gap}d — a hold opened now runs into the print.")

    print("\n  NOT KNOWN HERE: fundamentals (no feed), sector (no feed),")
    print("  analyst revisions, guidance. This is price + earnings dates only.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Context for names YOU pick. States what is known and what is not.")
    ap.add_argument("symbols", nargs="+")
    args = ap.parse_args()
    sdf = _load("SPY")
    spy = (sdf["close"].tolist(), None) if sdf is not None else None
    for s in args.symbols:
        describe(s.strip().upper(), spy)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
