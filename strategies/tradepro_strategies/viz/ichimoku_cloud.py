"""Ichimoku cloud chart with optional fill markers.

For the live-trading workflow: traders want to see "where did the
signal fire vs where the cloud actually was?" — a chart with the
price candles, the filled Ichimoku cloud, the Tenkan/Kijun lines,
and markers where fills actually happened. Validates intent vs
reality without re-running the strategy.

Inputs (all keyword):
    symbol: str — for the title.
    df: pd.DataFrame indexed by datetime with high/low/close columns
        (case-insensitive). Typically the daily series the strategy
        used to compute the signal.
    fills: optional list[dict] each with {time, side, price}. Side
        BUY plots as a green triangle-up, SELL as red triangle-down.
    tenkan/kijun/senkou_b/displacement: Ichimoku params; default to
        ichimoku_equity's settings (5/32/50/32).
"""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from ..indicators import ichimoku
from .backtest_4panel import PALETTE
from .registry import ChartBuilder, register_chart


@register_chart
class IchimokuCloud(ChartBuilder):
    """Per-symbol Ichimoku cloud + entry/exit markers."""

    name = "ichimoku_cloud"
    description = "Daily candles, filled Ichimoku cloud, Tenkan/Kijun lines, fill markers."
    required_inputs = ("symbol", "df")

    def build(
        self,
        *,
        symbol: str,
        df: pd.DataFrame,
        fills: Sequence[dict] | None = None,
        tenkan: int = 5,
        kijun: int = 32,
        senkou_b: int = 50,
        displacement: int = 32,
    ) -> dict:
        import plotly.graph_objects as go

        # Normalise column casing — strategy callers tend to pass DFs
        # with whatever case Yahoo/Stooq returned.
        cols = {c.lower(): c for c in df.columns}
        try:
            high = df[cols["high"]]
            low = df[cols["low"]]
            close = df[cols["close"]]
            open_ = df[cols.get("open", cols["close"])]
        except KeyError as exc:
            raise ValueError(
                f"ichimoku_cloud needs high/low/close columns in df for {symbol!r}; "
                f"got {list(df.columns)}"
            ) from exc

        ich = ichimoku(
            high=high, low=low, close=close,
            tenkan=tenkan, kijun=kijun,
            senkou_b=senkou_b, displacement=displacement,
        )
        # Trim to the trailing 250 rows so the chart stays readable
        # even when the strategy keeps years of cache.
        tail = 250
        ich_tail = ich.tail(tail)
        idx = ich_tail.index
        close_tail = close.reindex(idx)
        open_tail = open_.reindex(idx)
        high_tail = high.reindex(idx)
        low_tail = low.reindex(idx)

        fig = go.Figure()

        # ── Cloud (Senkou A vs Senkou B shaded area) ──
        # We draw two stacked scatter traces: Senkou A as the visible
        # line, Senkou B with fill="tonexty" so the area between them
        # fills. Tone differs by whether A is above B (bull) or below.
        fig.add_trace(go.Scatter(
            x=idx, y=ich_tail["senkou_a"],
            line=dict(width=0), showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=idx, y=ich_tail["senkou_b"],
            line=dict(width=0),
            fill="tonexty",
            fillcolor="rgba(6,167,125,0.18)",
            name="Cloud",
            hoverinfo="skip",
        ))

        # ── Candles ──
        fig.add_trace(go.Candlestick(
            x=idx,
            open=open_tail, high=high_tail, low=low_tail, close=close_tail,
            name="Price",
            increasing=dict(line=dict(color=PALETTE["strategy"])),
            decreasing=dict(line=dict(color=PALETTE["red"])),
            showlegend=False,
        ))

        # ── Tenkan + Kijun ──
        fig.add_trace(go.Scatter(
            x=idx, y=ich_tail["tenkan"],
            name=f"Tenkan ({tenkan})",
            line=dict(color=PALETTE["blue"], width=1.5),
        ))
        fig.add_trace(go.Scatter(
            x=idx, y=ich_tail["kijun"],
            name=f"Kijun ({kijun})",
            line=dict(color=PALETTE["benchmark"], width=1.5),
        ))

        # ── Fill markers ──
        if fills:
            buy_x, buy_y, sell_x, sell_y = [], [], [], []
            for f in fills:
                t = f.get("time") or f.get("fill_time")
                p = f.get("price") or f.get("fill_price")
                s = (f.get("side") or "").upper()
                if t is None or p is None:
                    continue
                if s == "BUY":
                    buy_x.append(t); buy_y.append(p)
                elif s == "SELL":
                    sell_x.append(t); sell_y.append(p)
            if buy_x:
                fig.add_trace(go.Scatter(
                    x=buy_x, y=buy_y, mode="markers", name="Buys",
                    marker=dict(symbol="triangle-up", size=14,
                                color=PALETTE["strategy"], line=dict(width=1, color="white")),
                ))
            if sell_x:
                fig.add_trace(go.Scatter(
                    x=sell_x, y=sell_y, mode="markers", name="Sells",
                    marker=dict(symbol="triangle-down", size=14,
                                color=PALETTE["red"], line=dict(width=1, color="white")),
                ))

        fig.update_layout(
            title=f"Ichimoku Cloud — {symbol}",
            template="plotly_white",
            height=560,
            hovermode="x unified",
            xaxis=dict(rangeslider=dict(visible=False)),
            legend=dict(orientation="h", y=1.05, x=1, xanchor="right"),
        )
        return fig.to_plotly_json()


__all__: list[Any] = ["IchimokuCloud"]
