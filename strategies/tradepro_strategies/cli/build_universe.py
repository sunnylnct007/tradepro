"""Build the tradeable universe from the bar store, on evidence.

Every symbol is measured against the criteria in `universe.py` and lands in
one of two lists — included, or excluded WITH A REASON. Nothing is dropped
silently, because "why isn't NVDA on the screen?" has to be answerable in one
command rather than by reading code.

Run it after a harvest, and commit the result. The universe is a decision, so
it belongs in git where it can be reviewed and blamed, not regenerated
implicitly at read time.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import os
import statistics as st

from ..universe import (MIN_PRICE, MIN_DOLLAR_VOLUME, MIN_SESSIONS, MAX_PHANTOM_BARS,
                        MIN_RECENT_COVERAGE, _instrument_ok, universe_path, poison_check)

log = logging.getLogger("tradepro.build_universe")
BASE_DIR = os.path.expanduser("~/.tradepro/bar_cache/us_etf")


def _load(sym: str):
    fs = sorted(glob.glob(f"{BASE_DIR}/{sym}/1d/*.parquet"))
    if not fs:
        return None
    import pandas as pd
    try:
        df = pd.concat([pd.read_parquet(f) for f in fs]).sort_index()
    except Exception:
        return None
    return df[~df.index.duplicated(keep="last")]


_SPY_CACHE: dict = {}


def _spy_returns():
    """SPY daily returns by date — the market leg for beta."""
    if "r" in _SPY_CACHE:
        return _SPY_CACHE["r"]
    df = _load("SPY")
    out = {}
    if df is not None:
        c = df["close"].tolist(); d = [str(x)[:10] for x in df.index]
        for i in range(1, len(c)):
            if c[i - 1] > 0:
                out[d[i]] = c[i] / c[i - 1] - 1
    _SPY_CACHE["r"] = out
    return out


def classify(df, c) -> dict:
    """Tags a suite run can select on — owner: "we can classify the high beta
    stocks etc ... run a suite of symbols in one go."

    Beta is measured against SPY over the last 252 sessions rather than taken
    from a vendor, so it is reproducible from the same store everything else
    reads. Volatility is ATR% because that is what the sleeves already size
    stops with; a name whose tier says "high" is one whose 8% stop is roughly
    two days of ordinary range, which is the thing worth knowing.
    """
    d = [str(x)[:10] for x in df.index]
    h = df["high"].tolist(); l = df["low"].tolist()
    spy = _spy_returns()

    def _beta(window):
        xs, ys = [], []
        for i in range(max(1, len(c) - window), len(c)):
            m = spy.get(d[i])
            if m is None or c[i - 1] <= 0:
                continue
            xs.append(m); ys.append(c[i] / c[i - 1] - 1)
        if len(xs) < 60:
            return None
        mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        var = sum((a - mx) ** 2 for a in xs)
        return round(cov / var, 2) if var > 0 else None

    # TWO WINDOWS, and the TIER comes from the LONGER one.
    #
    # A 252-day beta in this dataset is a regime reading, not a property of
    # the stock. Measured over the last year XLP -- consumer staples, literally
    # a subset of the S&P -- correlates -0.04 with SPY, because the index is
    # being driven by semiconductors and defensives have decoupled. Over all
    # history that same correlation is 0.70. (Alignment is not the problem:
    # IVV correlates 0.97 with SPY on the same window.)
    #
    # Tiering on the recent number would file BRK-B as "low beta" on the
    # strength of one unusual year. Momentum v3 was rejected for exactly that
    # -- mistaking a regime for a property -- so the stable window decides the
    # label, the recent one is reported beside it, and a big gap is FLAGGED
    # rather than averaged away.
    beta = _beta(252)
    beta_long = _beta(1000)
    beta_for_tier = beta_long if beta_long is not None else beta
    beta_unstable = (beta is not None and beta_long is not None
                     and abs(beta - beta_long) >= 0.5)

    i = len(c) - 1
    trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
           for j in range(max(1, i - 13), i + 1)]
    atr_pct = round(100 * (sum(trs) / len(trs)) / c[i], 2) if trs and c[i] else None

    def tier(v, lo, hi):
        return None if v is None else ("low" if v < lo else "high" if v >= hi else "mid")

    return {"beta": beta, "beta_long": beta_long,
            "beta_tier": tier(beta_for_tier, 0.8, 1.3),
            "beta_unstable": beta_unstable,
            "atr_pct": atr_pct, "volatility_tier": tier(atr_pct, 1.5, 3.5)}


# ── PHASE 2: ONE UNIVERSE, TAGGED (1 Sep 2026) ──────────────────────────────
#
# Owner: "we want a coherant and trustworthy data and not scattered data ... as
# user i dont have to think many screens".
#
# There were FOUR definitions of "what we screen":
#   1. this file's 244 committed names          (the real definition)
#   2. /api/universes/{name}, DB-backed         (large_50, high_beta, +12 more)
#   3. options_screen.DEFAULT_UNIVERSE          (~82, hand-curated in code)
#   4. ScreenerEndpoints.Universe               (30 hardcoded ticker+conid pairs)
#
# A screen running on a different list than its neighbours is how "0 of 30" and
# "21 of 82" looked like a strategy disagreement when it was a universe
# disagreement. Tags make the 244 the single source and everything else a FILTER
# over it.
#
# WHEEL IS SEEDED FROM THE EXISTING CURATED LIST, deliberately. Deriving it from
# price/liquidity rules would silently change which names get screened, and the
# owner's board is a live trading surface — a consolidation must not also be a
# behaviour change. The criteria that should eventually replace the hand-list
# are noted below; swapping them in is a separate, reviewable decision.
# IMPORTED, NOT COPIED. My first draft pasted a TRUNCATED copy of the wheel
# list here — 20 of 82 names — which would have silently shrunk what the wheel
# screens. That is exactly the failure this phase exists to end, committed while
# ending it. There is one list; everything else points at it.
from ..universe import WHEEL_SLEEVE as _WHEEL_SEED

_LARGE_N = 50


def _apply_tags(rows: list[dict]) -> list[dict]:
    """Attach `tags` to each included symbol. Derived, never hand-maintained.

    large_50   the N most liquid by median dollar volume — "the big names",
               which is what that label has always meant informally.
    high_beta  beta_tier already computed above; this just names it as a tag so
               a consumer selects by tag rather than by reaching into a field.
    wheel      seeded from the curated list (see _WHEEL_SEED).
    """
    ranked = sorted(
        (r for r in rows if r.get("dollar_volume_median") is not None),
        key=lambda r: r["dollar_volume_median"], reverse=True)
    large = {r["symbol"] for r in ranked[:_LARGE_N]}
    for r in rows:
        tags = []
        sym = r.get("symbol")
        if sym in large:
            tags.append("large_50")
        if r.get("beta_tier") == "high":
            tags.append("high_beta")
        if sym in _WHEEL_SEED:
            tags.append("wheel")
        r["tags"] = tags
    return rows


def _tag_counts(rows: list[dict]) -> dict:
    from collections import Counter
    c: Counter = Counter()
    for r in rows:
        for t in (r.get("tags") or []):
            c[t] += 1
    return dict(sorted(c.items()))


def assess(sym: str) -> dict:
    """Measure one symbol against every criterion. Returns a verdict dict."""
    ok, why = _instrument_ok(sym)
    if not ok:
        return {"symbol": sym, "include": False, "reason": why, "class": "instrument"}

    df = _load(sym)
    if df is None or df.empty:
        return {"symbol": sym, "include": False, "reason": "no stored daily bars",
                "class": "data"}
    c = df["close"].dropna().tolist()
    if len(c) < MIN_SESSIONS:
        return {"symbol": sym, "include": False,
                "reason": f"only {len(c)} stored sessions, need {MIN_SESSIONS}",
                "class": "history"}

    _vols = df["volume"].tolist() if "volume" in df.columns else None
    clean, ratio = poison_check(c, _vols)
    if not clean:
        return {"symbol": sym, "include": False,
                "reason": f"suspect series — {ratio} phantom bars (unchanged close on ZERO "
                          f"volume), consistent with a wrong venue or contract",
                "class": "quality"}

    price = c[-1]
    if price < MIN_PRICE:
        return {"symbol": sym, "include": False,
                "reason": f"price {price:.2f} below the {MIN_PRICE:.2f} floor — penny stock",
                "class": "price"}

    dv = None
    if "volume" in df.columns:
        v = df["volume"].tolist()
        dvs = [c[i] * v[i] for i in range(max(0, len(c) - 60), len(c))
               if v[i] and c[i]]
        dv = st.median(dvs) if dvs else None
    if dv is None:
        return {"symbol": sym, "include": False,
                "reason": "no volume data — liquidity cannot be established",
                "class": "liquidity"}
    if dv < MIN_DOLLAR_VOLUME:
        return {"symbol": sym, "include": False,
                "reason": f"median daily turnover ${dv/1e6:.1f}M below the "
                          f"${MIN_DOLLAR_VOLUME/1e6:.0f}M floor — too thin to fill against",
                "class": "liquidity"}

    dates = [str(x)[:10] for x in df.index][-60:]
    span = ((_dt.date.fromisoformat(dates[-1]) - _dt.date.fromisoformat(dates[0])).days
            if len(dates) > 1 else 0)
    expected = max(1, int(span * 5 / 7))
    coverage = min(1.0, len(dates) / expected) if expected else 0.0
    if coverage < MIN_RECENT_COVERAGE:
        return {"symbol": sym, "include": False,
                "reason": f"recent coverage {coverage:.0%} — the harvester is missing "
                          f"sessions on this name",
                "class": "coverage"}

    src = {}
    if "source" in df.columns:
        for s in df["source"].dropna().tail(60).tolist():
            src[str(s)] = src.get(str(s), 0) + 1

    return {"symbol": sym, "include": True, "reason": None, "class": "ok",
            "price": round(price, 2),
            "dollar_volume_median": int(dv),
            "sessions": len(c),
            "first_bar": str(df.index[0])[:10],
            "last_bar": str(df.index[-1])[:10],
            "poison_ratio": round(ratio, 2),
            "recent_sources": src,
            **classify(df, c)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="write the universe file")
    ap.add_argument("--show-excluded", action="store_true")
    ap.add_argument("--push", action="store_true",
                    help="publish to the API so the desk can offer the names and tiers")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    syms = sorted(os.listdir(BASE_DIR))
    rows = [assess(s) for s in syms]
    inc = [r for r in rows if r["include"]]
    exc = [r for r in rows if not r["include"]]
    inc.sort(key=lambda r: -r["dollar_volume_median"])

    print(f"scanned {len(syms)} · INCLUDED {len(inc)} · excluded {len(exc)}\n")
    print(f"criteria: price >= ${MIN_PRICE:.2f} · turnover >= ${MIN_DOLLAR_VOLUME/1e6:.0f}M/day"
          f" · >= {MIN_SESSIONS} sessions · <= {MAX_PHANTOM_BARS} phantom bars"
          f" · coverage >= {MIN_RECENT_COVERAGE:.0%}\n")
    by_class: dict[str, int] = {}
    for r in exc:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    print("excluded by reason:")
    for k, n in sorted(by_class.items(), key=lambda x: -x[1]):
        print(f"  {k:<12}{n:>4}")
    if a.show_excluded:
        print()
        for r in sorted(exc, key=lambda r: (r["class"], r["symbol"])):
            print(f"  {r['symbol']:<10}{r['class']:<12}{r['reason']}")
    tiers: dict[str, int] = {}
    for r in inc:
        k = f"{r.get('beta_tier') or '?'}-beta / {r.get('volatility_tier') or '?'}-vol"
        tiers[k] = tiers.get(k, 0) + 1
    print("\nincluded, by classification:")
    for k, n in sorted(tiers.items(), key=lambda x: -x[1]):
        print(f"  {k:<28}{n:>4}")

    print(f"\ntop 15 by turnover:")
    print(f"  {'sym':<8}{'price':>10}{'$vol/day':>12}{'sessions':>10}{'beta':>8}{'ATR%':>8}")
    for r in inc[:15]:
        print(f"  {r['symbol']:<8}{r['price']:>10.2f}{r['dollar_volume_median']/1e6:>11.0f}M"
              f"{r['sessions']:>10}{(r.get('beta') if r.get('beta') is not None else '—'):>8}"
              f"{(str(r.get('atr_pct')) + '%') if r.get('atr_pct') else '—':>8}")

    if a.write:
        out = {
            "as_of": _dt.datetime.now(_dt.UTC).isoformat(),
            "criteria": {"min_price": MIN_PRICE, "min_dollar_volume": MIN_DOLLAR_VOLUME,
                         "min_sessions": MIN_SESSIONS, "max_phantom_bars": MAX_PHANTOM_BARS,
                         "min_recent_coverage": MIN_RECENT_COVERAGE},
            "counts": {"scanned": len(syms), "included": len(inc), "excluded": len(exc)},
            "tags": _tag_counts(inc),
            "symbols": _apply_tags(inc),
            "excluded": [{"symbol": r["symbol"], "class": r["class"], "reason": r["reason"]}
                         for r in sorted(exc, key=lambda r: r["symbol"])],
        }
        p = universe_path(); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2) + "\n")
        print(f"\nwrote {p}")
        if a.push:
            try:
                import requests
                from .push_to_api import load_credentials
                base, token = load_credentials()
                if base:
                    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups",
                                      json={"universe": "universe", "label": "latest",
                                            "uploaded_by": os.uname().nodename,
                                            "artifact": {"kind": "tradeable_universe", **out}},
                                      headers={"Authorization": f"Bearer {token}"} if token else {},
                                      timeout=60)
                    print(f"push -> HTTP {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                log.warning("push failed: %s", exc)
    else:
        print("\n(dry run — pass --write to save)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
