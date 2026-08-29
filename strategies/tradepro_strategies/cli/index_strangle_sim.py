"""Historical backtest AND forward Monte Carlo for the index short strangle.

Owner, 29 Aug 2026: *"I need to run simulations on all those"* and *"is there no
way we can do future simulations on these numbers like monte carlo"*.

Yes — with one constraint that decides how every number here must be read.

WHAT A MONTE CARLO CAN AND CANNOT TELL YOU HERE.

The simulation resamples the strategy's own historical trades. It therefore
answers: *given a future that looks like the sampled past, what is the range of
outcomes?* It CANNOT answer: *what happens in a crash?* — because the volatility
gate deliberately stands aside in crashes, so no crash day is in the sample to
resample. Feeding gated returns into a bootstrap and calling the 5th percentile
a "worst case" is a false-confidence machine, and this file refuses to do it.

Two things are done about that:

  1. THE BOOTSTRAP IS BLOCKED, NOT IID. Losing days arrive together — volatility
     clusters. Drawing trades independently breaks that clustering and flatters
     the drawdown by a wide margin. A stationary bootstrap (Politis-Romano,
     geometric block lengths) preserves it. Both are computed and reported side
     by side, because the GAP between them is itself the measure of how much
     clustering matters for this strategy.

  2. A SEPARATE GATE-FAILURE STRESS. The same strangle is priced on the worst
     sessions in the underlying's whole history REGARDLESS of the gate. That is
     not a forecast; it is the answer to "what does one leaked crash day cost?"
     — the only question that actually matters for a short-premium book, and the
     one the bootstrap structurally cannot reach.

WHAT IS BEING PRICED. Strikes come from `strike_pair` in index_strangle_paper —
imported, not reimplemented — so the simulated trade is the one the email
prints. Credit and exit are Black-Scholes at the vol index. That is a MODEL
price, not a traded one; real credits carry a bid-ask spread that this does not
charge. Returns are quoted as % of collateral (the put strike), which makes them
comparable across an index at 29,000 and an ETF at 700.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import math
import os

log = logging.getLogger("tradepro.index_strangle_sim")

OUT_DIR = os.path.expanduser("~/.tradepro/research")
OUT = os.path.join(OUT_DIR, "index_strangle_sim.json")

MC_PATHS = 20_000        # forward paths per market
MC_TRADES = 50           # trades per path — about a year at the observed rate
MC_BLOCK = 5.0           # mean block length, trades. See _stationary_bootstrap.
STRESS_N = 25            # worst sessions to price for the gate-failure case

# ---------------------------------------------------------------------------
# HOW THE VOLATILITY THRESHOLD IS CHOSEN — a rule, not a judgement.
#
# Owner, 29 Aug 2026: "how did u decided on the threshold value" and "how do we
# make it deterministic". The honest answer to the first was that SPY's 14 and
# India's 12 came from a documented sweep, while VXN<=18 and GVZ<=16 were my
# guesses dressed up with a plausible sentence. One of them was wrong: GVZ<=16
# traded through 31 sessions of 2022 and 4 of COVID.
#
# So the threshold is now COMPUTED. The rule, in full:
#
#     Of a fixed grid of candidate thresholds, choose the LARGEST one that
#     takes ZERO trades inside any declared crisis window.
#
# Largest, because frequency is the scarce resource — the whole reason the US
# sample was too thin to forward-test. Zero crisis trades, because the gate has
# exactly one job and a gate that opens during a crash has failed at it.
#
# This reproduces both thresholds that were independently arrived at earlier
# (SPY 14, India 12) and rejects the two I guessed, which is the only reason to
# trust it over my judgement.
#
# WHAT THIS RULE IS NOT. The windows below are chosen with hindsight, so the
# thresholds are fitted to crises that already happened. That makes this a
# ROBUSTNESS rule, not an out-of-sample result: it is evidence the gate would
# have sat out the last four crises, and NO evidence it will sit out the next
# one. The gate-failure stress exists precisely because this limit cannot be
# engineered away.
CRISIS_WINDOWS = [
    ("2008-01-01", "2009-06-30", "GFC"),
    ("2020-02-15", "2020-04-30", "COVID"),
    ("2022-01-01", "2022-10-31", "2022 bear"),
    ("2025-03-15", "2025-05-15", "Apr 2025"),
]
# Candidate grid, in half-points. Deliberately coarse: a rule that can select
# 17.35 is fitting noise, and a threshold nobody can state from memory is one
# nobody will sanity-check.
THRESHOLD_GRID = [x / 2 for x in range(16, 61)]      # 8.0 .. 30.0


def choose_threshold(market: str) -> dict:
    """Run the selection rule for one market and show its working.

    Returns the chosen threshold AND the full grid with each candidate's crisis
    leakage, so the choice can be audited rather than taken on trust.
    """
    from .index_strangle_paper import MARKETS
    cfg = MARKETS[market]
    px, vx = _load(cfg["index"]), _load(cfg["vol"])
    if px is None or vx is None:
        return {"status": "no_data"}
    j = px[["Open"]].join(vx["Close"].rename("V"), how="inner").dropna()
    # Same one-session lag as trade_returns — the threshold must be selected on
    # the information the gate will actually have, or the chosen value describes
    # a filter nobody can run.
    j["V"] = j["V"].shift(1)
    j = j.dropna()
    grid, chosen = [], None
    for t in THRESHOLD_GRID:
        g = j[j.V <= t]
        if len(g) < 100:                 # too thin to be a usable gate at all
            continue
        leaks = {}
        for a0, a1, lbl in CRISIS_WINDOWS:
            n = int(((g.index >= a0) & (g.index <= a1)).sum())
            if n:
                leaks[lbl] = n
        grid.append({"threshold": t, "sessions": int(len(g)), "leaks": leaks})
        if not leaks:
            chosen = t                   # keep walking up; last clean one wins
    return {"status": "ok", "chosen": chosen,
            "configured": cfg["vol_max"],
            "matches_config": chosen == cfg["vol_max"],
            "windows": [w[2] for w in CRISIS_WINDOWS],
            "grid": grid}


def _pricer():
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    return BlackScholesPricer()


def _load(sym: str):
    from ..yahoo_session import yahoo_session
    import yfinance as yf
    d = yf.Ticker(sym, session=yahoo_session()).history(period="max", interval="1d")
    if d is None or not len(d):
        return None
    d.index = [str(x)[:10] for x in d.index]
    return d


def trade_returns(market: str, dte: int = 7, gated: bool = True):
    """Per-trade return, % of collateral, for every session the gate allowed.

    One trade = sell the strangle at the OPEN, buy it back at the CLOSE the same
    day. Same-day because that is what the owner actually does; a held position
    would need overnight gap modelling this does not do.
    """
    import numpy as np
    from .index_strangle_paper import MARKETS, STRIKE_MULT, strike_pair
    cfg = MARKETS[market]
    px, vx = _load(cfg["index"]), _load(cfg["vol"])
    if px is None or vx is None:
        return None
    j = px[["Open", "Close"]].join(vx["Close"].rename("V"), how="inner").dropna()
    j = j.astype(float)
    # LAG THE VOLATILITY INDEX BY ONE SESSION. The trade is entered at the OPEN,
    # so the only vol reading available is the PREVIOUS close. Gating on the same
    # day's close - which this did until 29 Aug 2026 - lets the filter see the
    # very move it is supposed to be avoiding, and quietly excludes exactly the
    # days that would have hurt.
    #
    # It is not a small effect. Measured across five markets: mean return falls
    # 10-17%, and SPY's worst day goes -0.80% -> -1.89%, more than double. The
    # live screen reads the last COMPLETED close (both jobs run pre-open), so the
    # lagged figures are the ones that describe the traded thing. This is the
    # same harness-vs-screen mismatch already sitting in the Swing numbers.
    j["V"] = j["V"].shift(1)
    j = j.dropna()
    div = cfg.get("divisor", 1.0)
    j = j[j.Open > 0]
    if gated:
        j = j[j.V <= cfg["vol_max"]]
    p = _pricer()
    rows, dates = [], []
    for day, r in j.iterrows():
        s0, sc = r.Open / div, r.Close / div
        iv = r.V / 100.0 * cfg["vol_scale"]
        width = STRIKE_MULT * iv / math.sqrt(252)
        kp, kc, _ = strike_pair(s0, width, dte, cfg["rate"], cfg["grid"])
        if kp <= 0:
            continue
        credit = (p.price(s0, kc, dte / 365, iv, "call")
                  + p.price(s0, kp, dte / 365, iv, "put"))
        value = (p.price(sc, kc, (dte - 1) / 365, iv, "call")
                 + p.price(sc, kp, (dte - 1) / 365, iv, "put"))
        rows.append(100.0 * (credit - value) / kp)
        dates.append(str(day)[:10])
    return np.array(rows), dates


def _stationary_bootstrap(x, n_trades: int, n_paths: int, mean_block: float, seed: int):
    """Politis-Romano stationary bootstrap.

    At each step, continue the current run with probability 1-1/L or jump to a
    fresh random position. Block lengths are geometric with mean L, so runs of
    consecutive real trades are preserved — which is the whole point, because
    this strategy's losses are serially correlated and an IID draw would report
    a drawdown that cannot happen.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    n = len(x)
    idx = np.empty((n_paths, n_trades), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_paths)
    cont = rng.random((n_paths, n_trades)) > (1.0 / mean_block)
    fresh = rng.integers(0, n, (n_paths, n_trades))
    for t in range(1, n_trades):
        idx[:, t] = np.where(cont[:, t], (idx[:, t - 1] + 1) % n, fresh[:, t])
    return x[idx]


def _path_stats(paths):
    """Summary of simulated paths. Returns are summed, not compounded — each
    trade is collateralised separately, so they do not stack on one balance."""
    import numpy as np
    cum = np.cumsum(paths, axis=1)
    total = cum[:, -1]
    peak = np.maximum.accumulate(cum, axis=1)
    dd = (cum - peak).min(axis=1)          # worst peak-to-trough, points of collateral
    return {
        "median_total_pct": round(float(np.median(total)), 3),
        "p5_total_pct": round(float(np.percentile(total, 5)), 3),
        "p95_total_pct": round(float(np.percentile(total, 95)), 3),
        "prob_losing_year_pct": round(float(100 * (total < 0).mean()), 1),
        "median_max_drawdown_pct": round(float(np.median(dd)), 3),
        "p95_max_drawdown_pct": round(float(np.percentile(dd, 5)), 3),
        "worst_path_pct": round(float(total.min()), 3),
    }


def stress(market: str, dte: int = 7, n: int = STRESS_N):
    """What ONE leaked crash day costs — the bootstrap cannot reach this.

    Prices the same strangle on the `n` largest single-day moves in the whole
    history, gate ignored. It answers the only question that matters for short
    premium: not how often it wins, but what a miss costs when the filter fails.
    """
    import numpy as np
    r = trade_returns(market, dte=dte, gated=False)
    if r is None:
        return None
    vals, dates = r
    all_r = trade_returns(market, dte=dte, gated=True)
    order = np.argsort(vals)[:n]
    return {
        "worst_ungated_pct": round(float(vals[order[0]]), 3),
        "worst_ungated_date": dates[order[0]],
        "mean_of_worst_n_pct": round(float(vals[order].mean()), 3),
        "n_worst": n,
        "gated_worst_pct": (round(float(all_r[0].min()), 3)
                            if all_r is not None and len(all_r[0]) else None),
        "ungated_sessions": len(vals),
    }


def simulate(market: str, dte: int = 7, paths: int = MC_PATHS,
             trades: int = MC_TRADES, seed: int = 7) -> dict:
    import numpy as np
    got = trade_returns(market, dte=dte, gated=True)
    if got is None:
        return {"market": market, "status": "no_data"}
    x, dates = got
    if len(x) < 60:
        return {"market": market, "status": "too_few", "n": int(len(x))}

    span_yrs = max(0.5, (_dt.date.fromisoformat(dates[-1])
                         - _dt.date.fromisoformat(dates[0])).days / 365.25)
    hist = {
        "n_trades": int(len(x)),
        "first": dates[0], "last": dates[-1],
        "trades_per_year": round(len(x) / span_yrs, 1),
        "win_pct": round(float(100 * (x > 0).mean()), 1),
        "mean_pct": round(float(x.mean()), 4),
        "median_pct": round(float(np.median(x)), 4),
        "p5_pct": round(float(np.percentile(x, 5)), 3),
        "worst_pct": round(float(x.min()), 3),
        "best_pct": round(float(x.max()), 3),
        "stdev_pct": round(float(x.std(ddof=1)), 4),
    }
    # Sharpe-like ratio per trade, annualised at the observed firing rate. No
    # risk-free subtraction — this is a ratio for RANKING markets, not a
    # published Sharpe, and saying so stops it being quoted as one.
    hist["return_risk_ratio"] = round(
        float(x.mean() / x.std(ddof=1) * math.sqrt(hist["trades_per_year"])), 2)

    blocked = _stationary_bootstrap(x, trades, paths, MC_BLOCK, seed)
    iid = _stationary_bootstrap(x, trades, paths, 1.0000001, seed + 1)
    return {
        "market": market, "status": "ok", "dte": dte,
        "historical": hist,
        "mc_blocked": _path_stats(blocked),
        "mc_iid": _path_stats(iid),
        "mc_config": {"paths": paths, "trades_per_path": trades,
                      "mean_block": MC_BLOCK, "seed": seed},
        "stress": stress(market, dte=dte),
        # The selection rule's own output, persisted so a test can check the
        # configured threshold against it WITHOUT network access. A rule that
        # only runs when someone remembers to run it is not a rule.
        "threshold_rule": choose_threshold(market),
    }


def main() -> int:
    from .index_strangle_paper import MARKETS
    ap = argparse.ArgumentParser(prog="tradepro-index-strangle-sim")
    ap.add_argument("--markets", default="", help="comma list; default all")
    ap.add_argument("--dte", type=int, default=7)
    ap.add_argument("--paths", type=int, default=MC_PATHS)
    ap.add_argument("--trades", type=int, default=MC_TRADES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    names = ([m.strip().upper() for m in args.markets.split(",") if m.strip()]
             or list(MARKETS))
    res = []
    for m in names:
        if m not in MARKETS:
            log.warning("unknown market %s — known: %s", m, ", ".join(MARKETS))
            continue
        res.append(simulate(m, dte=args.dte, paths=args.paths, trades=args.trades))

    ok = [r for r in res if r.get("status") == "ok"]
    ok.sort(key=lambda r: -r["historical"]["return_risk_ratio"])

    payload = {"generated_utc": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
               "dte": args.dte, "results": res}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(payload, open(args.out, "w"), indent=1)

    if args.json:
        print(json.dumps(payload, indent=1))
        return 0

    print(f"\nINDEX SHORT STRANGLE — SIMULATION   ({args.dte}d expiry, closed same day)")
    print("  returns are % of collateral, so they compare across every market\n")
    print(f"  {'market':<11}{'n':>6}{'/yr':>6}{'win%':>7}{'mean%':>8}"
          f"{'worst%':>8}{'ratio':>7}   family")
    print("  " + "-" * 68)
    for r in ok:
        h, m = r["historical"], r["market"]
        print(f"  {m:<11}{h['n_trades']:>6}{h['trades_per_year']:>6.0f}{h['win_pct']:>7.1f}"
              f"{h['mean_pct']:>8.4f}{h['worst_pct']:>8.2f}{h['return_risk_ratio']:>7.2f}"
              f"   {MARKETS[m]['family']}")

    print(f"\n  FORWARD MONTE CARLO — {args.paths:,} paths x {args.trades} trades")
    print("  block bootstrap (mean block 5) preserves the clustering of bad days\n")
    print(f"  {'market':<11}{'median%':>9}{'p5%':>8}{'lose yr':>9}"
          f"{'medDD%':>8}{'p95DD%':>8}{'IID DD%':>9}")
    print("  " + "-" * 68)
    for r in ok:
        b, i = r["mc_blocked"], r["mc_iid"]
        print(f"  {r['market']:<11}{b['median_total_pct']:>9.2f}{b['p5_total_pct']:>8.2f}"
              f"{b['prob_losing_year_pct']:>8.1f}%{b['median_max_drawdown_pct']:>8.2f}"
              f"{b['p95_max_drawdown_pct']:>8.2f}{i['p95_max_drawdown_pct']:>9.2f}")
    print("\n  IID DD% is the SAME simulation with clustering removed. Where it is")
    print("  shallower than p95DD%, an independent-draw model would have understated")
    print("  the drawdown — that gap is why the blocked figure is the one to use.")

    print("\n  GATE-FAILURE STRESS — the same strangle on the worst sessions in")
    print("  history with the volatility gate IGNORED. Not a forecast: the cost of")
    print("  ONE crash day leaking through the filter.\n")
    print(f"  {'market':<11}{'gated worst':>13}{'ungated worst':>15}{'date':>13}"
          f"{'mean worst 25':>15}")
    print("  " + "-" * 68)
    for r in ok:
        s = r.get("stress") or {}
        if not s:
            continue
        gw = s.get("gated_worst_pct")
        print(f"  {r['market']:<11}{(f'{gw:.2f}%' if gw is not None else '-'):>13}"
              f"{s['worst_ungated_pct']:>14.2f}%{s['worst_ungated_date']:>13}"
              f"{s['mean_of_worst_n_pct']:>14.2f}%")

    print(f"\n  written to {args.out}")
    print("  READ THIS BEFORE QUOTING ANY NUMBER ABOVE:")
    print("  The Monte Carlo resamples trades the gate ALLOWED. No crash day is in")
    print("  that sample, so its p5 is not a worst case — the stress table is.")
    print("  Credits are Black-Scholes at the vol index, with NO bid-ask charged.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
