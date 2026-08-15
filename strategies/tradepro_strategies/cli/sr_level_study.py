"""Does price actually respect our support/resistance lines?

Graded against SR_LEVEL_STUDY_GATES_V1.md, committed aac6b03 BEFORE this ran.

Every definition here is the pre-registered one. Nothing may be swept after
seeing a result — a failure is the answer, not a prompt for different
parameters (gates file, "What a pass would and would not license").
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from ..quant_engine.sr_levels import CLUSTER_PCT, levels_asof

HORIZON = 5          # N bars, pre-registered
LOOKBACK_DAYS = 2600  # ~7 years of calendar days
WARMUP = 60          # bars before the first decision bar


def _touches(highs, lows, closes, t, levels, *, kind):
    """Pre-registered touch rule: the bar's range contains the level, and the
    level sits on the far side of the PRIOR close (i.e. price approached it)."""
    out = []
    prev_close = closes[t - 1]
    for lv in levels:
        L = lv.level
        if not (lows[t] <= L <= highs[t]):
            continue
        if kind == "resistance" and not (prev_close < L):
            continue
        if kind == "support" and not (prev_close > L):
            continue
        out.append(lv)
    return out


def _rejected(closes, t, *, kind) -> bool | None:
    """Rejection = price turned away over the pre-registered N-bar horizon."""
    if t + HORIZON >= len(closes):
        return None
    if kind == "resistance":
        return closes[t + HORIZON] < closes[t]
    return closes[t + HORIZON] > closes[t]


def _placebo_levels(real_levels, closes, t, rng, *, kind):
    """C2, the PRIMARY control: a synthetic line at the same distance from the
    prior close as each real level, but at a price with no pivot within
    clusterPct. Same count, same geometry, no structure — so 'real minus
    placebo' isolates the structure rather than ordinary mean reversion."""
    prev_close = closes[t - 1]
    reals = [lv.level for lv in real_levels]
    out = []
    for lv in real_levels:
        dist = abs(lv.level - prev_close)
        # Jitter the distance by ±20-60% so the placebo is not simply the real
        # level, while staying on the same side and a comparable distance away.
        scale = rng.choice([-1, 1]) * rng.uniform(0.2, 0.6)
        cand = (prev_close + dist * (1 + scale)) if kind == "resistance" \
            else (prev_close - dist * (1 + scale))
        if cand <= 0:
            continue
        # Must not accidentally BE a real level.
        if any(abs(cand - r) / cand <= CLUSTER_PCT for r in reals if r > 0):
            continue
        out.append(type(lv)(level=cand, touches=lv.touches))
    return out


def run(symbols: list[str], *, seed: int = 20260815) -> dict:
    from ..ibkr_bars import fetch_daily_bars_with_provenance

    rng = random.Random(seed)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    stats = {
        k: {"real_n": 0, "real_hit": 0, "plac_n": 0, "plac_hit": 0,
            "multi_n": 0, "multi_hit": 0, "single_n": 0, "single_hit": 0,
            "h1_n": 0, "h1_hit": 0, "h1_pn": 0, "h1_phit": 0,
            "h2_n": 0, "h2_hit": 0, "h2_pn": 0, "h2_phit": 0}
        for k in ("resistance", "support")
    }
    uncond = {"n": 0, "down": 0, "up": 0}
    used, skipped, src_counts = [], [], {}

    for sym in symbols:
        df, prov = fetch_daily_bars_with_provenance(
            sym, start, end, asset_class="us_etf", fetched_by="sr-study")
        if df is None or df.empty or len(df) < 300:
            df, prov = fetch_daily_bars_with_provenance(
                sym, start, end, asset_class="us_equity", fetched_by="sr-study")
        if df is None or df.empty or len(df) < 300:
            skipped.append(sym)
            continue
        cols = {c.lower() for c in df.columns}
        if not {"high", "low", "close"} <= cols:
            skipped.append(sym)
            continue
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        closes = df["close"].tolist()
        src_counts[prov.get("source") or "unknown"] = \
            src_counts.get(prov.get("source") or "unknown", 0) + 1
        used.append(sym)

        n = len(closes)
        mid = WARMUP + (n - HORIZON - WARMUP) // 2   # for the G4 half-split
        for t in range(WARMUP, n - HORIZON):
            uncond["n"] += 1
            if closes[t + HORIZON] < closes[t]:
                uncond["down"] += 1
            else:
                uncond["up"] += 1

            # CAUSAL: levels knowable at the close of bar t, nothing after.
            res_lv, sup_lv = levels_asof(highs, lows, t)
            for kind, lvls in (("resistance", res_lv), ("support", sup_lv)):
                s = stats[kind]
                hit_list = _touches(highs, lows, closes, t, lvls, kind=kind)
                if hit_list:
                    rej = _rejected(closes, t, kind=kind)
                    if rej is not None:
                        for lv in hit_list:
                            s["real_n"] += 1
                            s["real_hit"] += int(rej)
                            half = "h1" if t < mid else "h2"
                            s[f"{half}_n"] += 1
                            s[f"{half}_hit"] += int(rej)
                            if lv.touches >= 2:
                                s["multi_n"] += 1
                                s["multi_hit"] += int(rej)
                            else:
                                s["single_n"] += 1
                                s["single_hit"] += int(rej)
                # Placebo, measured identically on the same bar.
                plac = _placebo_levels(lvls, closes, t, rng, kind=kind)
                phit = _touches(highs, lows, closes, t, plac, kind=kind)
                if phit:
                    rej = _rejected(closes, t, kind=kind)
                    if rej is not None:
                        s["plac_n"] += len(phit)
                        s["plac_hit"] += int(rej) * len(phit)
                        half = "h1" if t < mid else "h2"
                        s[f"{half}_pn"] += len(phit)
                        s[f"{half}_phit"] += int(rej) * len(phit)

    return {"stats": stats, "uncond": uncond, "used": used,
            "skipped": skipped, "bar_sources": src_counts}


def _pct(hit, n):
    return (100.0 * hit / n) if n else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", help="comma list (default: the wheel universe)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        from .options_screen import DEFAULT_UNIVERSE
        syms = list(DEFAULT_UNIVERSE)
    if args.limit:
        syms = syms[: args.limit]

    print(f"S/R level study — {len(syms)} symbols, horizon {HORIZON} bars, "
          f"graded vs SR_LEVEL_STUDY_GATES_V1.md (aac6b03, committed BEFORE this run)")
    r = run(syms)
    st, un = r["stats"], r["uncond"]
    print(f"  symbols used {len(r['used'])}, skipped {len(r['skipped'])} "
          f"· bar sources {r['bar_sources']}")
    print(f"  unconditional 5-bar down-rate {_pct(un['down'], un['n']):.2f}%  "
          f"(C1 drift control, disclosure only; n={un['n']:,})\n")

    gates = []
    for kind, gname in (("resistance", "G1"), ("support", "G2")):
        s = st[kind]
        real = _pct(s["real_hit"], s["real_n"])
        plac = _pct(s["plac_hit"], s["plac_n"])
        edge = real - plac
        print(f"  {kind:11} real {real:6.2f}% (n={s['real_n']:,})  "
              f"placebo {plac:6.2f}% (n={s['plac_n']:,})  EDGE {edge:+.2f} pts")
        print(f"              multi-touch {_pct(s['multi_hit'], s['multi_n']):6.2f}% "
              f"(n={s['multi_n']:,})  single {_pct(s['single_hit'], s['single_n']):6.2f}% "
              f"(n={s['single_n']:,})")
        e1 = _pct(s["h1_hit"], s["h1_n"]) - _pct(s["h1_phit"], s["h1_pn"])
        e2 = _pct(s["h2_hit"], s["h2_n"]) - _pct(s["h2_phit"], s["h2_pn"])
        print(f"              half-1 edge {e1:+.2f} pts · half-2 edge {e2:+.2f} pts")
        gates.append((f"V0·{kind}", s["real_n"] >= 2000,
                      f"{s['real_n']:,} touch events ≥ 2,000"))
        gates.append((gname, edge >= 5.0, f"{kind} edge {edge:+.2f} pts ≥ +5.0"))
        gates.append((f"G3·{kind}",
                      _pct(s["multi_hit"], s["multi_n"]) >= _pct(s["single_hit"], s["single_n"]),
                      f"multi-touch {_pct(s['multi_hit'], s['multi_n']):.2f}% ≥ "
                      f"single {_pct(s['single_hit'], s['single_n']):.2f}%"))
        gates.append((f"G4·{kind}", (e1 > 0) == (e2 > 0),
                      f"half-1 {e1:+.2f} / half-2 {e2:+.2f} same sign"))

    print("\n══ GATES (SR_LEVEL_STUDY_GATES_V1.md, committed BEFORE this run) ══")
    for name, ok, detail in gates:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:14} {detail}")
    n_fail = sum(1 for _, ok, _ in gates if not ok)
    print(f"\n  {n_fail} gate(s) FAILED" if n_fail else "\n  ALL GATES PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
