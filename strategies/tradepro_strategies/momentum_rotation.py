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

import math
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd



@dataclass(frozen=True)
class Params:
    # decisions 1-7 (owner, 12 Nov 2026)
    lookbacks: tuple[int, ...] = (63, 126, 252)
    skip_recent: int = 0                 # 21 = "12-minus-1" style variants
    top_n: int = 10
    market_filter: str = "spy200"        # spy200 | spy10m | breadth | none
    stock_trend_filter: bool = True      # close above own 200-day
    sector_cap: int | None = 4           # names per sector; None = off
    vol_target: float | None = 0.25      # None = off; 25% by owner 12 Nov (was 20%, changed AFTER results)
    vol_lookback: int = 63
    base_allocation: float = 0.50
    rebalance_day: int = 15              # day of month; 0 = month-end
    rebalance_offset: int = 0            # +-N trading days around that date
    trailing_stop: float | None = 0.20   # None = off
    alert_halve: float | None = 0.15     # account drawdown that halves the engine
    alert_cash: float | None = 0.25      # account drawdown that goes to cash
    # review variants (14 Nov 2026), OFF in the decided design
    entry_above_sma50: bool = False      # veto a pick closing below its 50-day
    score_mode: str = "blend"            # blend | vol_adjusted (score / 126-day vol)
    score_cap_x_median: float | None = None  # winsorize: cap a score at N x the median
    take_profit: float | None = None     # sell on a close this far above entry (0.15 = +15%)
    # proposed implementation details
    min_history: int = 252
    min_price: float = 10.0
    min_traded_value: float = 20e6
    liquidity_window: int = 60
    cost: float = 0.001
    rebalance_band: float = 0.02         # trade a kept holding only beyond +-2pp
    start: str = "2008-01-01"
    end: str | None = None
    symbols: tuple[str, ...] | None = None  # None = harvest symbols with a sector label


# ── data ────────────────────────────────────────────────────────────────


def load_universe() -> tuple[list[str], dict[str, str]]:
    """Rule 1 (build pack, 13 Nov 2026): TradePro's daily-harvest symbols —
    the harvest flag plus the owner's on/off toggles from the universes page —
    that carry a sector label. ETFs and funds have no sector label, so they
    are excluded here; SPY and QQQ are read separately as regime inputs. The
    remaining eligibility filters (252 days of history, $10, $20M traded
    value) are applied at each rebalance with data up to that day only."""
    from .universe import apply_harvest_overrides, harvest_overrides
    from .universe import load_universe as _load
    rows = apply_harvest_overrides(_load()["symbols"], harvest_overrides())
    rows = [r for r in rows if r.get("harvest", True) and r.get("sector")
            and r["symbol"] not in ("SPY", "QQQ")]
    return [r["symbol"] for r in rows], {r["symbol"]: r["sector"] for r in rows}


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


@dataclass
class Derived:
    """Everything the monthly decision reads, computed once per run."""
    adj_f: pd.DataFrame
    opn_f: pd.DataFrame
    score: pd.DataFrame
    ret_long: pd.DataFrame
    sma200: pd.DataFrame
    history: pd.DataFrame
    traded: pd.DataFrame
    market_ok: pd.Series
    daily_ret: pd.DataFrame
    sma50: pd.DataFrame
    vol126: pd.DataFrame


def derive(P: Panels, p: Params) -> Derived:
    # carry a stale price forward a few sessions so a one-day hole does not
    # read as a crash; anything longer stays a hole (not tradeable).
    adj_f = P.adj_close.ffill(limit=5)
    score, ret_long = _score(adj_f, p)
    return Derived(
        adj_f=adj_f, opn_f=P.adj_open.fillna(adj_f), score=score, ret_long=ret_long,
        sma200=adj_f.rolling(200).mean(), history=P.adj_close.notna().cumsum(),
        traded=(P.raw_close * P.volume).rolling(p.liquidity_window, min_periods=40).median(),
        market_ok=_market_ok(P.spy.reindex(P.dates).ffill(), adj_f, p),
        daily_ret=adj_f.pct_change(fill_method=None),
        sma50=adj_f.rolling(50).mean(),
        vol126=adj_f.pct_change(fill_method=None).rolling(126).std() * math.sqrt(252))


def select(t: pd.Timestamp, D: Derived, P: Panels, p: Params, multiplier: float = 1.0) -> dict:
    """THE monthly decision for signal date t: picks, allocation, and why.
    The backtest and the paper sleeve both call this, so they cannot drift."""
    picks: list[str] = []
    scores: dict[str, float] = {}
    eligible = 0
    reason = "market filter: SPY below its trend -> cash"
    market = bool(D.market_ok.get(t, False))
    if market and multiplier > 0:
        elig = (D.history.loc[t] >= p.min_history) & D.score.loc[t].notna() \
            & (P.raw_close.loc[t].ffill() > p.min_price) \
            & (D.traded.loc[t] > p.min_traded_value)
        if p.stock_trend_filter:
            elig &= D.adj_f.loc[t] > D.sma200.loc[t]
        if p.entry_above_sma50:
            elig &= D.adj_f.loc[t] > D.sma50.loc[t]
        sc = D.score.loc[t]
        if p.score_mode == "vol_adjusted":
            sc = sc / D.vol126.loc[t]
        elif p.score_mode != "blend":
            raise ValueError(f"unknown score_mode {p.score_mode!r}")
        if p.score_cap_x_median:
            med = sc[elig].median()
            if med and med > 0:
                sc = sc.clip(upper=p.score_cap_x_median * med)
        cand = pd.DataFrame({"score": sc, "long": D.ret_long.loc[t]})[elig]
        cand = cand.sort_values(["score", "long"], ascending=False)
        per_sector: dict[str, int] = {}
        for s in cand.index:
            sec = P.sectors.get(s, "?")
            if p.sector_cap and per_sector.get(sec, 0) >= p.sector_cap:
                continue
            picks.append(s)
            scores[s] = round(float(cand.at[s, "score"]), 4)
            per_sector[sec] = per_sector.get(sec, 0) + 1
            if len(picks) == p.top_n:
                break
        eligible = int(elig.sum())
        reason = f"{len(picks)} picks from {eligible} eligible"
    elif market:
        reason = "allocation cut to zero by the account drawdown alert"
    alloc = p.base_allocation * multiplier
    vol = None
    if picks and p.vol_target:
        window = D.daily_ret.loc[:t, picks].tail(p.vol_lookback)
        vol = float(window.mean(axis=1).std() * math.sqrt(252))
        if vol > 0:
            alloc *= min(1.0, p.vol_target / vol)
    return {"date": str(t.date()), "picks": picks, "scores": scores,
            "sectors": {s: P.sectors.get(s, "?") for s in picks},
            "allocation": round(alloc, 4), "weight_each": round(alloc / p.top_n, 4),
            "allocation_exact": alloc,
            "portfolio_vol": None if vol is None else round(vol, 4),
            "market_ok": market, "eligible": eligible, "reason": reason}


def backtest(p: Params = Params(), panels: Panels | None = None) -> dict:
    if panels is None:
        syms, sectors = load_universe()
        if p.symbols:
            syms = list(p.symbols)
        panels = load_panels(syms, sectors)
    P = panels
    adj = P.adj_close
    D = derive(P, p)
    adj_f, opn_f = D.adj_f, D.opn_f

    dates = P.dates
    start = pd.Timestamp(p.start)
    end = pd.Timestamp(p.end) if p.end else dates[-1]
    run_dates = dates[(dates >= start) & (dates <= end)]
    rebal = set(rebalance_dates(run_dates, p))

    cash = 1.0
    shares: dict[str, float] = {}
    high: dict[str, float] = {}
    entry_day: dict[str, pd.Timestamp] = {}
    entry_px: dict[str, float] = {}
    tp_exits = 0
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
                            entry_px.pop(s, None)
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
                            entry_px[s] = px
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

        if p.take_profit:
            for s in list(shares):
                px = px_of(c, s)
                if not math.isnan(px) and s in entry_px and px >= entry_px[s] * (1 + p.take_profit) \
                        and s not in pending_sells:
                    pending_sells.add(s)
                    tp_exits += 1

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
            dec = select(t, D, P, p, multiplier)
            w = dec["allocation_exact"] / p.top_n
            pending = {s: w for s in dec["picks"]}
            rebalance_log.append({k: dec[k] for k in ("date", "picks", "allocation",
                                                      "portfolio_vol", "reason")})

    navs = pd.Series(nav, index=run_dates)
    return {"params": _jsonable(asdict(p)), "metrics": metrics(navs, run_dates, P),
            "activity": {"trades": trades, "turnover_per_year": round(turnover / max(1e-9, len(navs) / 252), 2),
                         "avg_holding_days": round(float(np.mean(holds)), 1) if holds else None,
                         "trailing_stop_exits": stop_exits,
                         "take_profit_exits": tp_exits,
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


def _sessions(start, end) -> list:
    from datetime import datetime, timezone
    from .bar_cache.asset_classes import UsEtfPlugin
    return UsEtfPlugin().expected_session_dates(
        datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        datetime(end.year, end.month, end.day, tzinfo=timezone.utc))


def signal_dates_around(today) -> dict:
    """Which monthly signal is ACTIVE on `today`, from the exchange calendar
    (not from the bars we happen to have): the last session on or before the
    15th whose NEXT session is on or before today."""
    import datetime as _dt
    sess = _sessions(today - _dt.timedelta(days=75), today + _dt.timedelta(days=45))
    sigs = []
    for (y, m) in sorted({(d.year, d.month) for d in sess}):
        month = [d for d in sess if d.year == y and d.month == m and d.day <= 15]
        if month:
            sigs.append(month[-1])
    nxt = {d: sess[i + 1] for i, d in enumerate(sess[:-1])}
    active = [s for s in sigs if s in nxt and nxt[s] <= today]
    if not active:
        raise RuntimeError("no active signal in the calendar window")
    sig = active[-1]
    later = [s for s in sigs if s > sig]
    return {"signal_date": sig, "order_date": nxt[sig],
            "next_signal_date": later[0] if later else None}


def decide(today=None, p: Params = Params()) -> dict:
    """The decision the paper sleeve trades on `today`. FAILS CLOSED: if the
    signal day's bar is not in the store yet, it returns an error and no picks,
    and the sleeve does nothing rather than trading last month's list."""
    import datetime as _dt
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    cal = signal_dates_around(today)
    P = cached_panels(p.symbols, ttl_s=900)
    t = pd.Timestamp(cal["signal_date"])
    base = {k: str(v) if v else None for k, v in cal.items()}
    if t not in P.dates:
        return {**base, "error": f"no bar for the signal day {t.date()} in the store "
                                 f"(newest {P.dates[-1].date()}); not trading on a stale list",
                "picks": []}
    dec = select(t, derive(P, p), P, p)
    dec.pop("allocation_exact", None)
    return {**base, **dec, "params": {"vol_target": p.vol_target, "top_n": p.top_n,
                                      "trailing_stop": p.trailing_stop}}


def ranking_today(p: Params = Params()) -> dict:
    """What the engine WOULD pick if it rebalanced on the latest close in the
    store. Display only (owner, 14 Nov: "I need to get signals everyday based on
    latest data"): holdings still change on the monthly decision date, but the
    list's daily drift is visible."""
    P = cached_panels(p.symbols, ttl_s=900)
    t = P.dates[-1]
    dec = select(t, derive(P, p), P, p)
    dec.pop("allocation_exact", None)
    return dec


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
    + [{"vol_target": v} for v in (None, 0.15, 0.20)]
    + [{"trailing_stop": v} for v in (None, 0.15, 0.25)]
    + [{"sector_cap": None}]
)
NOT_YET_RUN = ["4 weekly tranches (rebalance split across four weeks)"]


def walk_forward(split_year: int = 2018, first_year: int = 2008) -> dict:
    """The pre-registered walk-forward (MOMENTUM_ROTATION_GATES_V1.md)."""
    import datetime as _dt
    panels = cached_panels(None)
    candidates = [{}] + ROBUSTNESS
    last_year = panels.dates[-1].year

    def _run(ov: dict, start: str, end: str) -> dict:
        p = with_overrides(**{**ov, "start": start, "end": end})
        return backtest(p, panels)["metrics"]["account"]

    def _best(end_year: int) -> tuple[dict, list]:
        scored = [(c, _run(c, f"{first_year}-01-01", f"{end_year}-12-31")) for c in candidates]
        return max(scored, key=lambda x: x[1]["sharpe"])[0], scored

    pick, scored = _best(split_year - 1)
    ins = next(m for c, m in scored if c == pick)
    oos = _run(pick, f"{split_year}-01-01", f"{last_year}-12-31")
    base_in = next(m for c, m in scored if c == {})
    base_oos = _run({}, f"{split_year}-01-01", f"{last_year}-12-31")

    yearly, chain = [], 1.0
    for y in range(split_year, last_year + 1):
        choice, _ = _best(y - 1)
        r = _run(choice, f"{y}-01-01", f"{y}-12-31")
        ret = (r["yearly_pct"].get(y, 0.0)) / 100
        chain *= 1 + ret
        yearly.append({"year": y, "chose": choice, "return_pct": round(ret * 100, 2)})
    n_years = (panels.dates[-1] - _dt.datetime(split_year, 1, 1)).days / 365.25
    return {
        "single_split": {"chose": pick, "in_sample": {k: ins[k] for k in ("cagr_pct", "max_drawdown_pct", "sharpe")},
                         "out_of_sample": {k: oos[k] for k in ("cagr_pct", "max_drawdown_pct", "sharpe")},
                         "g4_pass": oos["cagr_pct"] >= 0.5 * ins["cagr_pct"]},
        "decided_design_control": {"in_sample": {k: base_in[k] for k in ("cagr_pct", "max_drawdown_pct", "sharpe")},
                                   "out_of_sample": {k: base_oos[k] for k in ("cagr_pct", "max_drawdown_pct", "sharpe")},
                                   "keeps_half": base_oos["cagr_pct"] >= 0.5 * base_in["cagr_pct"]},
        "rolling": {"years": yearly, "chained_cagr_pct": round((chain ** (1 / n_years) - 1) * 100, 2)},
        "candidates": len(candidates),
    }


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
