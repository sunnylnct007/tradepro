# ============================================================
# visualization.py
# ============================================================
"""All Plotly charts in a single Plotter class."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portfolio import EnsembleResult, MonteCarloResult


class Plotter:
    """Stateless namespace for chart construction."""

    PALETTE = {
        "strategy": "#06A77D",
        "oos": "#FFB703",
        "benchmark": "#A23B72",
        "red": "#E63946",
        "blue": "#2E86AB",
        "neutral": "gray",
    }

    @staticmethod
    def _dd(eq: pd.Series) -> pd.Series:
        return (eq - eq.cummax()) / eq.cummax() * 100

    @classmethod
    def backtest(
        cls,
        result: EnsembleResult,
        spy_equity: pd.Series,
        spy_summary: dict,
        oos_equity: pd.Series | None = None,
        oos_summary: dict | None = None,
    ) -> go.Figure:
        """Full 4-panel backtest chart: equity, drawdown, sleeves, exposure."""
        s = result.summary
        gross_exposure = result.sleeve_returns.abs().rolling(1).sum().mean(axis=1) * result.vol_scalar
        fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
            row_heights=[0.4, 0.2, 0.2, 0.2],
            subplot_titles=(
                f"Equity | IS {s['cagr_pct']}%/Sharpe {s['sharpe']}"
                + (f" | OOS {oos_summary['cagr_pct']}%/Sharpe {oos_summary['sharpe']}" if oos_summary else "")
                + f" | SPY {spy_summary['cagr_pct']}%/Sharpe {spy_summary['sharpe']}",
                f"Drawdown | Strategy {s['max_drawdown_pct']}% | SPY {spy_summary['max_drawdown_pct']}%",
                "Sleeve Cumulative Returns",
                "Effective Gross Exposure (post vol-target)",
            ),
        )
        # Equity
        fig.add_trace(go.Scatter(x=result.equity.index, y=result.equity.values,
                                name="In-Sample", line=dict(color=cls.PALETTE["strategy"], width=2.5)), row=1, col=1)
        if oos_equity is not None:
            fig.add_trace(go.Scatter(x=oos_equity.index, y=oos_equity.values,
                                    name=f"OOS (Sharpe {oos_summary['sharpe']})",
                                    line=dict(color=cls.PALETTE["oos"], width=2, dash="dot")), row=1, col=1)
        fig.add_trace(go.Scatter(x=spy_equity.index, y=spy_equity.values, name="SPY B&H",
                                line=dict(color=cls.PALETTE["benchmark"], width=1.5, dash="dash")), row=1, col=1)
        # Drawdown
        fig.add_trace(go.Scatter(x=result.equity.index, y=cls._dd(result.equity).values,
                                fill="tozeroy", line=dict(color=cls.PALETTE["strategy"]),
                                showlegend=False), row=2, col=1)
        fig.add_trace(go.Scatter(x=spy_equity.index, y=cls._dd(spy_equity).values,
                                line=dict(color=cls.PALETTE["benchmark"], width=1, dash="dash"),
                                showlegend=False), row=2, col=1)
        # Sleeves
        colors = [cls.PALETTE["blue"], cls.PALETTE["red"], cls.PALETTE["oos"], cls.PALETTE["strategy"]]
        sleeve_cum = (1 + result.sleeve_returns).cumprod()
        for i, col in enumerate(sleeve_cum.columns):
            fig.add_trace(go.Scatter(x=sleeve_cum.index, y=sleeve_cum[col].values,
                                    name=col, line=dict(color=colors[i % len(colors)], width=1.5)),
                         row=3, col=1)
        # Exposure
        fig.add_trace(go.Scatter(x=gross_exposure.index, y=gross_exposure.values * 100,
                                fill="tozeroy", line=dict(color=cls.PALETTE["strategy"], width=1),
                                showlegend=False), row=4, col=1)
        fig.update_yaxes(title="Equity ($)", row=1, col=1, type="log")
        fig.update_yaxes(title="DD (%)", row=2, col=1)
        fig.update_yaxes(title="Cum (×)", row=3, col=1, type="log")
        fig.update_yaxes(title="Gross %", row=4, col=1)
        fig.update_layout(
            height=1100, template="plotly_white", hovermode="x unified",
            legend=dict(orientation="h", y=1.04, x=1, xanchor="right"),
            title="Multi-Asset Ichimoku Trend-Following Strategy",
        )
        return fig

    @classmethod
    def monte_carlo(cls, mc: MonteCarloResult) -> go.Figure:
        """Fan chart + final-value histogram from Monte Carlo simulation."""
        years_axis = np.arange(mc.paths.shape[1]) / 252
        fv = mc.final_values
        fig = make_subplots(
            rows=2, cols=1, vertical_spacing=0.10, row_heights=[0.6, 0.4],
            subplot_titles=(
                f"${mc.initial:,.0f} → {mc.years} years: Percentile Fan ({len(fv):,} sims)",
                "Distribution of Final Values",
            ),
        )
        # Bands
        for low, high, alpha, label in [(5, 95, 0.15, "5-95%"),
                                         (10, 90, 0.25, "10-90%"),
                                         (25, 75, 0.35, "25-75%")]:
            band_lo = np.percentile(mc.paths, low, axis=0)
            band_hi = np.percentile(mc.paths, high, axis=0)
            fig.add_trace(go.Scatter(x=years_axis, y=band_hi, line=dict(width=0),
                                    showlegend=False, hoverinfo="skip"), row=1, col=1)
            fig.add_trace(go.Scatter(x=years_axis, y=band_lo, fill="tonexty",
                                    fillcolor=f"rgba(6,167,125,{alpha})", line=dict(width=0),
                                    name=label, hoverinfo="skip"), row=1, col=1)
        # Median
        fig.add_trace(go.Scatter(x=years_axis, y=np.percentile(mc.paths, 50, axis=0),
                                name="Median", line=dict(color=cls.PALETTE["strategy"], width=3)),
                     row=1, col=1)
        fig.add_hline(y=mc.initial, line_dash="dot", line_color="red", opacity=0.5,
                     row=1, col=1, annotation_text=f"Initial ${mc.initial:,.0f}")
        # Histogram
        fig.add_trace(go.Histogram(x=fv, nbinsx=80,
                                  marker=dict(color=cls.PALETTE["strategy"],
                                              line=dict(color="white", width=1)),
                                  showlegend=False), row=2, col=1)
        for p, label in [(5, "P5"), (50, "Median"), (95, "P95")]:
            v = np.percentile(fv, p)
            fig.add_vline(x=v, line_dash="dash", line_color="black", opacity=0.6, row=2, col=1,
                         annotation_text=f"{label}: ${v:,.0f}")
        fig.update_yaxes(title="Value ($)", row=1, col=1, type="log")
        fig.update_xaxes(title="Years", row=1, col=1)
        fig.update_yaxes(title="Frequency", row=2, col=1)
        fig.update_xaxes(title="Final Value ($)", row=2, col=1, type="log")
        med = np.percentile(fv, 50)
        p_dbl = (fv >= 2 * mc.initial).mean() * 100
        p_lose = (fv < mc.initial).mean() * 100
        fig.update_layout(height=900, template="plotly_white",
                         title=f"Monte Carlo | Median ${med:,.0f} | P(double)={p_dbl:.0f}% | P(loss)={p_lose:.1f}%")
        return fig
