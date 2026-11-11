"""What-if engine — conditional forward returns against a same-stock null.

Owner, 11 Nov 2026: *"we should be able to read patterns, correlate different
data and find some winning combinations … like a what-if scenario"* — after
asking whether five months had proved anything.

Forty-seven hand-written studies live under backtests/studies/. Every one has
the same shape: a CONDITION on a symbol-day, a HORIZON, a NULL arm on the same
stock, twenty years of settled bars, and a day of my time. This module is that
shape as a function, so a question costs a minute and is archived with its
date, so a month of questions can be read in one sitting.

What it is NOT: a strategy. It emits no signals. A result here is the START of
a pre-registered study (gates committed before the run), never a reason to
trade — the 29 Aug / 10 Nov protocol stands.

Honesty rules, inherited from those studies and enforced here:
  * every trade is graded against a NULL on the SAME stock — random sessions
    at least `null_gap` sessions from any matching session. Market drift is
    not an edge, and a stock that rose 300% makes every condition look good.
  * no stop. The question is "what happened over h sessions", nothing else.
  * everything that could not be graded is COUNTED and reported.
  * one entry per symbol per matching run: a condition that stays true for
    ten sessions is one trade, taken on the first, not ten overlapping ones.
  * costs: 10 bps per side on every arm, null included.

Conditions are small named predicates over (bars, index); they AND together.
See CONDITIONS for the registry and `describe()` for the owner-facing list.
"""
from __future__ import annotations

import json
import random
import re
import statistics as st
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import numpy as np

from .sector_stress import MARKET_ETFS, SECTOR_ETF, sector_of_universe

COST = 0.001
HIGH_WIN = 20
SMA_N = 200
BETA_WIN = 63
NULL_DRAWS = 3
NULL_GAP = 40
HORIZONS_DEFAULT = (5, 10, 20, 40, 60)
SPLIT_DATE = "2016-01-01"
ARCHIVE_DIR = Path("~/.tradepro/research/whatif").expanduser()


def net(r: float) -> float:
    return r - 2 * COST


# ── bars ────────────────────────────────────────────────────────────────────

@dataclass
class Series:
    sym: str
    c: np.ndarray                 # closes
    d: list[str]                  # ISO dates
    ix: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.ix = {dt: i for i, dt in enumerate(self.d)}


def _to_series(sym: str, df) -> Series | None:
    if df is None or len(df) < SMA_N + 80:
        return None
    return Series(sym, df["close"].to_numpy(dtype=float), [str(x)[:10] for x in df.index])


# ── context: everything a condition may look at ─────────────────────────────

@dataclass
class Context:
    stock: Series
    sector_etf: Series | None          # the stock's own sector ETF, if any
    market: dict[str, Series]          # SPY/QQQ
    events: dict[str, list[str]]       # kind -> sorted ISO dates (MARKET-wide)
    sector_name: str | None


def _sma(c, i, n):
    return float(c[i - n + 1:i + 1].mean()) if i >= n - 1 else None


def _dd_from_high(c, i, w=HIGH_WIN):
    return float(c[i] / c[max(0, i - w + 1):i + 1].max() - 1)


def _rsi(c, i, n=14):
    if i < n:
        return None
    diff = np.diff(c[i - n:i + 1])
    up, dn = diff[diff > 0].sum(), -diff[diff < 0].sum()
    if up + dn == 0:
        return 50.0
    return float(100 * up / (up + dn))


def _beta(ctx: Context, i, lag=0):
    """63-session beta of the stock to its sector ETF, window ending `lag`
    sessions before i (no look-ahead when lag>0)."""
    e = ctx.sector_etf
    if e is None:
        return None
    k1, k0 = i - lag, i - lag - BETA_WIN
    if k0 <= 0:
        return None
    c, d = ctx.stock.c, ctx.stock.d
    ej = [e.ix.get(x) for x in d[k0:k1 + 1]]
    if any(j is None for j in ej):
        return None
    sr = np.diff(c[k0:k1 + 1]) / c[k0:k1]
    er = np.diff(e.c[ej]) / e.c[ej][:-1]
    if er.var() <= 0 or len(er) != len(sr):
        return None
    return float(np.cov(sr, er)[0, 1] / er.var())


def _etf_dd_on(etf: Series | None, dt: str):
    if etf is None:
        return None
    j = etf.ix.get(dt)
    return None if j is None else _dd_from_high(etf.c, j)


def _event_within(ctx: Context, i, n, kind):
    """A MARKET event of `kind` ("any" = all kinds) within the NEXT n sessions."""
    if i + n >= len(ctx.stock.d):
        return None
    lo, hi = ctx.stock.d[i], ctx.stock.d[i + n]
    kinds = ctx.events.keys() if kind == "any" else [kind]
    return any(lo < x <= hi for k in kinds for x in ctx.events.get(k, []))


# ── the registry ─────────────────────────────────────────────────────────────
# name -> (fn(ctx, i, *args) -> bool|None, doc). None = "cannot evaluate" and
# the symbol-day is COUNTED as ungraded, never silently treated as False.

Cond = Callable[..., bool | None]
CONDITIONS: dict[str, tuple[Cond, str]] = {}


def _cond(name: str, doc: str):
    def deco(fn):
        CONDITIONS[name] = (fn, doc)
        return fn
    return deco


@_cond("trend_up", "close above its own 200-session SMA")
def _trend_up(ctx, i):
    s = _sma(ctx.stock.c, i, SMA_N)
    return None if s is None else bool(ctx.stock.c[i] > s)


@_cond("trend_down", "close below its own 200-session SMA")
def _trend_down(ctx, i):
    s = _sma(ctx.stock.c, i, SMA_N)
    return None if s is None else bool(ctx.stock.c[i] <= s)


@_cond("own_dd_ge:X", "the stock is at least X% below its own 20-session high (e.g. own_dd_ge:10)")
def _own_dd_ge(ctx, i, x):
    return bool(_dd_from_high(ctx.stock.c, i) <= -float(x) / 100)


@_cond("new_high", "close is a new 20-session high")
def _new_high(ctx, i):
    return bool(_dd_from_high(ctx.stock.c, i) >= 0)


@_cond("sector_stressed", "its sector ETF is ≥ 8% below its 20-session high (the sector_selloff flag)")
def _sector_stressed(ctx, i):
    dd = _etf_dd_on(ctx.sector_etf, ctx.stock.d[i])
    return None if dd is None else bool(dd <= -0.08)


@_cond("sector_quiet", "its sector ETF is within 3% of its 20-session high")
def _sector_quiet(ctx, i):
    dd = _etf_dd_on(ctx.sector_etf, ctx.stock.d[i])
    return None if dd is None else bool(dd >= -0.03)


@_cond("market_stressed", "SPY or QQQ is ≥ 8% below its 20-session high")
def _market_stressed(ctx, i):
    dds = [_etf_dd_on(ctx.market.get(m), ctx.stock.d[i]) for m in MARKET_ETFS]
    dds = [x for x in dds if x is not None]
    return None if not dds else bool(min(dds) <= -0.08)


@_cond("market_quiet", "both SPY and QQQ within 3% of their 20-session highs")
def _market_quiet(ctx, i):
    dds = [_etf_dd_on(ctx.market.get(m), ctx.stock.d[i]) for m in MARKET_ETFS]
    dds = [x for x in dds if x is not None]
    return None if not dds else bool(min(dds) >= -0.03)


@_cond("beta_ge:X", "63-session beta to its sector ETF ≥ X, measured 20 sessions earlier (e.g. beta_ge:1.3)")
def _beta_ge(ctx, i, x):
    b = _beta(ctx, i, lag=20)
    return None if b is None else bool(b >= float(x))


@_cond("beta_le:X", "63-session beta to its sector ETF ≤ X, measured 20 sessions earlier")
def _beta_le(ctx, i, x):
    b = _beta(ctx, i, lag=20)
    return None if b is None else bool(b <= float(x))


@_cond("rsi_lt:X", "14-session RSI below X")
def _rsi_lt(ctx, i, x):
    r = _rsi(ctx.stock.c, i)
    return None if r is None else bool(r < float(x))


@_cond("rsi_gt:X", "14-session RSI above X")
def _rsi_gt(ctx, i, x):
    r = _rsi(ctx.stock.c, i)
    return None if r is None else bool(r > float(x))


@_cond("event_within:N:KIND", "a MARKET event (fomc, cpi, jobs, election, any) in the next N sessions — only dates the calendar holds, which today start in 2026")
def _event_within_cond(ctx, i, n, kind="any"):
    return _event_within(ctx, i, int(n), kind)


@_cond("sector:NAME", "GICS sector membership by ETF ticker or name (sector:XLI, sector:Industrials)")
def _sector_is(ctx, i, name):
    want = SECTOR_ETF.get(name, name).upper()
    return None if ctx.sector_name is None else bool(ctx.sector_name == want)


def describe() -> list[dict]:
    return [{"condition": k, "meaning": v[1]} for k, v in CONDITIONS.items()]


_ARG = re.compile(r"^([a-z_]+)(?::(.*))?$")


def parse_condition(text: str):
    """'own_dd_ge:10' -> (fn, ['10']); unknown names raise with the list."""
    m = _ARG.match(text.strip())
    if not m:
        raise ValueError(f"bad condition {text!r}")
    name, raw = m.group(1), m.group(2)
    args = raw.split(":") if raw else []
    for key, (fn, _) in CONDITIONS.items():
        if key.split(":")[0] == name:
            return fn, args, key
    raise ValueError(f"unknown condition {name!r}; known: {sorted(k.split(':')[0] for k in CONDITIONS)}")


# ── the question ─────────────────────────────────────────────────────────────

@dataclass
class Question:
    conditions: list[str]
    horizons: tuple[int, ...] = HORIZONS_DEFAULT
    universe: str = "harvest"          # harvest | all | comma-separated symbols
    null_draws: int = NULL_DRAWS
    null_gap: int = NULL_GAP
    seed: int = 20261111

    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", "+".join(self.conditions).lower()).strip("-")[:80]


def _summ(rs, nulls):
    n = len(rs)
    if n == 0:
        return {"n": 0}
    edges = [r - z for r, z in zip(rs, nulls)]
    return {
        "n": n,
        "mean_pct": st.mean(rs) * 100,
        "median_pct": st.median(rs) * 100,
        "null_mean_pct": st.mean(nulls) * 100,
        "edge_pt": st.mean(edges) * 100,
        "edge_win_pct": 100 * sum(e > 0 for e in edges) / n,
        "win_pct": 100 * sum(r > 0 for r in rs) / n,
        "p5_pct": float(np.percentile(rs, 5)) * 100,
        "worst_pct": min(rs) * 100,
    }


def run(q: Question, *, load=None, universe_symbols=None, sector_map=None,
        events=None, today: date | None = None) -> dict:
    """Answer a Question. All inputs injectable for tests; defaults read the
    settled bar store, tradeable.json and the committed market calendar."""
    from .daily_bars import load_daily
    load = load or load_daily
    rnd = random.Random(q.seed)
    sector_map = sector_map if sector_map is not None else sector_of_universe()
    if events is None:
        events = _calendar_events()

    if q.universe in ("harvest", "all"):
        if universe_symbols is None:
            from .universe import universe_symbols as _us
            universe_symbols = _us
        syms = universe_symbols(harvested_only=(q.universe == "harvest"))
    else:
        syms = [s.strip().upper() for s in q.universe.split(",") if s.strip()]
    syms = [s for s in syms if s not in SECTOR_ETF.values() and s not in MARKET_ETFS]

    parsed = [parse_condition(c) for c in q.conditions]
    etfs: dict[str, Series | None] = {}

    def etf(t):
        if t not in etfs:
            etfs[t] = _to_series(t, load(t))
        return etfs[t]

    market = {m: etf(m) for m in MARKET_ETFS}
    market = {k: v for k, v in market.items() if v is not None}
    H = max(q.horizons)
    trades, ungraded = [], {}

    def count(k):
        ungraded[k] = ungraded.get(k, 0) + 1

    for sym in syms:
        s = _to_series(sym, load(sym))
        if s is None:
            count("no_or_short_bars"); continue
        ctx = Context(stock=s, sector_etf=etf(sector_map[sym]) if sym in sector_map else None,
                      market=market, events=events, sector_name=sector_map.get(sym))
        c = s.c
        matches, i = [], SMA_N
        while i < len(c) - H:
            vals = [fn(ctx, i, *args) for fn, args, _ in parsed]
            if any(v is None for v in vals):
                count("condition_not_evaluable"); i += 1; continue
            if all(vals):
                matches.append(i)
                i += H          # one entry per run of the condition
            else:
                i += 1
        if not matches:
            continue
        excl = set()
        for i in matches:
            excl.update(range(i - q.null_gap, i + q.null_gap + 1))
        pool = [j for j in range(SMA_N, len(c) - H) if j not in excl]
        for i in matches:
            if len(pool) < q.null_draws:
                count("no_null_available"); continue
            draws = rnd.sample(pool, q.null_draws)
            trades.append({
                "symbol": sym, "date": s.d[i], "sector": ctx.sector_name,
                "ret": {h: net(c[i + h] / c[i] - 1) for h in q.horizons},
                "null": {h: st.mean(net(c[j + h] / c[j] - 1) for j in draws) for h in q.horizons},
            })

    by_h = {str(h): _summ([t["ret"][h] for t in trades], [t["null"][h] for t in trades]) for h in q.horizons}
    h40 = 40 if 40 in q.horizons else q.horizons[-1]
    syms_sorted = sorted({t["symbol"] for t in trades})
    half = set(syms_sorted[: len(syms_sorted) // 2])
    cells = {}
    for tn, tf in (("pre2016", lambda t: t["date"] < SPLIT_DATE), ("post2016", lambda t: t["date"] >= SPLIT_DATE)):
        for sn, sf in (("symA", lambda t: t["symbol"] in half), ("symB", lambda t: t["symbol"] not in half)):
            sub = [t for t in trades if tf(t) and sf(t)]
            cells[f"{tn}x{sn}"] = {"n": len(sub),
                                   "edge_pt": (st.mean(t["ret"][h40] - t["null"][h40] for t in sub) * 100) if sub else None}
    by_sector = {}
    for sec in sorted({t["sector"] for t in trades if t["sector"]}):
        sub = [t for t in trades if t["sector"] == sec]
        by_sector[sec] = {"n": len(sub),
                          "edge_pt": st.mean(t["ret"][h40] - t["null"][h40] for t in sub) * 100}
    robust = (len(trades) >= 100 and all(v["n"] >= 10 and (v["edge_pt"] or -99) > 0 for v in cells.values()))
    return {
        "as_of": (today or date.today()).isoformat(),
        "question": {"conditions": q.conditions, "horizons": list(q.horizons), "universe": q.universe,
                     "symbols_asked": len(syms)},
        "null": f"same stock, {q.null_draws} random sessions ≥{q.null_gap} sessions from any match, averaged; "
                f"{COST * 2 * 1e4:.0f} bps round trip on every arm",
        "trades": len(trades), "symbols_with_trades": len(syms_sorted), "ungraded": ungraded,
        "by_horizon": by_h,
        "split_edge_at_h": {"h": h40, "cells": cells, "all_four_positive_and_n_ge_100": robust},
        "by_sector_edge_at_h": by_sector,
        "reading": _reading(by_h, cells, robust, len(trades)),
        "rule": ("a positive edge here is a QUESTION for a pre-registered study, not a trade; "
                 "a negative one is a lesson for free"),
    }


def _reading(by_h, cells, robust, n) -> str:
    if n == 0:
        return "no symbol-day matched every condition — nothing to read"
    e20 = by_h.get("20", {}).get("edge_pt")
    e40 = by_h.get("40", {}).get("edge_pt")
    parts = []
    for h, e in (("20", e20), ("40", e40)):
        if e is not None:
            parts.append(f"{e:+.2f}pt vs null at {h}")
    verdict = ("edge is positive in all four time×symbol cells with n≥10 each — worth a pre-registered study"
               if robust else
               "edge does NOT hold across both halves of time and symbols — treat as noise unless a study says otherwise")
    return f"{n} trades; " + ", ".join(parts) + f". {verdict}."


def _calendar_events() -> dict[str, list[str]]:
    """MARKET-wide dated events from the committed seed (FOMC/CPI/jobs/election)."""
    p = Path(__file__).resolve().parents[1] / "events" / "market_calendar.json"
    try:
        d = json.loads(p.read_text())
    except Exception:  # noqa: BLE001 — no calendar → no event condition can evaluate
        return {}
    out: dict[str, list[str]] = {}
    for e in d.get("events", []):
        out.setdefault(str(e.get("kind")), []).append(str(e.get("on"))[:10])
    return {k: sorted(v) for k, v in out.items()}


def archive(result: dict, q: Question) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    p = ARCHIVE_DIR / f"{result['as_of']}-{q.slug()}.json"
    p.write_text(json.dumps(result, indent=2, default=float))
    return p


def recent(limit: int = 30) -> list[dict]:
    if not ARCHIVE_DIR.exists():
        return []
    out = []
    for p in sorted(ARCHIVE_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text())
            out.append({"file": p.name, "as_of": d.get("as_of"), "question": d.get("question"),
                        "trades": d.get("trades"), "reading": d.get("reading")})
        except Exception:  # noqa: BLE001
            continue
    return out


def format_result(r: dict) -> str:
    q = r["question"]
    lines = [f"WHAT IF  {' AND '.join(q['conditions'])}   [{q['universe']}, {q['symbols_asked']} symbols asked]",
             f"trades {r['trades']} on {r['symbols_with_trades']} symbols · ungraded {r['ungraded']}",
             f"null: {r['null']}", "",
             f"{'h':>4} {'mean%':>8} {'null%':>8} {'edge pt':>8} {'edge>0%':>8} {'win%':>6} {'p5%':>7} {'worst%':>8}"]
    f = lambda x: "n/a" if x is None else f"{x:+.2f}"
    for h, v in r["by_horizon"].items():
        if v.get("n"):
            lines.append(f"{h:>4} {f(v['mean_pct']):>8} {f(v['null_mean_pct']):>8} {f(v['edge_pt']):>8} "
                         f"{f(v['edge_win_pct']):>8} {f(v['win_pct']):>6} {f(v['p5_pct']):>7} {f(v['worst_pct']):>8}")
    s = r["split_edge_at_h"]
    lines.append("")
    lines.append(f"split edge@{s['h']}: " + "  ".join(f"{k} n={v['n']} {f(v['edge_pt'])}" for k, v in s["cells"].items()))
    if r["by_sector_edge_at_h"]:
        lines.append("by sector: " + "  ".join(f"{k} n={v['n']} {f(v['edge_pt'])}" for k, v in r["by_sector_edge_at_h"].items()))
    lines.append("")
    lines.append("READING: " + r["reading"])
    lines.append("RULE: " + r["rule"])
    return "\n".join(lines)


__all__ = ["Question", "run", "archive", "recent", "describe", "parse_condition", "format_result", "CONDITIONS"]
