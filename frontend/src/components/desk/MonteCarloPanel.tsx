/**
 * MonteCarloPanel — renders a block-bootstrap Monte Carlo projection (the
 * percentile fan + headline chips + an in-tool explainer). Source-agnostic:
 * feed it server-computed portfolio MC or client-computed per-symbol MC (both
 * share the `MonteCarloData` shape), and it renders identically.
 *
 * Every number carries an explainer (per the project rule: never ship a metric
 * a newcomer can't decode from the UI alone) — the "What am I looking at?"
 * details block spells out the method + each chip.
 */
import { useState } from "react";
import { PlotlyChart } from "../PlotlyChart";
import type { MonteCarloData } from "../../util/monteCarlo";

const STRATEGY = "#06a77d";

function fmtMoney(v: number, ccy: string): string {
  const sym = ccy === "GBP" ? "£" : ccy === "EUR" ? "€" : "$";
  return `${sym}${Math.round(v).toLocaleString()}`;
}

export function MonteCarloPanel({
  mc,
  title,
  ccy = "USD",
  subtitle,
}: {
  mc: MonteCarloData;
  title: string;
  ccy?: string;
  subtitle?: string;
}) {
  const s = mc.summary;
  const p5 = s.percentiles.p5 ?? { final_value: 0, cagr_pct: 0, multiple: 0 };
  const p50 = s.percentiles.p50 ?? { final_value: 0, cagr_pct: 0, multiple: 0 };
  const p95 = s.percentiles.p95 ?? { final_value: 0, cagr_pct: 0, multiple: 0 };
  const ddMedian = s.max_dd_pct?.p50 ?? 0;

  const chips: { label: string; value: string; tone?: "good" | "bad" | "neutral" }[] = [
    { label: "Median final", value: fmtMoney(p50.final_value, ccy), tone: "neutral" },
    { label: "Median CAGR", value: `${p50.cagr_pct.toFixed(1)}%`, tone: p50.cagr_pct >= 0 ? "good" : "bad" },
    { label: "P5 final (unlucky)", value: fmtMoney(p5.final_value, ccy), tone: "neutral" },
    { label: "P95 final (lucky)", value: fmtMoney(p95.final_value, ccy), tone: "good" },
    { label: "P(double)", value: `${(s.p_double * 100).toFixed(1)}%`, tone: "good" },
    { label: "P(5x)", value: `${(s.p_5x * 100).toFixed(1)}%`, tone: s.p_double > 0.5 ? "good" : "neutral" },
    { label: "P(lose money)", value: `${(s.p_lose_money * 100).toFixed(1)}%`, tone: s.p_lose_money > 0.1 ? "bad" : "good" },
    { label: "Median max drawdown", value: `${ddMedian.toFixed(1)}%`, tone: ddMedian < -20 ? "bad" : "neutral" },
  ];
  const toneColor = (t?: string) => (t === "good" ? "#1fc16b" : t === "bad" ? "#ef4444" : "var(--text-dim)");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text)" }}>{title}</span>
          <span style={{
            fontSize: 9, textTransform: "uppercase", letterSpacing: "0.06em",
            padding: "2px 6px", borderRadius: 4, border: "1px solid var(--border)",
            color: mc.source === "server" ? "#1fc16b" : "var(--accent, #4f8cff)",
          }}>
            {mc.source === "server" ? "strategy · server" : "per-symbol · client"}
          </span>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          {mc.n_sims.toLocaleString()} bootstrap paths · {mc.years}y horizon · {fmtMoney(mc.initial, ccy)} start
          {subtitle ? ` · ${subtitle}` : ""}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
        {chips.map((c) => (
          <div key={c.label} style={{
            padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 6,
            background: "var(--surface-1, #0b1220)",
          }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{c.label}</div>
            <div style={{ fontSize: 15, fontWeight: 700, fontFamily: "ui-monospace, monospace", color: toneColor(c.tone) }}>{c.value}</div>
          </div>
        ))}
      </div>

      <PlotlyChart figure={buildFanFigure(mc, ccy)} />

      <details style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6 }}>
        <summary style={{ cursor: "pointer", color: "var(--text-dim)", fontWeight: 600 }}>
          What am I looking at?
        </summary>
        <div style={{ marginTop: 6 }}>
          <p style={{ margin: "4px 0" }}>
            <strong>Block-bootstrap Monte Carlo.</strong> We draw random{" "}
            <strong>21-day blocks</strong> (≈1 month) of the {mc.source === "server" ? "strategy's" : "symbol's"}{" "}
            <em>historical daily returns</em> and stitch them into {mc.n_sims.toLocaleString()} synthetic{" "}
            {mc.years}-year paths, each starting at {fmtMoney(mc.initial, ccy)} and compounding. Blocks
            (not single days) keep momentum + fat tails. <strong>It is a range of plausible futures from
            past behaviour — not a forecast.</strong>
          </p>
          <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
            <li><strong>The cone</strong> = the spread of paths over time: shaded P5–P95 (outer) and P25–P75 (inner), solid median (P50). Y-axis is log so % moves read evenly.</li>
            <li><strong>Median / P5 / P95 final</strong> = where the middle, unlucky (bottom 5%), and lucky (top 5%) paths end up.</li>
            <li><strong>P(double) / P(5x) / P(lose money)</strong> = share of paths ending ≥2×, ≥5×, or below the starting stake.</li>
            <li><strong>Median max drawdown</strong> = the typical worst peak-to-trough dip along a path.</li>
          </ul>
          <p style={{ margin: "4px 0" }}>
            Per-symbol runs are computed in your browser from daily candles ({mc.source === "client"
              ? "this instrument"
              : "the strategy's ensemble returns"}); same seed ⇒ same projection (reproducible).
          </p>
        </div>
      </details>
    </div>
  );
}

function buildFanFigure(mc: MonteCarloData, ccy: string) {
  const { years_axis, q05, q25, q50, q75, q95 } = mc.fan_chart;
  const sym = ccy === "GBP" ? "£" : ccy === "EUR" ? "€" : "$";
  const traces: Array<Record<string, unknown>> = [
    { x: years_axis, y: q95, type: "scatter", mode: "lines", name: "P95", line: { color: STRATEGY, width: 0 }, showlegend: false },
    { x: years_axis, y: q05, type: "scatter", mode: "lines", name: "P5–P95", fill: "tonexty", fillcolor: "rgba(6,167,125,0.10)", line: { color: STRATEGY, width: 0 } },
    { x: years_axis, y: q75, type: "scatter", mode: "lines", name: "P75", line: { color: STRATEGY, width: 0 }, showlegend: false },
    { x: years_axis, y: q25, type: "scatter", mode: "lines", name: "P25–P75", fill: "tonexty", fillcolor: "rgba(6,167,125,0.22)", line: { color: STRATEGY, width: 0 } },
    { x: years_axis, y: q50, type: "scatter", mode: "lines", name: "Median", line: { color: STRATEGY, width: 2.5 } },
  ];
  return {
    data: traces,
    layout: {
      template: "plotly_dark",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      xaxis: { title: "Years from now" },
      yaxis: { title: `Value (${sym}, start ${sym}${mc.initial.toLocaleString()})`, type: "log" },
      hovermode: "x unified",
      height: 380,
      margin: { l: 70, r: 16, t: 14, b: 44 },
      legend: { orientation: "h", y: 1.06, x: 1, xanchor: "right" },
    },
    config: { responsive: true, displaylogo: false },
  };
}
