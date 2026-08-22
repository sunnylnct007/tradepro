"""Momentum candidates — the longer-hold sleeve.

Sits ALONGSIDE the Swing (mean-reversion) screen, not instead of it. The two
have deliberately different shapes and the owner asked to run both:

    Swing     4-day holds   62% win   +0.77%/trade   frequent small wins
    Momentum  34-bar holds  47% win   +1.53%/trade   fewer, larger wins

THE EVIDENCE (MOMENTUM_GATES_V2.md, gates committed ca494bf BEFORE the run —
ALL SIX PASSED, the second strategy to clear):

    entry   pullback to the 10-SMA inside an established uptrend
            (20-SMA > 50-SMA, close > 20-SMA, close back at the 10-SMA,
             and close > 200-SMA)
    stop    hard -8% from entry, AND an 8% trailing stop from the peak
    exit    trailing stop, or 60 sessions

    5,815 trades · 47.0% win · +1.53%/trade · 34-bar median hold · worst -14.7%

WHY NOT A BREAKOUT ENTRY. v1 tested 20-day highs and 52-week highs. Both work
(+1.8%/trade) but the pullback beats them on win rate and tail, and the
20-day-high variant failed G1 and G4. Buying the pullback within an uptrend is
the better version of "leverage strength".

WHY THE HOLD IS LONG, and why that was accepted rather than tuned away: v1
showed the trailing stop carries the entire edge and needs 32-35 bars. Forcing
a 10-bar hold costs two thirds of the per-trade return. G3 was changed from
<=20 to <=40 bars as a DECLARED scope change, recorded before the v2 run.

HONEST LIMITS:
  * 53% of these lose. The edge is the average, never any single row.
  * A 34-bar median hold is ~7 WEEKS. This is not the in-and-out trade; that
    is what the Swing screen is for.
  * Same data guards as Swing: settled bars only, wrong-venue quarantine,
    structurally-broken bars rejected.
  * No sentiment or fundamentals filter — neither is backtestable here.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import os

from ..universe import universe_symbols

log = logging.getLogger("tradepro.momentum_candidates")

STOP_PCT = 0.08
TRAIL_PCT = 0.08
MAX_HOLD = 60
MAX_DAY_MOVE = 0.25
BASE_DIR = os.path.expanduser("~/.tradepro/bar_cache/us_etf")


def _tradeable(sym: str) -> bool:
    """DEPRECATED as a universe filter — kept only because tests pin it and it
    still correctly describes instrument TYPE.

    This was string matching standing in for a universe: it excluded futures
    and indices but would happily admit a $0.40 shell trading 900 shares a
    day. The screens now read `universe.load_universe()`, which decides
    membership on price, turnover, history, data quality and coverage. See
    `universe.py` for why that mattered.

    Original doc follows.

    Is this a symbol we could actually place a bracket order on?

    The cache directory is not a tradeable universe. Excluded:
      "."  foreign listings (0700.HK, AIR.PA) — no IBKR API entitlement, and
           the symbol-harmonization work to map them is not done
      "=F" futures (HG=F copper) — different contract mechanics entirely
      "^"  indices (^STOXX) — not directly tradeable at all
      "-USD" crypto pairs

    Caught only because a momentum run surfaced HG=F and ^STOXX as candidates
    with an entry and a stop, as if they were shares.
    """
    if not sym or "." in sym:
        return False
    if "=" in sym or sym.startswith("^"):
        return False
    return not sym.endswith("-USD")


def poison_check(closes) -> tuple[bool, float | None]:
    """Reject a symbol whose history belongs to a DIFFERENT INSTRUMENT.

    The wrong-venue failure (found 22 Aug across VLUE/USMV/QUAL/MTUM/STX): the
    stored series is an LSE-pence listing or the wrong contract entirely. It
    passes every NaN/spike/OHLC guard because the series is internally
    CONSISTENT — it is simply not this security. Signature: a historical price
    level that is a large multiple of the recent level, with no corporate action
    to explain it.

    Owner ruling 22 Aug: "if we get poisoned prices then we better drop that
    symbol, highlight the fact." So this DROPS and REPORTS rather than trying to
    repair — a screen that quietly trades a mis-priced series is worse than one
    that is short a name.

    Mean reversion is the strategy most exposed to this: it buys what looks
    cheap, and a wrong-venue series looks permanently, enormously cheap.
    """
    xs = [x for x in closes if x and x > 0]
    if len(xs) < 80:
        return True, None
    recent = sorted(xs[-60:])[30]        # median of the last 60
    if recent <= 0:
        return True, None
    ratio = max(xs) / recent
    return ratio < 6.0, round(ratio, 1)


def _last_completed_session() -> str:
    """The date whose daily bar can actually be COMPLETE right now (YYYY-MM-DD).

    A US session settles at 20:00 UTC (16:00 ET). Before that, today's bar is
    partial; on a weekend the last completed session is the preceding Friday.
    Deliberately ignores US holidays — that errs toward treating a holiday as a
    session and stepping back one extra bar, which is the SAFE direction: a
    slightly older settled bar beats a partial one.
    """
    now = _dt.datetime.now(_dt.UTC)
    d = now.date()
    if now.hour < 20:                 # today has not settled yet
        d -= _dt.timedelta(days=1)
    while d.weekday() >= 5:           # back off Sat/Sun
        d -= _dt.timedelta(days=1)
    return d.isoformat()


def _pick_signal_index(dates: list[str], last_settled: str) -> int:
    """Index of the newest bar that is safe to compute a signal on.

    The harvest writes a PARTIAL row for the in-progress session, and reading
    one as a close invents signals the backtest never saw. So a bar dated
    AFTER the last completed session is stepped over.

    A bar dated EXACTLY the last completed session is settled and is the one
    we want — the comparison is ">", not ">=". It was ">=" for a day, which
    stepped back an extra session and ran the whole screen on yesterday's
    close: PLTR was published with an entry of 173.96 when the settled 21 Aug
    close was 179.94, and twelve of the thirteen rows were expired triggers
    from a session that had already passed. Shared by both screens so they
    can never disagree about which bar "today" is.
    """
    i = len(dates) - 1
    if i >= 0 and dates[i] > last_settled:
        i -= 1
    return i


def _load(sym: str):
    fs = sorted(glob.glob(f"{BASE_DIR}/{sym}/1d/*.parquet"))
    if not fs:
        return None
    import pandas as pd
    try:
        df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception:
        return None
    df = df[~df.index.duplicated(keep="last")]
    return df if len(df) >= 220 and "open" in df.columns else None


def _vol_ratio(df, i):
    """Entry-bar volume against the mean of the prior 20 bars, or None."""
    if "volume" not in getattr(df, "columns", []) or i < 20:
        return None
    try:
        v = df["volume"].tolist()
        av = sum(v[i - 20:i]) / 20
        return round(v[i] / av, 2) if av else None
    except Exception:  # noqa: BLE001 — context only, never worth failing a row
        return None


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def _entry_signal(c, h, l, i):
    """The v2 entry, evaluated at bar i. One definition, used by BOTH the live
    scan and the per-symbol replay below — if they ever drift, the drill-down
    would be describing a rule the screen does not run."""
    if i < 210 or c[i - 1] <= 0:
        return False
    s200, s50, s20, s10 = sma(c, i, 200), sma(c, i, 50), sma(c, i, 20), sma(c, i, 10)
    prev10 = sma(c, i - 1, 10)
    return bool(s200 > 0 and c[i] > s200 and s20 > s50 and c[i] > s20
                and c[i] <= s10 * 1.005 and c[i - 1] > prev10)


def replay_symbol(c, h, l, dates, upto):
    """Replay this rule over THIS symbol's own history.

    The headline evidence is 5,815 trades across the universe. That says the
    rule works on average; it says nothing about whether it has ever worked on
    MDB. This answers the question the owner actually asks when looking at a
    row — "has this thing ever done anything here?" — and is deliberately
    reported with its own sample size, because 6 trades is not evidence and
    the screen should not let it look like evidence.

    Non-overlapping: a new entry is only taken once the previous trade is out,
    which is how it would actually be traded.
    """
    trades = []
    i = 210
    while i < upto:
        if not _entry_signal(c, h, l, i):
            i += 1
            continue
        entry = c[i]
        peak = entry
        exit_i = None
        why = "open"
        j = i + 1
        while j <= min(upto, i + MAX_HOLD):
            if c[j - 1] > 0 and abs(c[j] / c[j - 1] - 1) > 0.35:
                # A >35% single session inside the hold is a corrupt bar, not a
                # move. Discard the whole trade rather than book the fiction —
                # this exact contamination produced a -98% "worst trade" on the
                # first momentum run.
                why = "discarded"
                break
            if c[j] <= entry * (1 - STOP_PCT):
                exit_i, why = j, "stop"
                break
            peak = max(peak, c[j])
            if c[j] <= peak * (1 - TRAIL_PCT):
                exit_i, why = j, "trail"
                break
            j += 1
        if why == "discarded":
            i = j + 1
            continue
        if exit_i is None:
            if j > i + MAX_HOLD:
                exit_i, why = min(upto, i + MAX_HOLD), "timeout"
            else:
                break  # still open at the end of history — not a completed trade
        trades.append({"entry_date": dates[i], "exit_date": dates[exit_i],
                       "pct": round(100 * (c[exit_i] / entry - 1), 2),
                       "bars": exit_i - i, "exit": why})
        i = exit_i + 1
    if not trades:
        return None
    pcts = sorted(t["pct"] for t in trades)
    wins = [p for p in pcts if p > 0]
    return {
        "trades": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "mean_pct": round(sum(pcts) / len(pcts), 2),
        "median_pct": pcts[len(pcts) // 2],
        "best_pct": pcts[-1],
        "worst_pct": pcts[0],
        "median_bars": sorted(t["bars"] for t in trades)[len(trades) // 2],
        "last_5": trades[-5:][::-1],
        # Said out loud rather than left for the reader to work out.
        "sample_warning": (None if len(trades) >= 15 else
                           f"only {len(trades)} completed trades on this symbol — "
                           f"too few to mean anything on its own; lean on the "
                           f"universe-wide 5,815."),
    }

def scan(symbols: list[str]) -> tuple[list[dict], list[dict]]:
    out: list[dict] = []
    quarantined: list[dict] = []
    for sym in symbols:
        df = _load(sym)
        if df is None:
            continue
        c = df["close"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
        ok_hist, ratio = poison_check(c)
        if not ok_hist:
            quarantined.append({"symbol": sym, "reason": "suspect price history",
                                "detail": f"historical max is {ratio}x the recent median — "
                                          f"consistent with a wrong-venue or wrong-contract series"})
            continue
        dates = [str(x)[:10] for x in df.index]
        i = _pick_signal_index(dates, _last_completed_session())
        if i < 210:
            continue
        if not (h[i] >= l[i] and l[i] - 1e-9 <= c[i] <= h[i] + 1e-9 and c[i] > 0):
            continue
        if c[i - 1] <= 0 or abs(c[i] / c[i - 1] - 1) > MAX_DAY_MOVE:
            continue
        if not _entry_signal(c, h, l, i):
            continue
        s200, s50, s20, s10 = sma(c, i, 200), sma(c, i, 50), sma(c, i, 20), sma(c, i, 10)
        prev10 = sma(c, i - 1, 10)
        trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])) for j in range(i - 13, i + 1)]
        atr = sum(trs) / 14
        hi52 = max(h[max(0, i - 251):i + 1])
        out.append({
            "symbol": sym, "bar": dates[i],
            "close": round(c[i], 2), "entry_hint": round(c[i], 2),
            "stop": round(c[i] * (1 - STOP_PCT), 2),
            "trailing_pct": round(TRAIL_PCT * 100, 1),
            "pct_above_200sma": round(100 * (c[i] / s200 - 1), 1),
            "pct_above_20sma": round(100 * (c[i] / s20 - 1), 1),
            "atr_pct": round(100 * atr / c[i], 2) if c[i] else None,
            "off_52w_high_pct": round(100 * (hi52 - c[i]) / hi52, 1) if hi52 else None,
            "expected_hold_sessions": 34,
            "max_hold_sessions": MAX_HOLD,
            # WHY this row is here, with the numbers behind each clause. A
            # screen that says "it qualified" and not "it qualified because
            # the 20-SMA is 3.1% above the 50-SMA" is asking to be trusted
            # rather than checked.
            "checks": [
                {"label": "Above the 200-day average",
                 "detail": f"close {c[i]:.2f} vs 200-SMA {s200:.2f}",
                 "value": f"+{100 * (c[i] / s200 - 1):.1f}%", "ok": True},
                {"label": "20-day average above the 50-day (uptrend)",
                 "detail": f"20-SMA {s20:.2f} vs 50-SMA {s50:.2f}",
                 "value": f"+{100 * (s20 / s50 - 1):.1f}%", "ok": True},
                {"label": "Still above the 20-day average",
                 "detail": f"close {c[i]:.2f} vs 20-SMA {s20:.2f}",
                 "value": f"+{100 * (c[i] / s20 - 1):.1f}%", "ok": True},
                {"label": "Pulled back TO the 10-day average",
                 "detail": f"close {c[i]:.2f} vs 10-SMA {s10:.2f} — this is the entry trigger",
                 "value": f"{100 * (c[i] / s10 - 1):+.1f}%", "ok": True},
                {"label": "Was above it yesterday (a pullback, not a breakdown)",
                 "detail": f"prior close {c[i - 1]:.2f} vs prior 10-SMA {prev10:.2f}",
                 "value": f"+{100 * (c[i - 1] / prev10 - 1):.1f}%", "ok": True},
            ],
            "levels": {"sma10": round(s10, 2), "sma20": round(s20, 2),
                       "sma50": round(s50, 2), "sma200": round(s200, 2)},
            # How this exact rule has done on THIS symbol, not the universe.
            "history": replay_symbol(c, h, l, dates, i),
            # Entry-bar volume vs its own 20-day average. Shown as CONTEXT and
            # labelled a failed filter: momentum v3 tested it as a gate and it
            # was REJECTED (MOMENTUM_GATES_V3.md, e50cd2a) — the apparent edge
            # exists only in the second half of the record and INVERTS before
            # 2020. Visible so a low-volume entry is not hidden; not acted on,
            # because it did not earn the right to be.
            "volume_vs_20d": _vol_ratio(df, i),
            "chg_5d_pct": (round(100 * (c[i] / c[i - 5] - 1), 1) if i >= 5 and c[i - 5] else None),
        })
    out.sort(key=lambda r: -(r["pct_above_200sma"] or 0))
    return out, quarantined


def build_artifact(rows, universe, quarantined=None) -> dict:
    return {
        "kind": "momentum_candidates",
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "universe": universe,
        "signal_bar": rows[0]["bar"] if rows else _last_completed_session(),
        "settled_bar_only": True,
        "rule": {
            "entry": "pullback to the 10-SMA in an uptrend (20>50 SMA, close>20-SMA, close>200-SMA)",
            "stop": f"hard -{STOP_PCT:.0%} from entry",
            "trailing": f"{TRAIL_PCT:.0%} from the peak close",
            "timeout": f"{MAX_HOLD} sessions",
        },
        # Recomputed on the TRADEABLE universe. The v2 headline (5,815 trades,
        # 47.0% win, +1.53%, worst -14.7%) was measured before _tradeable()
        # existed, so futures, indices and foreign listings were in it. Win
        # rate and mean are better than published; the worst trade is twice as
        # bad, because a -8% stop is checked on the CLOSE and does not survive
        # a gap. Published numbers must be the ones the screen would actually
        # produce — see MOMENTUM_GATES_V3.md.
        "evidence": {
            "gates_file": "MOMENTUM_GATES_V2.md", "gates_commit": "ca494bf",
            "trades": 5396, "win_rate_pct": 48.8, "mean_per_trade_pct": 2.20,
            "median_per_trade_pct": -0.33,
            "worst_trade_pct": -29.7, "median_hold_sessions": 35,
            "note": ("All six v2 gates passed. Restated on the tradeable universe after "
                     "the earlier figures were found to include futures and indices. "
                     "The hold is long by design — the trailing stop carries the edge "
                     "and needs 32-35 bars."),
        },
        "limits": [
            "51% of these lose, and the MEDIAN trade loses 0.33%. The positive average is "
            "carried by the winners in the tail, not by the typical trade.",
            "Worst historical trade: -29.7%. The -8% stop is checked on the close, so it "
            "does NOT protect against an overnight gap.",
            "A 34-bar median hold is about SEVEN WEEKS. This is not the in-and-out trade; use Swing for that.",
            "No sentiment or fundamentals filter — neither is backtestable on this data.",
        ],
        "quarantined": quarantined or [],
        "count": len(rows),
        "candidates": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", default="momentum")
    ap.add_argument("--symbols")
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols
            else universe_symbols())
    rows, quarantined = scan(syms)
    art = build_artifact(rows, args.universe, quarantined)
    print(f"momentum candidates — {art['as_of_utc'][:19]}Z · scanned {len(syms)} · {len(rows)} candidate(s)\n")
    if rows:
        print(f"{'sym':<7}{'close':>9}{'stop':>9}{'trail':>7}{'vs200':>8}{'vs20':>7}{'ATR%':>7}{'off52wHi':>10}")
        for r in rows:
            print(f"{r['symbol']:<7}{r['close']:>9.2f}{r['stop']:>9.2f}{r['trailing_pct']:>6.0f}%"
                  f"{r['pct_above_200sma']:>7.1f}%{r['pct_above_20sma']:>6.1f}%{r['atr_pct'] or 0:>6.1f}%"
                  f"{r['off_52w_high_pct'] or 0:>9.1f}%")
    if quarantined:
        print(f"\n⚠ {len(quarantined)} symbol(s) DROPPED for suspect price history")
    if not rows:
        print("none on the last settled bar")
    if args.push:
        try:
            import requests
            from .push_to_api import load_credentials
            base, token = load_credentials()
            if base:
                r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups",
                                  json={"universe": args.universe, "label": "latest",
                                        "uploaded_by": os.uname().nodename, "artifact": art},
                                  headers={"Authorization": f"Bearer {token}"} if token else {},
                                  timeout=45)
                print(f"\npush → HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            log.warning("push failed: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
