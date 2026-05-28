"""Broker profiles — one-call factory for (BarBus, OrderRouter).

The paper engine takes any `BarBus` + any `OrderRouter`. The combinations
that actually make sense in practice are small, so this module names
them as "profiles":

    replay     — ReplayBarBus (or YfinanceIntradayBus) + PaperOrderRouter.
                 Backtests, walk-forward, deterministic unit tests.
    yfinance   — Live(ish) Yahoo bars + PaperOrderRouter. Sim-only.
    t212       — Yahoo bars + T212OrderRouter. T212 has no OHLC feed
                 (memory: T212 has portfolio + instruments + orders,
                 but NO OHLC/quotes), so we pair it with Yahoo for bars
                 and use T212 only for execution.
    ibkr       — IBKRBarBus + IBKRRouter. Bars + orders both via IBKR;
                 supports paper accounts (DU prefix) or live with the
                 two-key safety gate.
    stub_live  — Yahoo bars + StubLiveRouter. Wiring-only safety net
                 until a real IBKR account is provisioned — logs every
                 order, fills nothing.

Each factory returns `(bus, router)`. Construct an Engine with these,
register strategies, call `run()`. Switching brokers is one keyword
argument away.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .bar_bus import BarBus, ReplayBarBus, YfinanceIntradayBus
from .brokers.ibkr import IBKRBarBus, IBKRConnection, IBKRRouter
from .brokers.t212 import T212OrderRouter
from .multi_router import MultiBrokerRouter
from .router import OrderRouter, PaperOrderRouter, StubLiveRouter
from .sources import (
    BarSource,
    CachedSource,
    FallbackSource,
    FinnhubSource,
    MultiSymbolSourceBackedBus,
    SourceBackedBus,
    YfinanceSource,
)
from .strategy import Bar


def build_session(
    broker: str,
    symbols: Iterable[str],
    *,
    session_date: datetime | None = None,
    interval: str = "1m",
    bars: Iterable[Bar] | None = None,
    pace_seconds: float | str | None = None,
    slippage_bps: float = 5.0,
    commission_per_trade: float = 0.0,
    commission_per_share: float = 0.0,
    t212_mode: str = "demo",
    t212_allow_real_orders: bool = False,
    t212_placement_mode: str = "auto",
    ibkr_connection: IBKRConnection | None = None,
    ibkr_default_account: str | None = None,
    ibkr_allow_real_orders: bool = False,
    ibkr_timeframe_seconds: int = 60,
    lookback_days: int = 0,
) -> tuple[BarBus, OrderRouter]:
    """Construct (bus, router) for a session.

    `broker` picks the profile. Most args are profile-specific; passing
    one that doesn't apply (e.g. `t212_mode` with broker="ibkr") is a
    no-op rather than an error — keeps the call site uniform when a
    runner sweeps multiple brokers.

    Profile contracts:
      - `replay` needs `bars` (a list/iterable of Bar). `pace_seconds`
        controls replay speed.
      - `yfinance` / `t212` / `stub_live` need `session_date` for the
        Yahoo intraday fetch (one symbol per session — multi-symbol
        sessions need separate buses today).
      - `ibkr` reads bars live from the gateway; ignores `session_date`.
    """
    symbols = list(symbols)
    if broker == "replay":
        if bars is None:
            raise ValueError("broker='replay' requires `bars`")
        bus = ReplayBarBus(bars=bars, pace_seconds=pace_seconds)
        router = PaperOrderRouter(
            slippage_bps=slippage_bps,
            commission_per_trade=commission_per_trade,
            commission_per_share=commission_per_share,
        )
        return bus, router

    if broker == "yfinance":
        bus = _yfinance_bus(symbols, session_date, interval, pace_seconds, lookback_days)
        router = PaperOrderRouter(
            slippage_bps=slippage_bps,
            commission_per_trade=commission_per_trade,
            commission_per_share=commission_per_share,
        )
        return bus, router

    if broker == "t212":
        # Yahoo for bars (T212 has none), T212 for execution.
        bus = _yfinance_bus(symbols, session_date, interval, pace_seconds, lookback_days)
        router = T212OrderRouter(
            mode=t212_mode,
            allow_real_orders=t212_allow_real_orders,
            placement_mode=t212_placement_mode,
        )
        return bus, router

    if broker == "ibkr":
        conn = ibkr_connection or IBKRConnection()
        bus = IBKRBarBus(
            symbols=symbols,
            connection=conn,
            timeframe_seconds=ibkr_timeframe_seconds,
        )
        router = IBKRRouter(
            connection=conn,
            default_account=ibkr_default_account,
            allow_real_orders=ibkr_allow_real_orders,
        )
        return bus, router

    if broker == "stub_live":
        bus = _yfinance_bus(symbols, session_date, interval, pace_seconds, lookback_days)
        router = StubLiveRouter()
        return bus, router

    raise ValueError(
        f"Unknown broker profile {broker!r}. "
        f"Choose from: replay | yfinance | t212 | ibkr | stub_live"
    )


def build_multi_broker_session(
    brokers: list[str],
    symbols: Iterable[str],
    *,
    mode: str = "shadow",
    route_by_strategy_id: dict[str, str] | None = None,
    default_broker_name: str | None = None,
    bar_source: str = "yfinance",
    session_date: datetime | None = None,
    bars: Iterable[Bar] | None = None,
    pace_seconds: float | str | None = None,
    interval: str = "1m",
    ibkr_connection: IBKRConnection | None = None,
    ibkr_default_account: str | None = None,
    ibkr_allow_real_orders: bool = False,
    t212_mode: str = "demo",
    t212_allow_real_orders: bool = False,
    t212_placement_mode: str = "auto",
    slippage_bps: float = 5.0,
) -> tuple[BarBus, OrderRouter]:
    """Build a session that fans approved orders to >1 broker at once.

    `brokers` is the ordered list of names to wrap. Each name maps to
    a concrete router via the same single-broker `build_session`
    knobs. `mode` picks how to dispatch:
      - "shadow"   — every approval sent to every wrapped router; the
                     Ledger gets a separate per-broker book (strategy_id
                     suffixed `.<broker>`) so the operator can diff fills
                     across brokers post-hoc.
      - "dispatch" — `route_by_strategy_id[sid] = broker_name` picks
                     where each strategy's orders land. Missing entries
                     fall back to `default_broker_name`.

    `bar_source` picks the upstream bus. "yfinance" pairs Yahoo bars
    with the multi-router; "ibkr" pulls bars from the IBKR gateway
    (lets you compare T212 vs IBKR fills against real IBKR bars);
    "replay" runs deterministic replay against the multi-router. T212
    has no bars of its own so it never appears here.
    """
    symbols = list(symbols)
    if bar_source == "yfinance":
        bus = _yfinance_bus(symbols, session_date, interval, pace_seconds, lookback_days)
    elif bar_source == "ibkr":
        conn = ibkr_connection or IBKRConnection()
        bus = IBKRBarBus(symbols=symbols, connection=conn)
    elif bar_source == "replay":
        if bars is None:
            raise ValueError("bar_source='replay' requires `bars`")
        bus = ReplayBarBus(bars=bars, pace_seconds=pace_seconds)
    else:
        raise ValueError(f"Unknown bar_source {bar_source!r}")

    multi = MultiBrokerRouter(
        mode=mode,
        route_by_strategy_id=route_by_strategy_id or {},
        default_broker_name=default_broker_name,
    )
    for name in brokers:
        if name == "paper":
            multi.add(name, PaperOrderRouter(slippage_bps=slippage_bps))
        elif name == "stub_live":
            multi.add(name, StubLiveRouter())
        elif name == "t212":
            multi.add(name, T212OrderRouter(
                mode=t212_mode,
                allow_real_orders=t212_allow_real_orders,
                placement_mode=t212_placement_mode,
            ))
        elif name == "ibkr":
            conn = ibkr_connection or IBKRConnection()
            multi.add(name, IBKRRouter(
                connection=conn,
                default_account=ibkr_default_account,
                allow_real_orders=ibkr_allow_real_orders,
            ))
        else:
            raise ValueError(
                f"Unknown broker in multi-broker list: {name!r}. "
                f"Choose from: paper | stub_live | t212 | ibkr"
            )
    return bus, multi


def _yfinance_bus(
    symbols: list[str],
    session_date: datetime | None,
    interval: str,
    pace_seconds: float | str | None,
    lookback_days: int = 0,
) -> BarBus:
    """Yahoo-backed bus, wrapped with the standard source chain:
    Parquet cache → Yahoo → Finnhub. The cache makes repeated
    backtests of the same session free; the Finnhub fallback covers
    Yahoo's 60-day intraday window limit and transient 5xx blips.

    Single-symbol returns a `SourceBackedBus`; multi-symbol returns a
    `MultiSymbolSourceBackedBus` that fetches every symbol concurrently
    and replays a merged, timestamp-ordered stream through one bus.
    `lookback_days` extends the fetch window backwards from session_date
    so warmup-hungry strategies (e.g. ichimoku_fx_mr needs ~107 hourly
    days) can satisfy their gate before the session's own bars arrive."""
    if session_date is None:
        raise ValueError("yfinance / t212 / stub_live profiles require `session_date`")
    if not symbols:
        raise ValueError("yfinance / t212 / stub_live profiles require at least one symbol")
    source = default_bar_source()
    if len(symbols) == 1:
        return SourceBackedBus(
            source=source,
            symbol=symbols[0],
            session_date=session_date,
            interval=interval,
            pace_seconds=pace_seconds,
            lookback_days=lookback_days,
        )
    return MultiSymbolSourceBackedBus(
        source=source,
        symbols=symbols,
        session_date=session_date,
        interval=interval,
        pace_seconds=pace_seconds,
        lookback_days=lookback_days,
    )


def default_bar_source() -> BarSource:
    """The standard intraday source chain. Each provider sits behind
    its own `CachedSource` so a cache hit is always preferred over a
    network call, regardless of which provider previously served that
    session. FinnhubSource only contributes when its API key is set
    (otherwise its fetch returns []), so OSS-clone users without a
    Finnhub key just get Yahoo + cache and nothing breaks.

    Override by passing your own `BarSource` to `SourceBackedBus`
    when you need a different chain (e.g. Polygon-first for a sub-
    second latency strategy)."""
    return FallbackSource(sources=[
        CachedSource(inner=YfinanceSource()),
        CachedSource(inner=FinnhubSource()),
    ])


__all__ = ["build_session", "build_multi_broker_session", "default_bar_source"]
