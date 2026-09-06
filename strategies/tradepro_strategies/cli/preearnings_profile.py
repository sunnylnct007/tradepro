"""tradepro-preearnings-profile — one symbol's behaviour around its own prints.

    uv run tradepro-preearnings-profile --symbol MU

Written for §19.1 of the MU pre-earnings spec (6 Sep 2026), which requires a
review of comparable pre-earnings windows BEFORE any live order staging. This
is CALIBRATION, not proof: a per-symbol history holds ~40 events, a sample
this desk has refused to *conclude* from every time it has been offered. It
answers "where do the levels and risks sit for THIS name", never "does the
strategy work" — that claim needs the population-level machinery
(tradepro-earnings-backtest) and pre-registered gates.

## What a daily-bar study can and cannot see

The spec's entries confirm on completed 15-MINUTE reclaims. We hold no years
of 15m history, so the entry analogue here is daily: a session that TOUCHES
the 20-day EMA from above (low <= EMA20) followed by the first session that
CLOSES back above it. That is a slower, blunter trigger than the spec's — it
measures the terrain, not the vehicle. Stated on every output.

Alignment: MU reports AMC (owner-confirmed for 2026-09-30; historically
consistent). D = the last session at or before the report date; its close is
the final pre-print price. T = the next session, the first post-print one.

Reads the bar store DISK-ONLY, same as every study since the TSLA damage.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
from pathlib import Path

WINDOW = 10          # pre-print sessions scanned for the pullback setup
EMA_N = 20
SMA_N = 50
ATR_N = 14


def _load_ohlc(sym: str):
    from .earnings_backtest import _STORE, load_closes  # noqa: F401 — registers plugins
    from ..bar_cache import asset_classes as _p  # noqa: F401
    from ..bar_cache.store import BarStore
    from ..ibkr_bars import route_asset_class
    from pathlib import Path as _P
    store = BarStore(base_dir=_P("~/.tradepro/bar_cache").expanduser())
    end = _dt.datetime.now(_dt.UTC)
    start = end - _dt.timedelta(days=4200)
    res = store.get(sym, route_asset_class(sym), "1d", start, end,
                    allow_partial=True, skip_fetch=True,
                    fetched_by="preearnings_profile")
    df = getattr(res, "df", res)
    if df is None or df.empty:
        raise SystemExit(f"ERROR: no bars on disk for {sym}")
    df = df.dropna(subset=["close"])
    return ([str(x)[:10] for x in df.index],
            [float(x) for x in df["open"]], [float(x) for x in df["high"]],
            [float(x) for x in df["low"]], [float(x) for x in df["close"]])


def _ema(closes, n=EMA_N):
    out, k, e = [], 2 / (n + 1), None
    for c in closes:
        e = c if e is None else c * k + e * (1 - k)
        out.append(e)
    return out


def _atr(highs, lows, closes, n=ATR_N):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    out, a = [], None
    for i, tr in enumerate(trs):
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a)
    return out


def _mean(xs):
    return statistics.fmean(xs) if xs else None


def pct(x, d=1):
    return f"{100 * x:+.{d}f}%" if x is not None else "—"


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-preearnings-profile")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    sym = args.symbol.upper()

    import bisect

    import requests

    from .push_to_api import load_credentials
    base, token = load_credentials()
    r = requests.get(f"{base.rstrip('/')}/api/earnings-calendar/{sym}",
                     params={"back": 4000, "ahead": 0},
                     headers={"Authorization": f"Bearer {token}"} if token else {},
                     timeout=45)
    today = _dt.date.today().isoformat()
    raw = sorted({str(e.get("report_date"))[:10]
                  for e in (r.json().get("events") or [])
                  if str(e.get("report_date"))[:10] < today})
    prints = []
    for d in raw:  # cluster <=3-day source drift to the earliest
        if prints and (_dt.date.fromisoformat(d)
                       - _dt.date.fromisoformat(prints[-1])).days <= 3:
            continue
        prints.append(d)

    dates, opens, highs, lows, closes = _load_ohlc(sym)
    ema20 = _ema(closes)
    atr14 = _atr(highs, lows, closes)

    events, skipped = [], 0
    for pd_ in prints:
        # D = last session at or before the report date (AMC: its close is pre-print)
        di = bisect.bisect_right(dates, pd_) - 1
        if di < max(SMA_N, EMA_N, ATR_N) + WINDOW or di + 1 >= len(closes):
            skipped += 1
            continue
        ti = di + 1
        ev = {"print": pd_, "D": dates[di]}
        ev["into_5s"] = closes[di] / closes[di - 5] - 1
        ev["gap_open"] = opens[ti] / closes[di] - 1
        ev["gap_close"] = closes[ti] / closes[di] - 1
        ev["atr_pct"] = atr14[di] / closes[di]
        sma50 = sum(closes[di - SMA_N + 1:di + 1]) / SMA_N
        ev["uptrend"] = closes[di - WINDOW] > sma50

        # pullback-touch + daily reclaim inside the pre-print window
        touch = next((i for i in range(di - WINDOW + 1, di + 1)
                      if lows[i] <= ema20[i]), None)
        entry_i = (next((i for i in range(touch, di + 1)
                         if closes[i] > ema20[i]), None)
                   if touch is not None else None)
        if entry_i is not None and entry_i < di:
            e = closes[entry_i]
            ev["setup"] = True
            ev["entry_D"] = dates[entry_i]
            ev["ret_preexit"] = closes[di] / e - 1          # spec path: out at D close
            ev["ret_through"] = closes[ti] / e - 1          # what the exit forgoes/avoids
            ev["mae"] = min(lows[entry_i:di + 1]) / e - 1
            ev["mfe"] = max(highs[entry_i:di + 1]) / e - 1
        else:
            ev["setup"] = False
        events.append(ev)

    setups = [e for e in events if e.get("setup")]
    n = len(events)
    print(f"{sym} — {len(prints)} prints on record, {n} with usable bars, "
          f"{skipped} skipped for missing history")
    print(f"CALIBRATION SAMPLE, not proof: n={n} events, {len(setups)} setups. "
          f"Entry analogue is a DAILY EMA20 touch-and-reclaim — the spec's 15m "
          f"reclaim cannot be backtested from this store.\n")

    print("── the pre-print window itself ──")
    up = [e for e in events if e["uptrend"]]
    print(f"  5-session return into the print: mean {pct(_mean([e['into_5s'] for e in events]))}, "
          f"sold off ≥4% in {sum(1 for e in events if e['into_5s'] <= -0.04)}/{n}")
    print(f"  window opened in an uptrend (close>SMA50): {len(up)}/{n}")

    print("\n── the setup the spec trades (daily analogue) ──")
    if setups:
        rp = [e["ret_preexit"] for e in setups]
        rt = [e["ret_through"] for e in setups]
        print(f"  EMA20 touch+reclaim occurred pre-print: {len(setups)}/{n} windows")
        print(f"  entry → pre-print exit (the spec's path): mean {pct(_mean(rp))}, "
              f"median {pct(statistics.median(rp))}, win {100*sum(1 for x in rp if x>0)/len(rp):.0f}%, "
              f"worst {pct(min(rp))}")
        print(f"  MAE mean {pct(_mean([e['mae'] for e in setups]))}, "
              f"worst {pct(min(e['mae'] for e in setups))}   ·   "
              f"MFE mean {pct(_mean([e['mfe'] for e in setups]))}")
        print(f"  if HELD THROUGH the print instead: mean {pct(_mean(rt))}, "
              f"worst {pct(min(rt))}  → the exit rule forgoes "
              f"{pct(_mean(rt) - _mean(rp))} on average to avoid that tail")
    else:
        print("  no qualifying setups — nothing to calibrate on")

    print("\n── the gap the mandatory exit avoids ──")
    go = [e["gap_open"] for e in events]
    gc = [e["gap_close"] for e in events]
    print(f"  overnight open gap: mean {pct(_mean(go))}, worst {pct(min(go))}, "
          f"|gap|≥5% in {sum(1 for x in go if abs(x) >= 0.05)}/{n}, "
          f"≥10% in {sum(1 for x in go if abs(x) >= 0.10)}/{n}")
    print(f"  print-day close move: mean {pct(_mean(gc))}, worst {pct(min(gc))}, "
          f"best {pct(max(gc))}")

    print("\n── ATR calibration (spec §18.4) ──")
    cur_atr = atr14[-1]
    cur_close = closes[-1]
    print(f"  ATR14 now: {cur_atr:.2f} ({100*cur_atr/cur_close:.1f}% of price) · "
          f"close {cur_close:.2f}")
    for name, lo, hi in (("S1 990 → inval 965", 965.0, 990.0),
                         ("S2 965-975 → inval 955", 955.0, 975.0),
                         ("C1 935-955", 935.0, 955.0)):
        print(f"  {name:26} width {hi-lo:.0f} = {(hi-lo)/cur_atr:.1f}x ATR")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"symbol": sym, "events": events, "n": n,
             "generated_utc": _dt.datetime.now(_dt.UTC).isoformat()}, indent=1))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ── ATR-stop variant study ────────────────────────────────────────────────
# Requested by the owner's advisor (6 Sep 2026) before any ATR distance goes
# into the spec: re-run the qualified setups across stop widths and make the
# choice a DOCUMENTED CALIBRATION, not a new intuition.
#
# The 15m close-without-reclaim rule cannot be tested from daily bars. It is
# BRACKETED instead: a daily TOUCH stop (low <= stop → filled at the stop, or
# at the open when the session gaps through it) is the harshest reading; a
# daily CLOSE stop (close < stop → out at that close) is the most lenient.
# The live 15m rule sits between the two, and every table says so.

def atr_variants(sym: str, variants=(0.5, 0.8, 1.0)) -> dict:
    import bisect

    import requests

    from .push_to_api import load_credentials
    base, token = load_credentials()
    r = requests.get(f"{base.rstrip('/')}/api/earnings-calendar/{sym}",
                     params={"back": 4000, "ahead": 0},
                     headers={"Authorization": f"Bearer {token}"} if token else {},
                     timeout=45)
    today = _dt.date.today().isoformat()
    raw = sorted({str(e.get("report_date"))[:10]
                  for e in (r.json().get("events") or [])
                  if str(e.get("report_date"))[:10] < today})
    prints = []
    for d in raw:
        if prints and (_dt.date.fromisoformat(d)
                       - _dt.date.fromisoformat(prints[-1])).days <= 3:
            continue
        prints.append(d)

    dates, opens, highs, lows, closes = _load_ohlc(sym)
    ema20 = _ema(closes)
    atr14 = _atr(highs, lows, closes)

    setups = []
    for pd_ in prints:
        di = bisect.bisect_right(dates, pd_) - 1
        if di < max(SMA_N, EMA_N, ATR_N) + WINDOW or di + 1 >= len(closes):
            continue
        touch = next((i for i in range(di - WINDOW + 1, di + 1)
                      if lows[i] <= ema20[i]), None)
        entry_i = (next((i for i in range(touch, di + 1)
                         if closes[i] > ema20[i]), None)
                   if touch is not None else None)
        if entry_i is not None and entry_i < di:
            setups.append((entry_i, di))

    med_atrpct = statistics.median(
        atr14[e] / closes[e] for e, _ in setups) if setups else 0

    def run(k: float, model: str):
        rows = []
        for e_i, di in setups:
            e = closes[e_i]
            d = atr14[e_i] * k
            s = e - d
            exit_px, stopped = closes[di], False
            for j in range(e_i + 1, di + 1):
                if model == "touch" and lows[j] <= s:
                    exit_px, stopped = (opens[j] if opens[j] < s else s), True
                    break
                if model == "close" and closes[j] < s:
                    exit_px, stopped = closes[j], True
                    break
            rows.append({
                "ret": exit_px / e - 1,
                "r_mult": (exit_px - e) / d,     # same fixed £ risk per trade
                "stopped": stopped,
                # stopped, but the planned pre-print exit was a WINNER anyway
                "recovered": stopped and closes[di] > e,
                "hi_vol": atr14[e_i] / closes[e_i] > med_atrpct,
            })
        return rows

    out = {}
    for k in variants:
        for model in ("touch", "close"):
            rows = run(k, model)
            rets = [r["ret"] for r in rows]
            rms = [r["r_mult"] for r in rows]
            grp = {
                "n": len(rows),
                "win": sum(1 for x in rets if x > 0) / len(rows),
                "mean": _mean(rets), "median": statistics.median(rets),
                "worst": min(rets),
                "mean_R": _mean(rms),
                "stop_rate": sum(r["stopped"] for r in rows) / len(rows),
                "stopped_would_have_won":
                    sum(r["recovered"] for r in rows),
                "mean_hiVol": _mean([r["ret"] for r in rows if r["hi_vol"]]),
                "mean_loVol": _mean([r["ret"] for r in rows if not r["hi_vol"]]),
            }
            out[f"{k}atr/{model}"] = grp
    return out


def variants_main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-preearnings-variants")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    res = atr_variants(args.symbol.upper())
    n = next(iter(res.values()))["n"] if res else 0
    print(f"{args.symbol.upper()} — {n} qualified setups. CALIBRATION, not proof.")
    print("The 15m close-without-reclaim rule is BRACKETED: 'touch' = harshest "
          "reading, 'close' = most lenient; live behaviour sits between.\n")
    hdr = f"{'variant':14}{'win':>6}{'mean':>8}{'med':>8}{'worst':>8}{'mean-R':>8}{'stops':>7}{'won-anyway':>11}{'hiVol':>8}{'loVol':>8}"
    print(hdr)
    for k, g in res.items():
        print(f"{k:14}{100*g['win']:5.0f}%{100*g['mean']:+7.1f}%{100*g['median']:+7.1f}%"
              f"{100*g['worst']:+7.1f}%{g['mean_R']:+8.2f}{100*g['stop_rate']:6.0f}%"
              f"{g['stopped_would_have_won']:>8}   {100*(g['mean_hiVol'] or 0):+6.1f}%"
              f"{100*(g['mean_loVol'] or 0):+7.1f}%")
    print("\nmean-R = return per unit of risk with the SAME fixed £ budget per "
          "trade\n(shares scale inversely with stop width — the advisor's "
          "risk-sized comparison).")
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1))
        print(f"wrote {args.out}")
    return 0
