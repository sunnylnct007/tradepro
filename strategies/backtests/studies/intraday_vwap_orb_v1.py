"""INTRADAY_VWAP_ORB_GATES_V1 — grading a proposed intraday sleeve.

The rules, the fill model, the walk-forward and the GATES are the proposal's
own, reproduced rather than reinterpreted. Two things were added, and both are
stated so the result is not mistaken for the proposal's own claim:

  1. COSTS. The proposal models none — not spread, not commission, not
     slippage. Its reversion target is 1.0% gross against a 0.8% stop, a
     structural payoff of ~1.25 against a gate demanding >=1.20. There is
     almost no room, so a costless result cannot be graded against a gate that
     assumes real trading. Costs are a PARAMETER here and the run reports both.

  2. A SESSION FLOOR. The gates need 60 out-of-sample trades. At <=2 trades a
     day with a 40% holdout that needs ~75 sessions per symbol, and 941 of our
     973 symbols hold under 50. Grading those would measure the sample, not the
     rule.
"""
import glob
import statistics
import sys
from dataclasses import dataclass

import pandas as pd

sys.path.insert(0, ".")

BAR_CACHE = "/Users/skumar/.tradepro/bar_cache"
MIN_SESSIONS = 250          # below this a symbol is reported, never graded
#: Round-trip cost in PERCENT of notional: spread crossed twice plus commission.
#: 0.04% is deliberately modest for liquid US large caps at 5m — the point is
#: that it is not ZERO, which is what the proposal assumes.
COST_PCT = 0.04


@dataclass
class Params:
    stretch: float = 0.007
    target: float = 0.003
    stop: float = 0.008
    slope_max: float = 0.05
    slope_look: int = 4
    max_trades: int = 2
    first_bar: int = 6
    last_bar: int = 24
    orb_bars: int = 2


@dataclass
class Trade:
    session: str
    setup: str
    side: str
    pct: float          # NET of costs


def vwap_series(high, low, close, volume):
    pv = cv = 0.0
    out = []
    for h, l, c, v in zip(high, low, close, volume):
        pv += ((h + l + c) / 3) * v
        cv += v
        out.append(pv / cv if cv else c)
    return out


def _resolve(side, bars, start, stop_px, target_px, last_bar):
    high, low, close = bars["high"], bars["low"], bars["close"]
    for j in range(start, min(last_bar + 1, len(close))):
        hit_stop = low[j] <= stop_px if side > 0 else high[j] >= stop_px
        hit_target = high[j] >= target_px if side > 0 else low[j] <= target_px
        if hit_stop:                       # STOP FIRST when a bar holds both
            return stop_px, "stop", j
        if hit_target:
            return target_px, "target", j
    j = min(last_bar, len(close) - 1)
    return close[j], "time", j


def reversion(session, bars, p, cost):
    vw = vwap_series(bars["high"], bars["low"], bars["close"], bars["volume"])
    out, taken, i = [], 0, p.first_bar
    n = len(bars["close"])
    while i <= min(p.last_bar, n - 1) and taken < p.max_trades:
        if i - p.slope_look < 0:
            i += 1
            continue
        drift = abs(vw[i] - vw[i - p.slope_look]) / vw[i] * 100
        if drift > p.slope_max:
            i += 1
            continue
        w = vw[i]
        long_ = bars["low"][i] <= w * (1 - p.stretch)
        short = bars["high"][i] >= w * (1 + p.stretch)
        if not (long_ or short):
            i += 1
            continue
        side = 1 if long_ else -1
        entry = w * (1 - p.stretch) if long_ else w * (1 + p.stretch)
        target = w * (1 + p.target) if long_ else w * (1 - p.target)
        stop = entry * (1 - p.stop) if long_ else entry * (1 + p.stop)
        px, _why, j = _resolve(side, bars, i, stop, target, p.last_bar)
        gross = (px - entry) / entry * 100 * side
        out.append(Trade(session, "reversion", "long" if long_ else "short",
                         gross - cost))
        taken += 1
        i = j + 1
    return out


def opening_range(session, bars, p, cost):
    n = len(bars["close"])
    if n <= p.orb_bars:
        return []
    hi = max(bars["high"][:p.orb_bars])
    lo = min(bars["low"][:p.orb_bars])
    width = hi - lo
    for i in range(p.orb_bars, min(p.last_bar, n - 1)):
        c = bars["close"][i]
        if c > hi or c < lo:
            side = 1 if c > hi else -1
            stop = lo if side > 0 else hi
            target = c + side * width
            px, _why, _ = _resolve(side, bars, i + 1, stop, target, p.last_bar)
            gross = (px - c) / c * 100 * side
            return [Trade(session, "orb", "long" if side > 0 else "short",
                          gross - cost)]
    return []


def classify(bars, p):
    vw = vwap_series(bars["high"], bars["low"], bars["close"], bars["volume"])
    i = min(p.first_bar, len(vw) - 1)
    drift = abs(vw[i] - vw[p.orb_bars]) / vw[i] * 100
    return "reversion" if drift <= p.slope_max * 2 else "breakout"


def run_backtest(sessions, p, cost=COST_PCT):
    trades = []
    for label, bars in sessions:
        if len(bars["close"]) < p.first_bar + 2:
            continue
        trades += (reversion(label, bars, p, cost)
                   if classify(bars, p) == "reversion"
                   else opening_range(label, bars, p, cost))
    return summarise(trades)


def summarise(trades, notional=25_000):
    out = {}
    for tag in ("reversion", "orb", "all"):
        sel = [t for t in trades if tag == "all" or t.setup == tag]
        if not sel:
            continue
        pct = [t.pct for t in sel]
        wins = [x for x in pct if x > 0]
        losses = [x for x in pct if x <= 0]
        out[tag] = {
            "n": len(pct),
            "hit_rate": len(wins) / len(pct),
            "avg_pct": statistics.mean(pct),
            "payoff": (statistics.mean(wins) / abs(statistics.mean(losses)))
                      if wins and losses else None,
            "expectancy_usd": (sum(pct) / len(pct)) / 100 * notional,
        }
    return out


def load_sessions(symbol):
    files = sorted(glob.glob(f"{BAR_CACHE}/*/{symbol}/5m/*.parquet"))
    if not files:
        return []
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    out = []
    for day, g in df.groupby(df.index.date):
        if len(g) < 12:
            continue
        out.append((str(day), {"high": g["high"].tolist(), "low": g["low"].tolist(),
                               "close": g["close"].tolist(),
                               "volume": g["volume"].tolist()}))
    return out


GATES = {"min_trades": 60, "min_hit_rate": 0.55, "min_payoff": 1.20,
         "min_expectancy_usd": 40, "max_variants_before_oos": 6}


def main() -> int:
    grid = [Params(stretch=s, target=t)
            for s in (0.005, 0.007, 0.010) for t in (0.002, 0.003)]
    assert len(grid) <= GATES["max_variants_before_oos"], "too many variants"

    syms = sorted({f.split("/")[-3] for f in glob.glob(f"{BAR_CACHE}/*/*/5m/*.parquet")})
    deep = []
    for s in syms:
        n = len(load_sessions(s))
        if n >= MIN_SESSIONS:
            deep.append((s, n))
    print(f"{len(deep)} symbol(s) with >= {MIN_SESSIONS} sessions of 5m history\n")

    all_sessions = []
    for s, n in sorted(deep, key=lambda x: -x[1]):
        all_sessions += load_sessions(s)
    print(f"pooled sessions across those names: {len(all_sessions)}")

    cut = int(len(all_sessions) * 0.6)
    in_s, out_s = all_sessions[:cut], all_sessions[cut:]
    print(f"in-sample {len(in_s)} · out-of-sample {len(out_s)}\n")

    scored = []
    for p in grid:
        r = run_backtest(in_s, p)
        scored.append(((r.get("all") or {}).get("expectancy_usd", -1e9), p, r))
    scored.sort(key=lambda x: -x[0])
    best_exp, best_p, in_res = scored[0]
    print(f"chosen in-sample: stretch={best_p.stretch} target={best_p.target} "
          f"(expectancy ${best_exp:.2f})")
    for tag in ("all", "reversion", "orb"):
        v = in_res.get(tag)
        if v:
            print(f"  IN  {tag:10} n={v['n']:>5} hit={100*v['hit_rate']:.1f}% "
                  f"avg={v['avg_pct']:+.3f}% payoff="
                  f"{v['payoff']:.2f}" if v.get("payoff") else
                  f"  IN  {tag:10} n={v['n']:>5} hit={100*v['hit_rate']:.1f}%")

    oos_res = run_backtest(out_s, best_p)
    print()
    for tag in ("all", "reversion", "orb"):
        v = oos_res.get(tag)
        if not v:
            continue
        pay = f"{v['payoff']:.2f}" if v.get("payoff") else "—"
        print(f"  OOS {tag:10} n={v['n']:>5} hit={100*v['hit_rate']:.1f}% "
              f"avg={v['avg_pct']:+.3f}% payoff={pay} "
              f"exp=${v['expectancy_usd']:+.2f}")

    oos = oos_res.get("all", {})
    checks = {
        "trades >= 60": oos.get("n", 0) >= GATES["min_trades"],
        "hit >= 55%": oos.get("hit_rate", 0) >= GATES["min_hit_rate"],
        "payoff >= 1.20": (oos.get("payoff") or 0) >= GATES["min_payoff"],
        "expectancy >= $40": oos.get("expectancy_usd", 0) >= GATES["min_expectancy_usd"],
        "variants <= 6": len(grid) <= GATES["max_variants_before_oos"],
    }
    print(f"\n— the proposal's own gates, out of sample (costs {COST_PCT}%/trade) —")
    for k, v in checks.items():
        print(f"  {k:22} {'PASS' if v else 'FAIL'}")
    print(f"\n  VERDICT: {'ALL PASS' if all(checks.values()) else 'FAILS'}")

    # And the same OOS with costs OFF, so the cost sensitivity is visible.
    free = run_backtest(out_s, best_p, cost=0.0).get("all", {})
    if free:
        print(f"\n  with ZERO costs (the proposal's own assumption): "
              f"exp=${free['expectancy_usd']:+.2f} vs ${oos.get('expectancy_usd', 0):+.2f} "
              f"net — costs move it by ${free['expectancy_usd'] - oos.get('expectancy_usd', 0):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
