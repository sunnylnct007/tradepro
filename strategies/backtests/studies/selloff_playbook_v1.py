"""Sell-off playbook — grades SELLOFF_PLAYBOOK_GATES_V1.md and nothing else.

    uv run --project strategies python strategies/backtests/studies/selloff_playbook_v1.py

The gates file was committed BEFORE this grader. Every definition below is the
one frozen there; this file adds no judgement, only arithmetic. It emits NO
candidates and NO signals. Output: one JSON artifact under ~/.tradepro/research
and a printed summary with each gate marked PASS/FAIL.

Three honesty rules carried from the 29 Aug earnings-recovery study:
  * every trade is graded against a NULL on the same stock (market drift is
    not an edge);
  * no stop — the question is recovery at a horizon, and a stop answers a
    different question;
  * everything that could not be graded is COUNTED, not dropped.
"""
from __future__ import annotations

import json
import os
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import date

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from tradepro_strategies.daily_bars import load_daily  # noqa: E402

GATES_DOC = "SELLOFF_PLAYBOOK_GATES_V1.md"
SECTOR_ETF = {
    "Materials": "XLB", "Communication Services": "XLC", "Energy": "XLE",
    "Financials": "XLF", "Industrials": "XLI", "Information Technology": "XLK",
    "Consumer Staples": "XLP", "Real Estate": "XLRE", "Utilities": "XLU",
    "Health Care": "XLV", "Consumer Discretionary": "XLY",
}
MARKET = ["SPY", "QQQ"]
DROP = 0.08           # episode: close <= 0.92 x 20-session high
HIGH_WIN = 20
EPISODE_GAP = 40      # sessions before the same ETF can start a new episode
CLUSTER_DAYS = 14     # calendar-day proxy for "within 10 sessions"
TREND_LAG = 20        # trend condition measured 20 sessions before the episode
SMA_N = 200
HORIZONS = (20, 40, 60)
PUT_OTM = 0.90
PUT_DTE = 21
COST = 0.001          # 10 bps per side
NULL_DRAWS = 3
NULL_GAP = 40
BETA_WIN = 63
SPLIT_DATE = "2016-01-01"
random.seed(20261110)

UNIVERSE = os.path.join(os.path.dirname(__file__), "..", "..", "universe", "tradeable.json")
OUT = os.path.expanduser("~/.tradepro/research/selloff_playbook_v1.json")


def net(r: float) -> float:
    return r - 2 * COST


def load(sym):
    df = load_daily(sym)
    if df is None or len(df) < SMA_N + 80:
        return None
    c = df["close"].to_numpy(dtype=float)
    d = [str(x)[:10] for x in df.index]
    return c, d, {dt: i for i, dt in enumerate(d)}


def episodes(c, d):
    """First session ≤ 0.92 x rolling 20-session high; 40-session refractory."""
    hi = np.array([c[max(0, i - HIGH_WIN + 1):i + 1].max() for i in range(len(c))])
    dd = c / hi - 1
    out, i = [], HIGH_WIN
    while i < len(c):
        if dd[i] <= -DROP:
            out.append((d[i], float(dd[i])))
            i += EPISODE_GAP
        else:
            i += 1
    return out


def cluster(all_eps):
    """all_eps: list of (date, etf, dd). Clusters episodes within CLUSTER_DAYS."""
    eps = sorted(all_eps)
    clusters, cur = [], []
    for e in eps:
        if cur and (date.fromisoformat(e[0]) - date.fromisoformat(cur[0][0])).days > CLUSTER_DAYS:
            clusters.append(cur); cur = []
        cur.append(e)
    if cur:
        clusters.append(cur)
    return clusters


def sma(c, i, n=SMA_N):
    return float(c[i - n + 1:i + 1].mean()) if i >= n - 1 else None


def own_dd(c, i):
    return float(c[i] / c[max(0, i - HIGH_WIN + 1):i + 1].max() - 1)


def main() -> int:
    raw_u = json.load(open(UNIVERSE))
    rows = raw_u.get("symbols", []) if isinstance(raw_u, dict) else raw_u
    members = defaultdict(list)
    for r in rows:
        if isinstance(r, dict) and r.get("include") and r.get("sector") in SECTOR_ETF:
            members[SECTOR_ETF[r["sector"]]].append(r["symbol"])

    etf_bars = {e: load(e) for e in list(SECTOR_ETF.values()) + MARKET}
    missing_etf = [e for e, b in etf_bars.items() if b is None]

    # ── episodes ────────────────────────────────────────────────────────────
    all_eps = []
    per_etf = {}
    for e, b in etf_bars.items():
        if b is None:
            continue
        eps = episodes(b[0], b[1])
        per_etf[e] = eps
        all_eps += [(dt, e, dd) for dt, dd in eps]
    clusters = cluster(all_eps)
    cluster_id = {}
    for k, cl in enumerate(clusters):
        for dt, e, _ in cl:
            cluster_id[(dt, e)] = k

    # episode dates per ETF as sets for the null exclusion
    spy_eps = {dt for dt, _ in per_etf.get("SPY", [])}

    # ── S1 / S2 / S4 trades ─────────────────────────────────────────────────
    trades = []           # one dict per (episode, stock)
    ungraded = defaultdict(int)
    stock_cache = {}
    for e, eps in per_etf.items():
        if e in MARKET:
            continue
        ec, ed, eix = etf_bars[e]
        for sym in members.get(e, []):
            if sym not in stock_cache:
                stock_cache[sym] = load(sym)
            sb = stock_cache[sym]
            if sb is None:
                ungraded["no_bars"] += len(eps); continue
            c, d, ix = sb
            # sessions to exclude for the null: ±NULL_GAP around any episode of its ETF or SPY
            excl = set()
            for dt in list({x for x, _ in eps}) + list(spy_eps):
                j = ix.get(dt)
                if j is not None:
                    excl.update(range(j - NULL_GAP, j + NULL_GAP + 1))
            for dt, sec_dd in eps:
                i = ix.get(dt)
                if i is None:
                    ungraded["episode_date_not_in_stock_bars"] += 1; continue
                if i - TREND_LAG < SMA_N or i + max(HORIZONS) >= len(c):
                    ungraded["insufficient_history_or_future"] += 1; continue
                s200 = sma(c, i - TREND_LAG)
                if s200 is None or c[i - TREND_LAG] <= s200:
                    ungraded["trend_condition_not_met"] += 1; continue
                if own_dd(c, i) > sec_dd:
                    ungraded["did_not_participate"] += 1; continue
                entry = c[i]
                rets = {h: net(c[i + h] / entry - 1) for h in HORIZONS}
                put_ok = c[i + PUT_DTE] >= PUT_OTM * entry
                breach = float(c[i + 1:i + PUT_DTE + 1].min() / (PUT_OTM * entry) - 1)
                # beta to the sector ETF over 63 sessions ending TREND_LAG before
                beta = None
                k0, k1 = i - TREND_LAG - BETA_WIN, i - TREND_LAG
                if k0 > 0:
                    sr = np.diff(c[k0:k1 + 1]) / c[k0:k1]
                    ej = [eix.get(x) for x in d[k0:k1 + 1]]
                    if all(j is not None for j in ej):
                        er = np.diff(ec[ej]) / ec[ej][:-1]
                        if er.var() > 0 and len(er) == len(sr):
                            beta = float(np.cov(sr, er)[0, 1] / er.var())
                # null: random non-episode sessions with the same trend condition
                nulls = {h: [] for h in HORIZONS}; null_put = []
                cands = [j for j in range(SMA_N + TREND_LAG, len(c) - max(HORIZONS))
                         if j not in excl]
                random.shuffle(cands)
                taken = 0
                for j in cands:
                    sj = sma(c, j - TREND_LAG)
                    if sj is None or c[j - TREND_LAG] <= sj:
                        continue
                    for h in HORIZONS:
                        nulls[h].append(net(c[j + h] / c[j] - 1))
                    null_put.append(1.0 if c[j + PUT_DTE] >= PUT_OTM * c[j] else 0.0)
                    taken += 1
                    if taken >= NULL_DRAWS:
                        break
                if taken == 0:
                    ungraded["no_null_available"] += 1; continue
                trades.append({
                    "etf": e, "symbol": sym, "date": dt, "cluster": cluster_id[(dt, e)],
                    "sector_dd": sec_dd, "tier": "B" if sec_dd <= -0.12 else "A",
                    "ret": rets, "null": {h: st.mean(v) for h, v in nulls.items()},
                    "put_ok": bool(put_ok), "null_put_ok": st.mean(null_put),
                    "breach": breach, "beta": beta,
                })

    # ── grade S1 ────────────────────────────────────────────────────────────
    def edge(ts, h):
        return st.mean(t["ret"][h] - t["null"][h] for t in ts) * 100 if ts else None

    n = len(trades)
    etfs = {t["etf"] for t in trades}
    cl = {t["cluster"] for t in trades}
    s1 = {}
    for h in HORIZONS:
        rs = [t["ret"][h] for t in trades]
        s1[h] = {
            "n": n,
            "mean_pct": st.mean(rs) * 100 if rs else None,
            "null_mean_pct": st.mean(t["null"][h] for t in trades) * 100 if trades else None,
            "edge_pt": edge(trades, h),
            "win_pct": 100 * sum(r > 0 for r in rs) / n if n else None,
            "p5_pct": float(np.percentile(rs, 5)) * 100 if rs else None,
            "worst_pct": min(rs) * 100 if rs else None,
        }
    syms_sorted = sorted({t["symbol"] for t in trades})
    half = set(syms_sorted[: len(syms_sorted) // 2])
    cells = {}
    for tname, tf in (("pre2016", lambda t: t["date"] < SPLIT_DATE), ("post2016", lambda t: t["date"] >= SPLIT_DATE)):
        for sname, sf in (("symA", lambda t: t["symbol"] in half), ("symB", lambda t: t["symbol"] not in half)):
            sub = [t for t in trades if tf(t) and sf(t)]
            cells[f"{tname}×{sname}"] = {"n": len(sub), "edge40_pt": edge(sub, 40)}
    gates = {
        "S1-0": n >= 300 and len(etfs) >= 8 and len(cl) >= 15,
        "S1-1": (s1[20]["edge_pt"] or -99) >= 1.0,
        "S1-2": (s1[40]["edge_pt"] or -99) >= 1.0,
        "S1-3": (s1[40]["win_pct"] or 0) >= 55,
        "S1-4": (s1[40]["p5_pct"] or -99) >= -15,
        "S1-5": all((v["edge40_pt"] or -99) > 0 for v in cells.values()) and all(v["n"] > 0 for v in cells.values()),
    }
    # S1b — the ETF itself
    s1b = {}
    for e, eps in per_etf.items():
        c, d, ix = etf_bars[e]
        rs = defaultdict(list)
        for dt, _ in eps:
            i = ix[dt]
            if i + 60 < len(c):
                for h in HORIZONS:
                    rs[h].append(net(c[i + h] / c[i] - 1))
        s1b[e] = {h: (st.mean(v) * 100 if v else None) for h, v in rs.items()} | {"n": len(rs[20])}

    # ── grade S2 ────────────────────────────────────────────────────────────
    p_ok = 100 * sum(t["put_ok"] for t in trades) / n if n else None
    p_null = 100 * st.mean(t["null_put_ok"] for t in trades) if trades else None
    worst_breach = min(t["breach"] for t in trades) * 100 if trades else None
    gates |= {
        "S2-0": n >= 300,
        "S2-1": (p_ok or 0) >= 90,
        "S2-2": (p_ok or 0) - (p_null or 0) >= 2.0,
        "S2-3": (worst_breach or -99) >= -20,
    }
    s2 = {"n": n, "p_expires_worthless_pct": p_ok, "null_pct": p_null, "edge_pt": (p_ok or 0) - (p_null or 0),
          "worst_breach_below_strike_pct": worst_breach}

    # ── S3 descriptive: concentrated vs capped vs capped+reserve ───────────
    s3_rows = []
    sector_etfs = list(SECTOR_ETF.values())
    for k, clus in enumerate(clusters):
        secs = [x for x in clus if x[1] not in MARKET]
        if not secs:
            continue
        dt, hardest, _ = min(secs, key=lambda x: x[2])
        hc, hd, hix = etf_bars[hardest]
        i = hix[dt]
        i0 = i - TREND_LAG
        if i0 < 0 or i0 + 20 >= len(hc):
            continue
        start_dt, window = hd[i0], hd[i0:i0 + 21]

        def path(e):
            c, d, ix = etf_bars[e]
            js = [ix.get(x) for x in window]
            if any(j is None for j in js):
                return None
            return c[js] / c[js[0]]
        conc = path(hardest)
        others = [p for p in (path(e) for e in sector_etfs if e != hardest) if p is not None]
        if conc is None or len(others) < 6:
            continue
        rest = np.mean(others, axis=0)
        capped = 0.45 * conc + 0.55 * rest
        reserve = 0.8 * capped + 0.2
        s3_rows.append({
            "cluster": k, "start": start_dt, "hardest": hardest,
            "concentrated_dd_pct": (conc.min() - 1) * 100,
            "capped_dd_pct": (capped.min() - 1) * 100,
            "capped_reserve_dd_pct": (reserve.min() - 1) * 100,
        })
    def med(key):
        v = [r[key] for r in s3_rows]
        return (st.median(v), min(v)) if v else (None, None)
    s3 = {"clusters": len(s3_rows),
          "concentrated": dict(zip(("median_dd_pct", "worst_dd_pct"), med("concentrated_dd_pct"))),
          "capped_45": dict(zip(("median_dd_pct", "worst_dd_pct"), med("capped_dd_pct"))),
          "capped_45_plus_20_cash": dict(zip(("median_dd_pct", "worst_dd_pct"), med("capped_reserve_dd_pct"))),
          "avoided_loss_median_pt": (st.median([r["concentrated_dd_pct"] - r["capped_dd_pct"] for r in s3_rows]) if s3_rows else None)}

    # ── S4 descriptive: beta terciles ──────────────────────────────────────
    bt = [t for t in trades if t["beta"] is not None]
    s4 = {}
    if len(bt) >= 30:
        bs = sorted(t["beta"] for t in bt)
        lo, hi = bs[len(bs) // 3], bs[2 * len(bs) // 3]
        for name, f in (("low", lambda b: b < lo), ("mid", lambda b: lo <= b < hi), ("high", lambda b: b >= hi)):
            sub = [t for t in bt if f(t["beta"])]
            s4[name] = {"n": len(sub), "beta_range": [min(t["beta"] for t in sub), max(t["beta"] for t in sub)] if sub else None,
                        "edge40_pt": edge(sub, 40), "edge20_pt": edge(sub, 20)}

    art = {
        "gates_doc": GATES_DOC, "as_of": date.today().isoformat(),
        "universe": {"constituents_with_sector": sum(len(v) for v in members.values()),
                     "sector_etfs_missing_bars": missing_etf},
        "episodes": {"raw": len(all_eps), "clustered": len(clusters),
                     "per_etf": {e: len(v) for e, v in per_etf.items()}},
        "trades": n, "trades_by_tier": {"A": sum(t["tier"] == "A" for t in trades), "B": sum(t["tier"] == "B" for t in trades)},
        "ungraded": dict(ungraded),
        "S1": {str(h): s1[h] for h in HORIZONS} | {"two_split_edge40": cells, "etfs": sorted(etfs), "clusters": len(cl)},
        "S1b_etf_itself": s1b,
        "S2": s2, "S3": s3, "S4": s4,
        "gates": gates,
        "S1_pass": all(gates[k] for k in gates if k.startswith("S1")),
        "S2_pass": all(gates[k] for k in gates if k.startswith("S2")),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(art, open(OUT, "w"), indent=2, default=float)

    # ── print ───────────────────────────────────────────────────────────────
    f = lambda x: "n/a" if x is None else f"{x:+.2f}"
    print(f"\nSELL-OFF PLAYBOOK v1 — {art['as_of']}  (artifact: {OUT})")
    print(f"episodes raw {len(all_eps)} / clustered {len(clusters)} · trades {n} across {len(etfs)} ETFs, {len(cl)} clusters"
          f" · tier A {art['trades_by_tier']['A']} / tier B {art['trades_by_tier']['B']}")
    print("ungraded:", dict(ungraded))
    print("\nS1 — buy constituents at the episode close (net of 20 bps)")
    print(f"{'h':>4} {'mean%':>8} {'null%':>8} {'edge pt':>8} {'win%':>6} {'p5%':>7} {'worst%':>8}")
    for h in HORIZONS:
        r = s1[h]
        print(f"{h:>4} {f(r['mean_pct']):>8} {f(r['null_mean_pct']):>8} {f(r['edge_pt']):>8} {f(r['win_pct']):>6} {f(r['p5_pct']):>7} {f(r['worst_pct']):>8}")
    print("two-split edge@40:", {k: (v["n"], f(v["edge40_pt"])) for k, v in cells.items()})
    print("\nS1b — the ETF itself (mean % at 20/40/60):")
    for e, v in s1b.items():
        print(f"  {e:<5} n={v['n']:>3}  {f(v.get(20))} / {f(v.get(40))} / {f(v.get(60))}")
    print(f"\nS2 — 10%-OTM put, 21 sessions: P(expires worthless) {f(p_ok)}% vs null {f(p_null)}% (edge {f(s2['edge_pt'])}pt); worst close vs strike {f(worst_breach)}%")
    print(f"\nS3 — 20-session drawdown of the book you already held when the sell-off hit ({len(s3_rows)} clusters):")
    for k, v in s3.items():
        if isinstance(v, dict):
            print(f"  {k:<24} median {f(v['median_dd_pct'])}%  worst {f(v['worst_dd_pct'])}%")
    print(f"  avoided loss (concentrated − capped), median: {f(s3['avoided_loss_median_pt'])} pt")
    print("\nS4 — S1 edge vs null by pre-episode beta tercile:")
    for k, v in s4.items():
        print(f"  {k:<5} n={v['n']:>4} beta {v['beta_range'][0]:+.2f}..{v['beta_range'][1]:+.2f}  edge20 {f(v['edge20_pt'])}  edge40 {f(v['edge40_pt'])}")
    print("\nGATES")
    for k, v in gates.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    print(f"\nS1 {'PASSES' if art['S1_pass'] else 'FAILS'} · S2 {'PASSES' if art['S2_pass'] else 'FAILS'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
