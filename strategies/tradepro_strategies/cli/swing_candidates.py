"""Swing candidates — the one screen that earned its place.

WHAT THIS IS
------------
The morning list: symbol, ENTRY, TARGET, STOP. Placeable as a single bracket
order, which is how the owner actually trades ("I can get into market in the
morning placing order at certain price and booking the profit target in that
order").

THE EVIDENCE BEHIND IT (MEAN_REVERSION_GATES_V1.md, gates committed 6c9f330
BEFORE the run; parameters chosen by a 24-combination sweep):

    entry   close < 2.5 sigma below the 20-day mean, while ABOVE the 200-SMA
    target  the 20-day mean
    stop    -8%
    timeout 10 sessions

    2,413 trades · 62.4% win · +0.77%/trade · worst -12.5% · tail 26%
    high-ATR subset (ATR>=4%): +1.91%/trade  <- the semis case

Two findings that shaped it, both counter-intuitive:
  * A 5-day-SMA target gives a 76% win rate and +0.06%/trade — it books tiny
    gains and keeps full losses. High win rate, no money. Rejected.
  * Requiring capitulation VOLUME (>1.5x average) costs 42% of the trades and
    does not improve the edge. Not used.

WHY IT IS NOT THE ICHIMOKU STRATEGY
-----------------------------------
Different family, deliberately. Ichimoku's edge IS the long hold and the tail
(41+ bar holds average +16.24%; the top 1% of trades carry 54% of all profit).
It cannot produce quick in-and-out trades — every attempt to make it destroyed
it. This is the swing nest; the two run side by side.

HONEST LIMITS, stated here rather than discovered later:
  * Data guard is applied at screen time (structurally broken bars rejected,
    >25% single-session moves refused). Mean reversion is UNIQUELY exposed to
    corrupt bars because its premise is BUY THE CRASH — a fake -60% print is
    exactly what it would buy.
  * No sentiment or fundamentals filter. Neither can be backtested here (the
    EPS store is 3 months deep), so neither is claimed. They can be layered
    live as an operator veto.
  * The worst historical trade was -34% pre-stop (CRWD, the 2024 global outage
    day). The stop caps it; nothing prevents an event.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import os

log = logging.getLogger("tradepro.swing_candidates")

SIGMA = 2.5
BB_WINDOW = 20
STOP_PCT = 0.08
MAX_HOLD = 10
MAX_DAY_MOVE = 0.25
BASE_DIR = os.path.expanduser("~/.tradepro/bar_cache/us_etf")

# Tiering from the per-name study: mega-caps and sector ETFs post the highest
# win rates (XLI 86%, V 74%, MSFT 80%); semis/high-beta post the biggest moves
# (+1.91%/trade on ATR>=4%). Sized differently, so labelled differently.
CORE = {"AAPL","MSFT","GOOGL","AMZN","V","MA","JPM","BAC","JNJ","PG","KO","XOM","CVX",
        "UNH","HD","WMT","COST","MRK","ABBV","LLY","T","VZ","DIS","CSCO","ORCL","ADBE"}
ETFS = {"SPY","QQQ","IVV","VTI","DIA","IWM","XLK","XLF","XLI","XLE","XLV","XLP","XLU",
        "XLB","XLY","SOXX","ITA","GDX","GLD","SLV","TLT","AGG","EEM","EFA","KRE"}




def _tradeable(sym: str) -> bool:
    """Is this a symbol we could actually place a bracket order on?

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
        v = df["volume"].tolist() if "volume" in df.columns else [0] * len(c)
        dates = [str(x)[:10] for x in df.index]
        i = len(c) - 1
        # ── SETTLED-BAR ONLY ──────────────────────────────────────────────
        # The backtest signalled on a SETTLED close and filled the next open.
        # A daily bar for TODAY is PARTIAL until the session ends — the harvest
        # writes "today partial" rows during the day — and a name down 3% at
        # 11am may close flat. Computing on that produces signals the backtest
        # never saw and cannot vouch for. The same trap the Ichimoku config
        # guards with entry_settled_bar_only=True.
        i = _pick_signal_index(dates, _last_completed_session())
        # NOTE: this length guard used to sit INSIDE the step-back branch, so
        # it only ran on the days the screen stepped back a bar — a symbol with
        # too little history could reach the indicator maths unguarded.
        if i < 210:
            continue
        # ── data guard (see module docstring) ──────────────────────────────
        if not (h[i] >= l[i] and l[i] - 1e-9 <= c[i] <= h[i] + 1e-9 and c[i] > 0):
            continue
        if c[i-1] <= 0 or abs(c[i] / c[i-1] - 1) > MAX_DAY_MOVE:
            continue
        if len(c) < 210:
            continue
        sma200 = sum(c[i-199:i+1]) / 200
        w = c[i-BB_WINDOW+1:i+1]
        mean20 = sum(w) / BB_WINDOW
        sd = (sum((x - mean20) ** 2 for x in w) / BB_WINDOW) ** 0.5
        if sd <= 0 or sma200 <= 0:
            continue
        lower = mean20 - SIGMA * sd
        if not (c[i] > sma200 and c[i] < lower):
            continue
        trs = [max(h[j]-l[j], abs(h[j]-c[j-1]), abs(l[j]-c[j-1])) for j in range(i-13, i+1)]
        atr = sum(trs) / 14
        atr_pct = 100 * atr / c[i] if c[i] else 0
        hi52 = max(h[max(0, i-251):i+1])
        target = mean20
        stop = c[i] * (1 - STOP_PCT)
        rr = ((target - c[i]) / (c[i] - stop)) if c[i] > stop else None
        tier = "core" if sym in CORE or sym in ETFS else ("high-beta" if atr_pct >= 4 else "standard")
        out.append({
            "symbol": sym,
            "tier": tier,
            "bar": dates[i],
            "close": round(c[i], 2),
            "entry_hint": round(c[i], 2),
            "target": round(target, 2),
            "stop": round(stop, 2),
            "target_pct": round(100 * (target / c[i] - 1), 2),
            "reward_risk": round(rr, 2) if rr else None,
            "sigma_below": round((mean20 - c[i]) / sd, 2),
            "atr_pct": round(atr_pct, 2),
            "pct_above_200sma": round(100 * (c[i] / sma200 - 1), 1),
            "off_52w_high_pct": round(100 * (hi52 - c[i]) / hi52, 1) if hi52 else None,
            "volume_vs_20d": (round(v[i] / (sum(v[i-19:i+1]) / 20), 2)
                              if sum(v[i-19:i+1]) > 0 else None),
            "max_hold_sessions": MAX_HOLD,
        })
    # Best reward:risk first — the number that decides whether a bracket is worth placing.
    out.sort(key=lambda r: -(r["reward_risk"] or 0))
    return out, quarantined


def build_artifact(rows: list[dict], universe: str,
                   quarantined: list[dict] | None = None) -> dict:
    return {
        "kind": "swing_candidates",
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "universe": universe,
        "rule": {
            "entry": f"close < {SIGMA} sigma below the {BB_WINDOW}-day mean, while above the 200-SMA",
            "target": f"the {BB_WINDOW}-day mean",
            "stop": f"-{STOP_PCT:.0%}",
            "timeout": f"{MAX_HOLD} sessions",
        },
        "evidence": {
            "gates_file": "MEAN_REVERSION_GATES_V1.md",
            "gates_commit": "6c9f330",
            "trades": 2413, "win_rate_pct": 62.4, "mean_per_trade_pct": 0.77,
            "worst_trade_pct": -12.5, "median_hold_sessions": 4,
            "note": ("Gates committed to git BEFORE the run. High-ATR names (>=4%) "
                     "averaged +1.91%/trade. A 5-day-SMA target scored 76% win but only "
                     "+0.06%/trade and was rejected; volume filtering added nothing."),
        },
        "limits": [
            "No sentiment or fundamentals filter — neither is backtestable here (EPS store is 3 months deep), so neither is claimed.",
            "Data guard applied at screen time; mean reversion is uniquely exposed to corrupt bars because it buys crashes.",
            "Worst historical trade was -34% pre-stop (CRWD, 2024 outage). The stop caps it; nothing prevents an event.",
        ],
        "signal_bar": rows[0]["bar"] if rows else _last_completed_session(),
        "settled_bar_only": True,
        "quarantined": quarantined or [],
        "count": len(rows),
        "candidates": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", default="swing")
    ap.add_argument("--symbols", help="comma list (default: everything cached)")
    ap.add_argument("--push", action="store_true", help="POST to /api/ingest/today-setups")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else
            [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)])
    rows, quarantined = scan(syms)
    art = build_artifact(rows, args.universe, quarantined)

    if args.json:
        print(json.dumps(art, indent=1))
    else:
        print(f"swing candidates — {art['as_of_utc'][:19]}Z · scanned {len(syms)} · {len(rows)} candidate(s)\n")
        if rows:
            print(f"{'sym':<7}{'tier':<11}{'close':>9}{'target':>9}{'stop':>9}{'upside':>8}{'R:R':>6}{'sigma':>7}{'ATR%':>7}")
            for r in rows:
                print(f"{r['symbol']:<7}{r['tier']:<11}{r['close']:>9.2f}{r['target']:>9.2f}"
                      f"{r['stop']:>9.2f}{r['target_pct']:>7.1f}%{r['reward_risk'] or 0:>6.2f}"
                      f"{r['sigma_below']:>7.2f}{r['atr_pct']:>6.1f}%")
        if quarantined:
            print(f"\n⚠ {len(quarantined)} symbol(s) DROPPED for suspect price history:")
            for q in quarantined:
                print(f"   {q['symbol']:<7} {q['detail']}")
        if not rows:
            print("none today — the screen is selective (~7 signals/week across 257 names)")

    if args.push:
        try:
            import requests
            from .push_to_api import load_credentials
            base, token = load_credentials()
            if not base:
                log.warning("no API base — not pushed")
                return 0
            r = requests.post(
                f"{base.rstrip('/')}/api/ingest/today-setups",
                json={"universe": args.universe, "label": "latest",
                      "uploaded_by": os.uname().nodename, "artifact": art},
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=45)
            print(f"\npush → HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001 — a push failure must not lose the scan
            log.warning("push failed: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
