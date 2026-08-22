"""Trade odds — "if I place a limit at X and target Y, what are the chances?"

Owner: "we should be able to place an order at 920 and close at 950 and let it
run ... calculate the probability."

WHAT THIS IS. A base rate measured on this symbol's OWN history, not a
forecast. It walks every historical bar, pretends the same order was resting
there, and counts what actually happened. If MU reached a +3.3% target before
a -8% stop in 61% of past attempts, that is the number — no model, no
distribution assumption, no opinion.

WHY IT IS TWO QUESTIONS, NOT ONE. This is the part the phrasing hides. With MU
at 966.78, a limit at 920 is a bet that price falls 4.8% FIRST. Most of the
time it simply will not, and you are flat. So the honest answer is a chain:

    P(filled)                  does price reach the limit at all, within N days
    P(target | filled)         and THEN does it reach the target before the stop
    P(both)                    the two multiplied — what actually happens to you

Quoting only the second number would flatter the trade badly.

CONSERVATIVE ON AMBIGUITY. Daily bars cannot say whether the high or the low
came first. When one session touches BOTH the target and the stop, this counts
it as a STOP. That biases every number DOWNWARD, which is the correct
direction for something a person will risk money on.

WHAT IT DELIBERATELY DOES NOT USE.

  * Live prices. Daily bars up to the last settled session. Nothing here is
    a live quote and it must never be described as one.
  * Volume. Momentum v3 gated on entry volume and was REJECTED — the apparent
    edge exists only in the second half of the record and inverts before 2020
    (MOMENTUM_GATES_V3.md, e50cd2a). Using it here would contradict our own
    evidence.
  * Support and resistance levels. Tested over 76,260 touch events, both
    edges negative against random placebo lines. What replaces it is the
    EXCURSION table: how far this symbol actually travels before reversing,
    measured rather than drawn.

REGIME WARNING. The all-history number and the recent number are both shown
and they can differ a lot. MU trading at 88 in 2025 and 966 now is not the
same instrument's behaviour. Trust the recent row more, and the sample size
above all.
"""
from __future__ import annotations

import argparse
import logging
import statistics as st

from .momentum_candidates import _load, poison_check, _tradeable

log = logging.getLogger("tradepro.trade_odds")


def barrier_scan(c, h, l, dates, *, limit_pct, target_pct, stop_pct,
                 fill_window, trade_window, start_idx=0):
    """Replay a resting limit order from every historical bar.

    limit_pct/target_pct/stop_pct are all RELATIVE TO THE PRICE AT THE MOMENT
    THE ORDER IS PLACED, so the same order shape can be measured across a
    history spanning an 11x price change.
    """
    attempts = filled = won = lost = timed = 0
    bars_to_fill: list[int] = []
    bars_to_win: list[int] = []
    outcomes: list[float] = []

    n = len(c)
    for i in range(start_idx, n - 1):
        ref = c[i]
        if ref <= 0:
            continue
        attempts += 1
        limit = ref * (1 + limit_pct)
        fill_j = None
        for j in range(i + 1, min(n, i + fill_window + 1)):
            # A limit BELOW the market fills when price trades down to it;
            # a limit ABOVE fills when price trades up to it.
            if (limit_pct <= 0 and l[j] <= limit) or (limit_pct > 0 and h[j] >= limit):
                fill_j = j
                break
        if fill_j is None:
            continue
        filled += 1
        bars_to_fill.append(fill_j - i)

        tgt = limit * (1 + target_pct)
        stp = limit * (1 + stop_pct)
        done = False
        for j in range(fill_j, min(n, fill_j + trade_window + 1)):
            hit_t = h[j] >= tgt
            hit_s = l[j] <= stp
            if hit_t and hit_s:
                # Same session touched both. Daily bars cannot order them, so
                # assume the bad one — never the flattering one.
                lost += 1; outcomes.append(stop_pct * 100); done = True; break
            if hit_t:
                won += 1; bars_to_win.append(j - fill_j)
                outcomes.append(target_pct * 100); done = True; break
            if hit_s:
                lost += 1; outcomes.append(stop_pct * 100); done = True; break
        if not done:
            timed += 1
            end = min(n - 1, fill_j + trade_window)
            outcomes.append(100 * (c[end] / limit - 1))

    return {
        "attempts": attempts, "filled": filled, "won": won, "lost": lost,
        "timed_out": timed,
        "p_fill": (filled / attempts) if attempts else None,
        "p_target_given_fill": (won / filled) if filled else None,
        "p_stop_given_fill": (lost / filled) if filled else None,
        "p_both": (won / attempts) if attempts else None,
        "median_bars_to_fill": (st.median(bars_to_fill) if bars_to_fill else None),
        "median_bars_to_target": (st.median(bars_to_win) if bars_to_win else None),
        "mean_outcome_pct": (round(st.mean(outcomes), 2) if outcomes else None),
    }


def excursion_table(c, h, l, *, window, start_idx=0):
    """How far does this symbol actually TRAVEL before reversing?

    The empirical answer to the question support/resistance lines are usually
    asked. From each bar, the maximum favourable move within `window`
    sessions. If the median is +4% and you are targeting +12%, the target is
    fighting the symbol's own behaviour — and that is measured, not drawn.
    """
    ups: list[float] = []
    n = len(c)
    for i in range(start_idx, n - 1):
        if c[i] <= 0:
            continue
        end = min(n, i + window + 1)
        ups.append(100 * (max(h[i + 1:end]) / c[i] - 1))
    if not ups:
        return None
    ups.sort()
    q = lambda p: ups[int(p * (len(ups) - 1))]  # noqa: E731
    return {"n": len(ups), "p25": round(q(.25), 1), "median": round(q(.50), 1),
            "p75": round(q(.75), 1), "p90": round(q(.90), 1)}


def sweep_targets(c, h, l, dates, *, limit_pct, stop_pct, fill_window,
                  trade_window, start_idx=0, targets=None):
    """Same order, swept across a range of TARGETS.

    Because the target is the variable actually worth solving. MU shows why:
    a +3.3% target hits 83% of the time, which reads as a great trade until
    you notice the -8% stop means one loss erases 2.4 wins — while the symbol
    typically travels +18% in the same window. A high hit rate and a good
    trade are different things, and only expectancy tells them apart.

    Expectancy per FILLED order is the column to read.
    """
    targets = targets or [1, 2, 3, 5, 8, 12, 18, 25]
    rows = []
    for t in targets:
        r = barrier_scan(c, h, l, dates, limit_pct=limit_pct, target_pct=t / 100,
                         stop_pct=stop_pct, fill_window=fill_window,
                         trade_window=trade_window, start_idx=start_idx)
        rows.append({
            "target_pct": t,
            "p_target_given_fill": r["p_target_given_fill"],
            "expectancy_pct": r["mean_outcome_pct"],
            "reward_risk": round(t / abs(stop_pct * 100), 2) if stop_pct else None,
            "median_bars_to_target": r["median_bars_to_target"],
            "filled": r["filled"],
        })
    return rows


def compute(symbol, *, entry, target, stop_pct, fill_window, trade_window, ref=None):
    df = _load(symbol)
    if df is None:
        return {"error": f"no stored daily bars for {symbol}"}
    c = df["close"].tolist(); h = df["high"].tolist(); l = df["low"].tolist()
    dates = [str(x)[:10] for x in df.index]
    ok, ratio = poison_check(c)
    if not ok:
        return {"error": f"{symbol} is quarantined — historical max is {ratio}x the recent "
                         f"median, consistent with a wrong-venue series. No odds computed."}
    last = c[-1]
    ref = ref if ref is not None else last
    limit_pct = entry / ref - 1
    target_pct = target / entry - 1
    if target_pct <= 0:
        return {"error": "target must be above the entry"}

    # Recent = last ~2 years of sessions, shown separately because regime
    # matters more than sample size here (momentum v3 proved that the hard way).
    recent_start = max(0, len(c) - 504)
    out = {
        "symbol": symbol, "as_of_bar": dates[-1], "last_close": round(last, 2),
        "reference_price": round(ref, 2), "entry": entry, "target": target,
        "limit_move_pct": round(100 * limit_pct, 2),
        "target_move_pct": round(100 * target_pct, 2),
        "stop_pct": round(100 * stop_pct, 2),
        "stop_price": round(entry * (1 + stop_pct), 2),
        "fill_window": fill_window, "trade_window": trade_window,
        "all_history": barrier_scan(c, h, l, dates, limit_pct=limit_pct,
                                    target_pct=target_pct, stop_pct=stop_pct,
                                    fill_window=fill_window, trade_window=trade_window),
        "recent_2y": barrier_scan(c, h, l, dates, limit_pct=limit_pct,
                                  target_pct=target_pct, stop_pct=stop_pct,
                                  fill_window=fill_window, trade_window=trade_window,
                                  start_idx=recent_start),
        "excursion_all": excursion_table(c, h, l, window=trade_window),
        "excursion_recent": excursion_table(c, h, l, window=trade_window,
                                            start_idx=recent_start),
        # BOTH eras, always. A sweep on the last two years alone would have
        # told an MU trader to target +25%, because MU rose 11x over exactly
        # that window. Momentum v3 was rejected for believing a second-half
        # regime was an edge; the same mistake is available here and is far
        # more expensive, because a person acts on this one directly.
        "sweep_all": sweep_targets(c, h, l, dates, limit_pct=limit_pct,
                                   stop_pct=stop_pct, fill_window=fill_window,
                                   trade_window=trade_window),
        "sweep_recent": sweep_targets(c, h, l, dates, limit_pct=limit_pct,
                                      stop_pct=stop_pct, fill_window=fill_window,
                                      trade_window=trade_window, start_idx=recent_start),
        "history_from": dates[0], "bars": len(c),
        # Said out loud rather than left to be inferred from a small number in
        # a corner. SKHY has two partitions of history; odds computed on it
        # would look identical in shape to MU's 4,183 bars and mean nothing.
        "thin_history": (None if len(c) >= 500 else
                         f"only {len(c)} stored sessions from {dates[0]} — too little history "
                         f"for these odds to mean much. Treat them as indicative at best."),
        "not_live": ("Computed from stored daily bars up to the last settled session. "
                     "These are not live prices and this is a base rate, not a forecast."),
    }
    return out


def _pct(x):
    return "—" if x is None else f"{100 * x:.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="Historical odds for a limit-entry trade")
    ap.add_argument("symbol")
    ap.add_argument("--entry", type=float, required=True, help="limit price you would rest")
    ap.add_argument("--target", type=float, required=True)
    ap.add_argument("--stop-pct", type=float, default=-8.0, help="stop, %% from entry (default -8)")
    ap.add_argument("--fill-window", type=int, default=10, help="sessions to wait for a fill")
    ap.add_argument("--trade-window", type=int, default=20, help="sessions to hold once filled")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    r = compute(a.symbol.upper(), entry=a.entry, target=a.target,
                stop_pct=a.stop_pct / 100, fill_window=a.fill_window,
                trade_window=a.trade_window)
    if "error" in r:
        print(f"✗ {r['error']}")
        return 1
    print(f"{r['symbol']} — last settled close {r['last_close']} ({r['as_of_bar']})")
    print(f"  rest a limit at {r['entry']} ({r['limit_move_pct']:+.1f}%), "
          f"target {r['target']} ({r['target_move_pct']:+.1f}% from entry), "
          f"stop {r['stop_price']} ({r['stop_pct']:+.1f}%)")
    print(f"  wait up to {r['fill_window']} sessions for a fill, then hold up to {r['trade_window']}\n")
    print(f"{'':<12}{'attempts':>9}{'P(fill)':>9}{'P(tgt|fill)':>13}{'P(BOTH)':>9}{'avg out':>9}")
    for k, label in (("all_history", "all history"), ("recent_2y", "last 2yr")):
        s = r[k]
        print(f"{label:<12}{s['attempts']:>9}{_pct(s['p_fill']):>9}"
              f"{_pct(s['p_target_given_fill']):>13}{_pct(s['p_both']):>9}"
              f"{(str(s['mean_outcome_pct']) + '%') if s['mean_outcome_pct'] is not None else '—':>9}")
    for k, label in (("excursion_all", "all history"), ("excursion_recent", "last 2yr")):
        e = r[k]
        if e:
            print(f"\nhow far it travels up within {r['trade_window']} sessions ({label}): "
                  f"median {e['median']}% · 75th {e['p75']}% · 90th {e['p90']}%")
    print(f"\n{'':>8}{'':>7}{'--- all history ---':>26}{'--- last 2 years ---':>26}")
    print(f"{'target':>8}{'R:R':>7}{'P(hit|fill)':>13}{'expectancy':>13}"
          f"{'P(hit|fill)':>13}{'expectancy':>13}")
    best_a = max((x["expectancy_pct"] or -99) for x in r["sweep_all"])
    best_r = max((x["expectancy_pct"] or -99) for x in r["sweep_recent"])
    for ra, rr in zip(r["sweep_all"], r["sweep_recent"]):
        mark = ""
        if ra["expectancy_pct"] == best_a and rr["expectancy_pct"] == best_r:
            mark = "  ← best in BOTH"
        elif ra["expectancy_pct"] == best_a:
            mark = "  ← best all-history"
        elif rr["expectancy_pct"] == best_r:
            mark = "  ← best recent only"
        print(f"{('+' + str(ra['target_pct']) + '%'):>8}{ra['reward_risk']:>7}"
              f"{_pct(ra['p_target_given_fill']):>13}{(str(ra['expectancy_pct']) + '%'):>13}"
              f"{_pct(rr['p_target_given_fill']):>13}{(str(rr['expectancy_pct']) + '%'):>13}{mark}")
    print("  A target that is best ONLY in the recent column is a bet on the regime "
          "continuing,\n  not a property of the symbol.")
    if r.get("thin_history"):
        print(f"\n⚠ {r['thin_history']}")
    print(f"\n{r['not_live']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
