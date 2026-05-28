"""Port of the trader-supplied ``Plotter.backtest`` 4-panel chart.

Four stacked subplots — equity, drawdown, sleeve cumulative returns,
gross exposure — with optional out-of-sample overlay and SPY
benchmark. The figure JSON drops into result_summary.charts and the
frontend's PlotlyChart component renders it as-is.

Differences from the trader's original:
  * ``EnsembleResult.sleeve_returns`` is ``dict[str, pd.Series]`` in
    our codebase (not a DataFrame). We adapt locally without changing
    the engine's data shape.
  * The trader's original looked up `result.sleeve_returns.abs().rolling(1).sum().mean(axis=1)`
    against a DataFrame. With the dict-of-Series form we sum across
    sleeves directly, which is the same arithmetic post-broadcast.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .registry import ChartBuilder, register_chart


PALETTE = {
    "strategy": "#06A77D",
    "oos": "#FFB703",
    "benchmark": "#A23B72",
    "red": "#E63946",
    "blue": "#2E86AB",
    "neutral": "gray",
}


def _drawdown_pct(eq: pd.Series) -> pd.Series:
    return (eq - eq.cummax()) / eq.cummax() * 100


def _sleeve_returns_df(sr: Any) -> pd.DataFrame:
    """Accept either a dict[str, Series] (our engine) or a DataFrame
    (trader's original) and return a DataFrame indexed by date with
    one column per sleeve."""
    if isinstance(sr, pd.DataFrame):
        return sr
    return pd.DataFrame(sr)


@register_chart
class Backtest4Panel(ChartBuilder):
    """4-panel backtest chart — equity, drawdown, sleeves, exposure."""

    name = "backtest_4panel"
    description = "Equity / drawdown / per-sleeve cumulative returns / vol-targeted gross exposure."
    required_inputs = ("result", "spy_equity", "spy_summary")

    def build(
        self,
        *,
        result: Any,                 # EnsembleResult
        spy_equity: pd.Series,
        spy_summary: dict,
        oos_equity: pd.Series | None = None,
        oos_summary: dict | None = None,
        title: str = "Multi-Asset Ichimoku Trend-Following Strategy",
    ) -> dict:
        # plotly is a soft dep — only imported when a chart is actually
        # rendered so unit tests for the rest of the engine don't pull
        # in the heavy plotly dist.
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        s = result.summary
        sleeve_df = _sleeve_returns_df(result.sleeve_returns)
        # Effective gross exposure: |sleeve_returns| summed across
        # sleeves, scaled by the vol-target multiplier the engine
        # applied. Multiply by 100 in the trace for percent.
        gross_exposure = sleeve_df.abs().sum(axis=1) * result.vol_scalar

        eq_title = (
            f"Equity | IS {s['cagr_pct']}%/Sharpe {s['sharpe']}"
            + (
                f" | OOS {oos_summary['cagr_pct']}%/Sharpe {oos_summary['sharpe']}"
                if oos_summary
                else ""
            )
            + f" | SPY {spy_summary['cagr_pct']}%/Sharpe {spy_summary['sharpe']}"
        )
        dd_title = (
            f"Drawdown | Strategy {s['max_drawdown_pct']}% | "
            f"SPY {spy_summary['max_drawdown_pct']}%"
        )

        fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
            row_heights=[0.4, 0.2, 0.2, 0.2],
            subplot_titles=(
                eq_title, dd_title,
                "Sleeve Cumulative Returns",
                "Effective Gross Exposure (post vol-target)",
            ),
        )

        # ── Row 1: Equity ──
        fig.add_trace(go.Scatter(
            x=result.equity.index, y=result.equity.values,
            name="In-Sample",
            line=dict(color=PALETTE["strategy"], width=2.5),
        ), row=1, col=1)
        if oos_equity is not None and oos_summary is not None:
            fig.add_trace(go.Scatter(
                x=oos_equity.index, y=oos_equity.values,
                name=f"OOS (Sharpe {oos_summary['sharpe']})",
                line=dict(color=PALETTE["oos"], width=2, dash="dot"),
            ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=spy_equity.index, y=spy_equity.values,
            name="SPY B&H",
            line=dict(color=PALETTE["benchmark"], width=1.5, dash="dash"),
        ), row=1, col=1)

        # ── Row 2: Drawdown ──
        fig.add_trace(go.Scatter(
            x=result.equity.index,
            y=_drawdown_pct(result.equity).values,
            fill="tozeroy",
            line=dict(color=PALETTE["strategy"]),
            showlegend=False,
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=spy_equity.index,
            y=_drawdown_pct(spy_equity).values,
            line=dict(color=PALETTE["benchmark"], width=1, dash="dash"),
            showlegend=False,
        ), row=2, col=1)

        # ── Row 3: Sleeves ──
        colors = [
            PALETTE["blue"], PALETTE["red"], PALETTE["oos"], PALETTE["strategy"],
        ]
        sleeve_cum = (1 + sleeve_df).cumprod()
        for i, col in enumerate(sleeve_cum.columns):
            fig.add_trace(go.Scatter(
                x=sleeve_cum.index, y=sleeve_cum[col].values,
                name=str(col),
                line=dict(color=colors[i % len(colors)], width=1.5),
            ), row=3, col=1)

        # ── Row 4: Gross exposure ──
        fig.add_trace(go.Scatter(
            x=gross_exposure.index,
            y=(gross_exposure * 100).values,
            fill="tozeroy",
            line=dict(color=PALETTE["strategy"], width=1),
            showlegend=False,
        ), row=4, col=1)

        fig.update_yaxes(title="Equity ($)", row=1, col=1, type="log")
        fig.update_yaxes(title="DD (%)", row=2, col=1)
        fig.update_yaxes(title="Cum (×)", row=3, col=1, type="log")
        fig.update_yaxes(title="Gross %", row=4, col=1)
        fig.update_layout(
            height=1100,
            template="plotly_white",
            hovermode="x unified",
            legend=dict(orientation="h", y=1.04, x=1, xanchor="right"),
            title=title,
        )
        return fig.to_plotly_json()
