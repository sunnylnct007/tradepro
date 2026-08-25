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

from ..universe import universe_symbols, poison_check, volume_ratio

log = logging.getLogger("tradepro.swing_candidates")

# THE RULE'S CONSTANTS ARE IMPORTED, NOT RETYPED.
#
# These were four local literals, and MAX_HOLD was still 10 after the shared
# module moved to 20 — so this screen would have advertised "exit by 10
# sessions" on every row while the live strategy held for 20. A screen and the
# strategy it describes disagreeing about the rule is the worst version of
# this bug, because the number is printed in front of a human.
#
# Third duplicated constant found today, after poison_check (three near-copies)
# and the backtest harness's own MAX_HOLD. There is one definition now.
from ..signals.mean_reversion import (SIGMA, BB_WINDOW, STOP_PCT,  # noqa: E402
                                      MAX_HOLD)
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


def latest_price(sym: str) -> dict | None:
    """The most recent price we hold, from the 5-MINUTE lane.

    Owner, repeatedly: "I need latest prices." The screens showed the settled
    close and nothing else, so a plan built on Friday's 29.71 stayed on screen
    while the stock traded at 28.58 — a 3.8% gap between the number displayed
    and the number you would actually pay.

    The daily harvest runs once at 21:30; the 5m harvest runs every 30 minutes.
    So this is at most half an hour stale, needs no IBKR quote, and therefore
    cannot take the single market-data session.

    DISPLAY ONLY. The signal stays on the settled bar because that is what the
    backtest measured — a name down 3% at 11am may close flat. This exists so
    the ENTRY you are quoted is not silently out of date.
    """
    import glob as _g
    try:
        fs = sorted(_g.glob(f"{BASE_DIR}/{sym}/5m/*.parquet"))
        if not fs:
            return None
        import pandas as _pd
        df = _pd.concat([_pd.read_parquet(f) for f in fs])
        df = df[~df.index.duplicated(keep="last")]
        if df.empty:
            return None
        day = str(df.index[-1])[:10]
        rows = df[[str(x)[:10] == day for x in df.index]]
        if rows.empty:
            return None
        return {"price": round(float(rows["close"].iloc[-1]), 2),
                "as_of": str(rows.index[-1])[:19],
                "session": day,
                "high": round(float(rows["high"].max()), 2),
                "low": round(float(rows["low"].min()), 2)}
    except Exception:  # noqa: BLE001 — a missing price must never drop a row
        return None

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
        ok_hist, ratio = poison_check(
            c, df["volume"].tolist() if "volume" in df.columns else None)
        if not ok_hist:
            quarantined.append({"symbol": sym, "reason": "suspect price history",
                                "detail": f"{ratio} phantom bars (unchanged close on ZERO volume) — "
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
        # volume_vs_20d is CONTEXT, and it is WITHHELD rather than guessed when
        # the stored volume series changes units mid-window. See
        # universe.volume_ratio for why a ratio is not immune to that.
        _vol, _vol_why = volume_ratio(v, i)
        out.append({
            "latest": latest_price(sym),   # display-only; see latest_price()
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
            "volume_vs_20d": _vol,
            "volume_vs_20d_unavailable": _vol_why,
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
        # Restored 23 Aug: an earlier rewrite of the evidence block dropped
        # this, so the screen rendered an undefined signal bar and a dated
        # archive had nothing to key on.
        "signal_bar": rows[0]["bar"] if rows else _last_completed_session(),
        "settled_bar_only": True,
        "rule": {
            "entry": f"close < {SIGMA} sigma below the {BB_WINDOW}-day mean, while above the 200-SMA",
            "target": f"the {BB_WINDOW}-day mean",
            "stop": f"-{STOP_PCT:.0%}",
            "timeout": f"{MAX_HOLD} sessions",
        },
        # FIRST REPRODUCIBLE MEASUREMENT — harness committed at
        # backtests/studies/mean_reversion_v2.py, run 22 Aug 2026 against the
        # 89-name defined universe on the cleaned store.
        #
        # These supersede v1's figures (2,413 trades / 62.4% / +0.77% / worst
        # -12.5% / hold 4). Those were measured over a symbol list that included
        # futures and indices, on data since found to contain wrong-contract
        # series, by a harness that no longer exists. Their hold figure could
        # not be reproduced under ANY of the four exit conventions.
        #
        # The worst trade is honest now: stops fill at min(stop, open), because
        # a gap through a stop does not fill at the stop. Modelling the fill at
        # the trigger produced exactly -8.0% in every variant — a stop that
        # never slips, which does not exist.
        "evidence": {
            "gates_file": "MEAN_REVERSION_GATES_V1.md",
            "gates_commit": "6c9f330",
            "harness": "backtests/studies/mean_reversion_v2.py",
            "trades": 2310, "win_rate_pct": 72.8, "mean_per_trade_pct": 1.06,
            "median_per_trade_pct": 1.78,
            "worst_trade_pct": -23.9, "median_hold_sessions": 7,
            "note": ("ALL SIX gates pass, including G4 (top-1% tail 18.2% of net vs the "
                     "25% ceiling) which v1 FAILED at 26%. Re-measured on the corrected "
                     "244-name universe after IBKR volume was found stored in 100-share "
                     "lots. Also passes a two-split test "
                     "that rejected three other candidates the same day: both halves of "
                     "history and both halves of the universe hold 65-67% win and a "
                     "positive mean. All four exit conventions pass every gate, so the "
                     "result does not depend on picking one."),
        },
        # MEASURED REGIME DEPENDENCE, re-measured on the corrected 244-name
        # universe after IBKR volume was found to be stored in 100-share lots.
        # The earlier 89-name reading said this LOSES money below the 200-SMA
        # (-0.48%/trade). On the full universe it does not — it earns +0.24%,
        # which is weak but positive. The claim was too strong and is corrected
        # here rather than quietly softened.
        #   SPY above its 200-SMA  2,016 trades  65.5% win  +0.93%/trade
        #   SPY below its 200-SMA    235 trades  60.0% win  +0.24%/trade
        #   SPY drawdown 5-15%       471 trades  68.8% win  +1.30%/trade  <- best
        #   SPY drawdown over 15%    133 trades  53.4% win  -0.28%/trade  <- only losing cell
        "regime_dependence": {
            "above_200sma": {"trades": 2016, "win_pct": 65.5, "mean_pct": 0.93},
            "below_200sma": {"trades": 235, "win_pct": 60.0, "mean_pct": 0.24},
            "drawdown_5_15": {"trades": 471, "win_pct": 68.8, "mean_pct": 1.30},
            "drawdown_over_15": {"trades": 133, "win_pct": 53.4, "mean_pct": -0.28},
        },
        "limits": [
            "THE EDGE THINS BADLY IN FALLING MARKETS. Below the S&P's 200-day average it earns "
            "+0.24% a trade against +0.93% above it — still positive, but a quarter of the "
            "strength. In a drawdown deeper than 15% it turns negative at -0.28% on 133 trades. "
            "Ordinary pullbacks of 5-15% are its best conditions (+1.30%).",
            "Recent results flatter it. The last months show far higher win rates while the S&P "
            "rose 19% with a 9% maximum drawdown — that is the regime, not the edge.",
            "35% of these lose. The edge is the average across many, never any single row.",
            "Settled bars only — the signal is computed on a closed session, so it cannot "
            "chase an intraday move.",
            "Symbols with a suspect price series are DROPPED and named below, never silently "
            "included.",
        ],
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
            universe_symbols())
    rows, quarantined = scan(syms)
    art = build_artifact(rows, args.universe, quarantined)

    if args.json:
        print(json.dumps(art, indent=1))
    else:
        # Say WHICH SESSION the signals are computed on, not just when the job
        # ran. The two differ by design — the rule reads a SETTLED close, so a
        # midday run on Monday is necessarily working from Friday's bar — and
        # printing only the run timestamp made that look like stale output
        # rather than the intended behaviour. It cost a "why is this as of
        # Friday?" on go-live morning, which is a fair question to ask of a
        # line that stamps itself with today's date and says nothing else.
        print(f"swing candidates — signals computed on the "
              f"{art.get('signal_bar', 'unknown')} CLOSE "
              f"(settled bars only; entry at the next open)\n"
              f"  run {art['as_of_utc'][:19]}Z · scanned {len(syms)} · "
              f"{len(rows)} candidate(s)\n")
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
            print("none today — the screen is selective (~7 signals/week across the universe)")

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
            # ALSO PUBLISH UNDER A DATED LABEL, because "latest" is overwritten
            # every run. By week two there would be no record of what was
            # published on day one — and forward-test gates F2 (every fill
            # traces to a signal) and F3 (slippage vs the published entry) both
            # need exactly that.
            #
            # This is the ONLY route that persists. Checked against the live
            # OMS: it stores 19 fields and neither `tag` nor the advisory
            # risk_* prices is among them, so a reference price attached to an
            # ORDER is silently dropped. A dated artifact joins to a fill on
            # (symbol, date) with no backend change.
            # Archived as a DATED UNIVERSE, not a dated label. The backend
            # routes GET /{universe}/latest and nothing else, so a dated LABEL
            # is stored and then unreadable — POST 200, GET 404. Verified
            # before relying on it. Putting the date in the universe name keeps
            # it inside the one route that exists, with no backend change hours
            # before go-live.
            _dated = art.get("signal_bar") or art["as_of_utc"][:10]
            _a = requests.post(
                f"{base.rstrip('/')}/api/ingest/today-setups",
                json={"universe": f"{args.universe}-{_dated}", "label": "latest",
                      "uploaded_by": os.uname().nodename, "artifact": art},
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=45)
            print(f"archived as {args.universe}-{_dated} → HTTP {_a.status_code}")
        except Exception as exc:  # noqa: BLE001 — a push failure must not lose the scan
            log.warning("push failed: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
