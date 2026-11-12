"""Monthly momentum rotation, the core engine: backtest (spec 12 Nov 2026).

The rules are pre-registered in MOMENTUM_ROTATION_GATES_V1.md. This module is
the ONE implementation: the CLI and the MCP tool both call `backtest()`, and a
variant is a `Params` override, never a fork of the code.

Simulates the ACCOUNT, not just the engine: net liquidation is the engine's
holdings plus cash, the engine is allocated at most `base_allocation` of it,
and the -15% / -25% alerts act on the account's drawdown as the spec says.
Cash earns nothing (conservative).

Prices: `close * adj_factor` from the golden store (dividend-adjusted) for
returns, valuation, the ranking and the trend filters; raw close for the $10
price floor and traded value. Signals on the close; fills at the next trading
day's open; costs on traded value.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

UNIVERSE_FILE = Path(__file__).resolve().parent.parent / "universe" / "momentum_core.json"


@dataclass(frozen=True)
class Params:
    # decisions 1-7 (owner, 12 Nov 2026)
    lookbacks: tuple[int, ...] = (63, 126, 252)
    skip_recent: int = 0                 # 21 = "12-minus-1" style variants
    top_n: int = 10
    market_filter: str = "spy200"        # spy200 | spy10m | breadth | none
    stock_trend_filter: bool = True      # close above own 200-day
    sector_cap: int | None = 4           # names per sector; None = off
    vol_target: float | None = 0.20      # None = off
    vol_lookback: int = 63
    base_allocation: float = 0.50
    rebalance_day: int = 15              # day of month; 0 = month-end
    rebalance_offset: int = 0            # +-N trading days around that date
    trailing_stop: float | None = 0.20   # None = off
    alert_halve: float | None = 0.15     # account drawdown that halves the engine
    alert_cash: float | None = 0.25      # account drawdown that goes to cash
    # proposed implementation details
    min_history: int = 252
    min_price: float = 10.0
    min_traded_value: float = 20e6
    liquidity_window: int = 60
    cost: float = 0.001
    rebalance_band: float = 0.02         # trade a kept holding only beyond +-2pp
    start: str = "2008-01-01"
    end: str | None = None
    symbols: tuple[str, ...] | None = None  # None = universe/momentum_core.json


# ── data ────────────────────────────────────────────────────────────────


def load_universe() -> tuple[list[str], dict[str, str]]:
    doc = json.loads(UNIVERSE_FILE.read_text())
    syms = [r["symbol"] for r in doc["symbols"]]
    return syms, {r["symbol"]: r["sector"] for r in doc["symbols"]}


@dataclass
class Panels:
    dates: pd.DatetimeIndex
    adj_close: pd.DataFrame
    adj_open: pd.DataFrame
    raw_close: pd.DataFrame
    volume: pd.DataFrame
    spy: pd.Series
    sectors: dict[str, str]
    missing: list[str] = field(default_factory=list)


def load_panels(symbols: list[str], sectors: dict[str, str]) -> Panels:
    """Daily panels aligned on SPY's sessions, from the golden store."""
    from .daily_bars import load_daily

    def _day_index(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.index = pd.to_datetime([str(t)[:10] for t in df.index])
        return df[~df.index.duplicated(keep="last")].sort_index()

    spy = load_daily("SPY", fetched_by="momentum_rotation")
    if spy is None:
        raise RuntimeError("SPY has no bars in the store; the market filter cannot run")
    spy = _day_index(spy)
    dates = spy.index
    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in ("ac", "ao", "rc", "v")}
    missing = []
    for s in symbols:
        df = load_daily(s, fetched_by="momentum_rotation")
        if df is None or df.empty:
            missing.append(s)
            continue
        df = _day_index(df)
        f = df["adj_factor"].fillna(1.0).astype(float)
        cols["ac"][s] = (df["close"].astype(float) * f)
        cols["ao"][s] = (df["open"].astype(float) * f)
        cols["rc"][s] = df["close"].astype(float)
        cols["v"][s] = df["volume"].astype(float)

    def _panel(d: dict[str, pd.Series]) -> pd.DataFrame:
        return pd.DataFrame(d).reindex(dates)

    spy_adj = spy["close"].astype(float) * spy["adj_factor"].fillna(1.0).astype(float)
    return Panels(dates=dates, adj_close=_panel(cols["ac"]), adj_open=_panel(cols["ao"]),
                  raw_close=_panel(cols["rc"]), volume=_panel(cols["v"]),
                  spy=spy_adj, sectors=sectors, missing=missing)


# ── signals ─────────────────────────────────────────────────────────────


def rebalance_dates(dates: pd.DatetimeIndex, p: Params) -> list[pd.Timestamp]:
    """The signal date each month: the last session on or before the
    rebalance day (or the month's last session), shifted by the offset."""
    out = []
    s = pd.Series(dates, index=dates)
    for _, month in s.groupby([dates.year, dates.month]):
        if p.rebalance_day == 0:
            d = month.iloc[-1]
        else:
            on_or_before = month[month.dt.day <= p.rebalance_day]
            if on_or_before.empty:
                continue
            d = on_or_before.iloc[-1]
        i = dates.get_loc(d) + p.rebalance_offset
        if 0 <= i < len(dates):
            out.append(dates[i])
    return sorted(set(out))


def _score(adj: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = adj.shift(p.skip_recent) if p.skip_recent else adj
    rets = [base / adj.shift(n + p.skip_recent) - 1 for n in p.lookbacks]
    score = sum(rets) / len(rets)
    return score, rets[-1]


def _market_ok(spy: pd.Series, adj: pd.DataFrame, p: Params) -> pd.Series:
    if p.market_filter == "none":
        return pd.Series(True, index=spy.index)
    if p.market_filter == "spy200":
        return spy >= spy.rolling(200).mean()
    if p.market_filter == "spy10m":
        return spy >= spy.rolling(210).mean()
    if p.market_filter == "breadth":
        above = (adj >= adj.rolling(200).mean()).sum(axis=1)
        have = adj.rolling(200).mean().notna().sum(axis=1)
        return (above / have.replace(0, np.nan)) >= 0.5
    raise ValueError(f"unknown market_filter {p.market_filter!r}")


# ── simulation ──────────────────────────────────────────────────────────


def backtest(p: Params = Params(), panels: Panels | None = None) -> dict:
    if panels is None:
        syms, sectors = load_universe()
        if p.symbols:
            syms = list(p.symbols)
        panels = load_panels(syms, sectors)
    P = panels
    adj, opn = P.adj_close, P.adj_open
    # carry a stale price forward a few sessions so a one-day hole does not
    # read as a crash; anything longer stays a hole (not tradeable).
    adj_f = adj.ffill(limit=5)
    opn_f = opn.fillna(adj_f)
    score, ret_long = _score(adj_f, p)
    sma200 = adj_f.rolling(200).mean()
    history = adj.notna().cumsum()
    traded = (P.raw_close * P.volume).rolling(p.liquidity_window, min_periods=40).median()
    market_ok = _market_ok(P.spy.reindex(P.dates).ffill(), adj_f, p)
    daily_ret = adj_f.pct_change(fill_method=None)

    dates = P.dates
    start = pd.Timestamp(p.start)
    end = pd.Timestamp(p.end) if p.end else dates[-1]
    run_dates = dates[(dates >= start) & (dates <= end)]
    rebal = set(rebalance_dates(run_dates, p))

    cash = 1.0
    shares: dict[str, float] = {}
    high: dict[str, float] = {}
    entry_day: dict[str, pd.Timestamp] = {}
    peak = 1.0
    multiplier = 1.0
    pending: dict[str, float] | None = None      # target weights for next open
    pending_sells: set[str] = set()
    nav, invested, sector_max = [], [], []
    trades = turnover = stop_exits = 0
    holds: list[int] = []
    rebalance_log = []

    def px_of(row: pd.Series, s: str) -> float:
        v = row.get(s, np.nan)
        return float(v) if v is not None and not math.isnan(v) and v > 0 else float("nan")

    def value(row: pd.Series) -> float:
        tot = cash
        for s, q in shares.items():
            v = px_of(row, s)
            if not math.isnan(v):          # a holding with no price at all is carried at 0
                tot += q * v
        return tot

    for t in run_dates:
        o = opn_f.loc[t]
        # 1. orders from yesterday's close, filled at today's open; sells first
        if pending is not None or pending_sells:
            nlv = value(o)
            if pending is None:                     # stop exits only: keep the rest
                targets = {s: shares[s] * px_of(o, s) / nlv for s in shares
                           if not math.isnan(px_of(o, s))}
            else:
                targets = dict(pending)
            for s in pending_sells:
                targets[s] = 0.0
            names = list(dict.fromkeys(list(shares) + list(targets)))
            for phase in ("sell", "buy"):
                for s in names:
                    px = px_of(o, s)
                    if math.isnan(px):
                        continue
                    cur = shares.get(s, 0.0) * px
                    diff = targets.get(s, 0.0) * nlv - cur
                    kept = s in shares and targets.get(s, 0.0) > 0
                    if pending is not None and kept and abs(diff) / nlv < p.rebalance_band:
                        continue
                    if phase == "sell" and diff < 0:
                        q = min(shares[s], -diff / px)
                        cash += q * px * (1 - p.cost)
                        shares[s] -= q
                        turnover += q * px / nlv
                        trades += 1
                        if shares[s] <= 1e-12:
                            shares.pop(s)
                            high.pop(s, None)
                            if s in entry_day:
                                holds.append((t - entry_day.pop(s)).days)
                    elif phase == "buy" and diff > 0:
                        spend = min(diff, cash / (1 + p.cost))
                        if spend <= 1e-12:
                            continue
                        cash -= spend * (1 + p.cost)
                        if s not in shares:
                            entry_day[s] = t
                            high[s] = px
                        shares[s] = shares.get(s, 0.0) + spend / px
                        turnover += spend / nlv
                        trades += 1
            pending, pending_sells = None, set()

        # 2. the close: value, stops, alerts
        c = adj_f.loc[t]
        nlv = value(c)
        nav.append(nlv)
        inv = sum(q * px_of(c, s) for s, q in shares.items() if not math.isnan(px_of(c, s)))
        invested.append(inv / nlv if nlv else 0.0)
        secs: dict[str, float] = {}
        for s, q in shares.items():
            v = q * (px_of(c, s) if not math.isnan(px_of(c, s)) else 0.0)
            secs[P.sectors.get(s, "?")] = secs.get(P.sectors.get(s, "?"), 0.0) + v
        sector_max.append(max(secs.values()) / nlv if secs and nlv else 0.0)
        peak = max(peak, nlv)
        dd = nlv / peak - 1

        if p.trailing_stop:
            for s in list(shares):
                px = px_of(c, s)
                if math.isnan(px):
                    continue
                high[s] = max(high.get(s, px), px)
                if px <= high[s] * (1 - p.trailing_stop) and s not in pending_sells:
                    pending_sells.add(s)
                    stop_exits += 1

        if p.alert_cash and dd <= -p.alert_cash and multiplier > 0:
            multiplier = 0.0
            pending = {}
        elif p.alert_halve and dd <= -p.alert_halve and multiplier == 1.0:
            multiplier = 0.5
            pending = {s: 0.5 * q * px_of(c, s) / nlv for s, q in shares.items()
                       if not math.isnan(px_of(c, s))}

        # 3. the monthly decision
        if t in rebal:
            if dd > -(p.alert_halve or 1.0):
                multiplier = 1.0
            picks: list[str] = []
            reason = "market filter: SPY below its trend -> cash"
            if bool(market_ok.get(t, False)) and multiplier > 0:
                elig = (history.loc[t] >= p.min_history) & score.loc[t].notna() \
                    & (P.raw_close.loc[t].ffill() > p.min_price) \
                    & (traded.loc[t] > p.min_traded_value)
                if p.stock_trend_filter:
                    elig &= adj_f.loc[t] > sma200.loc[t]
                cand = pd.DataFrame({"score": score.loc[t], "long": ret_long.loc[t]})[elig]
                cand = cand.sort_values(["score", "long"], ascending=False)
                per_sector: dict[str, int] = {}
                for s in cand.index:
                    sec = P.sectors.get(s, "?")
                    if p.sector_cap and per_sector.get(sec, 0) >= p.sector_cap:
                        continue
                    picks.append(s)
                    per_sector[sec] = per_sector.get(sec, 0) + 1
                    if len(picks) == p.top_n:
                        break
                reason = f"{len(picks)} picks from {int(elig.sum())} eligible"
            alloc = p.base_allocation * multiplier
            vol = None
            if picks and p.vol_target:
                window = daily_ret.loc[:t, picks].tail(p.vol_lookback)
                vol = float(window.mean(axis=1).std() * math.sqrt(252))
                if vol > 0:
                    alloc *= min(1.0, p.vol_target / vol)
            w = alloc / p.top_n
            pending = {s: w for s in picks}
            rebalance_log.append({"date": str(t.date()), "picks": picks, "allocation": round(alloc, 4),
                                  "portfolio_vol": None if vol is None else round(vol, 4),
                                  "reason": reason})

    navs = pd.Series(nav, index=run_dates)
    return {"params": _jsonable(asdict(p)), "metrics": metrics(navs, run_dates, P),
            "activity": {"trades": trades, "turnover_per_year": round(turnover / max(1e-9, len(navs) / 252), 2),
                         "avg_holding_days": round(float(np.mean(holds)), 1) if holds else None,
                         "trailing_stop_exits": stop_exits,
                         "time_in_cash_pct": round(float(np.mean(np.array(invested) < 0.01)) * 100, 1),
                         "avg_invested_pct": round(float(np.mean(invested)) * 100, 1),
                         "max_sector_weight_pct": round(float(np.max(sector_max)) * 100, 1)},
            "data": {"symbols_requested": int(adj.shape[1] + len(P.missing)), "missing": P.missing,
                     "first": str(run_dates[0].date()), "last": str(run_dates[-1].date())},
            "rebalances": rebalance_log, "nav": navs}


def _stats(navs: pd.Series) -> dict:
    r = navs.pct_change().dropna()
    years = len(navs) / 252
    cagr = (navs.iloc[-1] / navs.iloc[0]) ** (1 / years) - 1 if years > 0 else float("nan")
    dd = (navs / navs.cummax() - 1).min()
    sharpe = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else float("nan")
    roll = (navs / navs.shift(252) - 1).min()
    yearly = navs.groupby(navs.index.year).last().pct_change()
    yearly.iloc[0] = navs.groupby(navs.index.year).last().iloc[0] / navs.iloc[0] - 1
    return {"cagr_pct": round(cagr * 100, 2), "max_drawdown_pct": round(dd * 100, 2),
            "sharpe": round(float(sharpe), 2), "worst_rolling_12m_pct": round(float(roll) * 100, 2),
            "yearly_pct": {int(k): round(float(v) * 100, 1) for k, v in yearly.items()}}


def metrics(navs: pd.Series, run_dates: pd.DatetimeIndex, P: Panels) -> dict:
    spy = P.spy.reindex(run_dates).ffill()
    spy_half = 0.5 + 0.5 * spy / spy.iloc[0]          # SPY at the same 50% budget
    ew = P.adj_close.ffill(limit=5).reindex(run_dates).pct_change(fill_method=None).mean(axis=1).fillna(0)
    ew_nav = (1 + ew).cumprod()
    return {"account": _stats(navs), "spy": _stats(spy / spy.iloc[0]),
            "spy_at_50pct": _stats(spy_half), "equal_weight_universe": _stats(ew_nav)}


def _jsonable(d: dict) -> dict:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}


_PANEL_CACHE: dict[tuple, tuple[float, Panels]] = {}


def cached_panels(symbols: tuple[str, ...] | None, ttl_s: float = 3600) -> Panels:
    """One load per process per hour: a robustness sweep runs dozens of
    variants on the same data, and loading is most of the cost."""
    import time
    syms, sectors = load_universe()
    if symbols:
        syms = list(symbols)
    key = tuple(syms)
    hit = _PANEL_CACHE.get(key)
    if hit and time.time() - hit[0] < ttl_s:
        return hit[1]
    panels = load_panels(syms, sectors)
    _PANEL_CACHE[key] = (time.time(), panels)
    return panels


def run(overrides: dict | None = None) -> dict:
    """Backtest the decided design with optional overrides; summarised."""
    p = with_overrides(**(overrides or {}))
    return summarise(backtest(p, cached_panels(p.symbols)))


def compare(variants: list[dict]) -> dict:
    """Several variants on the same data. EVERY variant is reported, with the
    count, so a sweep cannot quietly keep only its best row."""
    rows = []
    for v in variants:
        r = run(v)
        a, spy = r["metrics"]["account"], r["metrics"]["spy_at_50pct"]
        rows.append({"overrides": v, "cagr_pct": a["cagr_pct"], "max_drawdown_pct": a["max_drawdown_pct"],
                     "sharpe": a["sharpe"], "worst_rolling_12m_pct": a["worst_rolling_12m_pct"],
                     "beats_spy_at_same_budget": a["cagr_pct"] > spy["cagr_pct"],
                     "trades": r["activity"]["trades"],
                     "time_in_cash_pct": r["activity"]["time_in_cash_pct"]})
    return {"variants_run": len(rows), "rows": rows,
            "spy_at_50pct_cagr_pct": r["metrics"]["spy_at_50pct"]["cagr_pct"] if rows else None}


#: The robustness suite, FIXED before any run (MOMENTUM_ROTATION_GATES_V1.md
#: step 2). Each entry is one change from the decided design.
ROBUSTNESS: list[dict] = (
    [{"lookbacks": [n]} for n in (63, 126, 189, 252)]
    + [{"lookbacks": [231], "skip_recent": 21}]                  # 12-minus-1
    + [{"top_n": n} for n in (8, 12, 15)]
    + [{"rebalance_day": 0}, {"rebalance_offset": -5}, {"rebalance_offset": 5}]
    + [{"market_filter": m} for m in ("spy10m", "breadth", "none")]
    + [{"vol_target": v} for v in (None, 0.15, 0.25)]
    + [{"trailing_stop": v} for v in (None, 0.15, 0.25)]
    + [{"sector_cap": None}]
)
NOT_YET_RUN = ["4 weekly tranches (rebalance split across four weeks)"]


def summarise(result: dict) -> dict:
    """The result without the daily series, for logs, MCP and reports."""
    out = {k: v for k, v in result.items() if k not in ("nav", "rebalances")}
    out["last_rebalances"] = result["rebalances"][-3:]
    return out


def with_overrides(**kw) -> Params:
    """A null override MEANS off (trailing_stop=None disables the stop), so
    nulls are kept. An unknown name raises rather than being ignored: a typo
    that silently ran the default would report the default as the variant."""
    from dataclasses import fields
    known = {f.name for f in fields(Params)}
    unknown = set(kw) - known
    if unknown:
        raise ValueError(f"unknown parameter(s) {sorted(unknown)}")
    clean = {k: (tuple(v) if isinstance(v, list) else v) for k, v in kw.items()}
    return replace(Params(), **clean)
