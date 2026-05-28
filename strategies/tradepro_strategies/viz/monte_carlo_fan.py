"""Port of the trader-supplied ``Plotter.monte_carlo`` chart.

Top panel: fan of equity-path percentiles (5/95, 10/90, 25/75 bands +
median). Bottom panel: histogram of final values with P5 / Median / P95
vertical lines.

Note: the trader's `MonteCarloResult` had `initial`, `years`, and
`final_values` fields. Our engine exposes `paths`, `summary`, `n_sims`,
`years`. We derive `initial` from paths[:, 0] and `final_values` from
paths[:, -1] in the builder so callers don't have to massage the input.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .registry import ChartBuilder, register_chart
from .backtest_4panel import PALETTE


@register_chart
class MonteCarloFan(ChartBuilder):
    """Monte Carlo percentile fan + final-value histogram."""

    name = "monte_carlo_fan"
    description = "Percentile fan of bootstrap equity paths + final-value histogram."
    required_inputs = ("mc",)

    def build(self, *, mc: Any) -> dict:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        paths = mc.paths  # shape (n_sims, n_days+1)
        if paths.ndim != 2 or paths.shape[0] == 0:
            raise ValueError("Monte Carlo paths must be a non-empty 2-D array")
        initial = float(paths[:, 0].mean())
        final_values = paths[:, -1]
        years_axis = np.arange(paths.shape[1]) / 252

        fig = make_subplots(
            rows=2, cols=1, vertical_spacing=0.10, row_heights=[0.6, 0.4],
            subplot_titles=(
                f"${initial:,.0f} → {mc.years} years: Percentile Fan ({len(final_values):,} sims)",
                "Distribution of Final Values",
            ),
        )

        # ── Row 1: Percentile bands ──
        bands = [(5, 95, 0.15, "5-95%"),
                 (10, 90, 0.25, "10-90%"),
                 (25, 75, 0.35, "25-75%")]
        for low, high, alpha, label in bands:
            band_lo = np.percentile(paths, low, axis=0)
            band_hi = np.percentile(paths, high, axis=0)
            fig.add_trace(go.Scatter(
                x=years_axis, y=band_hi,
                line=dict(width=0), showlegend=False, hoverinfo="skip",
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=years_axis, y=band_lo,
                fill="tonexty",
                fillcolor=f"rgba(6,167,125,{alpha})",
                line=dict(width=0),
                name=label, hoverinfo="skip",
            ), row=1, col=1)

        # Median line on top of the bands.
        fig.add_trace(go.Scatter(
            x=years_axis,
            y=np.percentile(paths, 50, axis=0),
            name="Median",
            line=dict(color=PALETTE["strategy"], width=3),
        ), row=1, col=1)

        fig.add_hline(
            y=initial,
            line_dash="dot", line_color="red", opacity=0.5,
            row=1, col=1,
            annotation_text=f"Initial ${initial:,.0f}",
        )

        # ── Row 2: Final-value histogram ──
        fig.add_trace(go.Histogram(
            x=final_values, nbinsx=80,
            marker=dict(
                color=PALETTE["strategy"],
                line=dict(color="white", width=1),
            ),
            showlegend=False,
        ), row=2, col=1)
        for p, label in [(5, "P5"), (50, "Median"), (95, "P95")]:
            v = float(np.percentile(final_values, p))
            fig.add_vline(
                x=v, line_dash="dash", line_color="black", opacity=0.6,
                row=2, col=1,
                annotation_text=f"{label}: ${v:,.0f}",
            )

        fig.update_yaxes(title="Value ($)", row=1, col=1, type="log")
        fig.update_xaxes(title="Years", row=1, col=1)
        fig.update_yaxes(title="Frequency", row=2, col=1)
        fig.update_xaxes(title="Final Value ($)", row=2, col=1, type="log")

        median_final = float(np.percentile(final_values, 50))
        p_double = float((final_values >= 2 * initial).mean() * 100)
        p_loss = float((final_values < initial).mean() * 100)
        fig.update_layout(
            height=900, template="plotly_white",
            title=(
                f"Monte Carlo | Median ${median_final:,.0f} | "
                f"P(double)={p_double:.0f}% | P(loss)={p_loss:.1f}%"
            ),
        )
        return fig.to_plotly_json()
