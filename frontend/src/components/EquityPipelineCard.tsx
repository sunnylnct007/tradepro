/**
 * EquityPipelineCard — strategy validation surface backed by the
 * tradepro-equity-pipeline artifact (port of docs/main 4.py).
 *
 * Renders the trader's "is this strategy real?" view on top of the
 * live signals/orders cards. Three layers:
 *
 *   1. Summary band (Sharpe / CAGR / MaxDD: in-sample vs walk-forward
 *      OOS vs SPY benchmark). One row, scannable.
 *   2. 4-panel backtest chart (Plotly): equity, drawdown, sleeve
 *      cumulative returns, gross exposure. Matches the trader's
 *      visualization.py Plotter.backtest output.
 *   3. Monte Carlo fan chart with p5/p25/p50/p75/p95 bands + a
 *      "median / p_double / p_5x / max_dd" summary chip row.
 *
 * Per-window walk-forward results live below the chart as a table so
 * the trader can see per-year stability (is the strategy OOS-robust
 * or did one year carry the whole period?).
 *
 * Empty state: 404 from /api/equity-pipeline/{strategy}/latest →
 * shows a one-line CLI hint instead of an error. No automatic trigger
 * — the trader runs it on the Mac when they want a fresh validation
 * (run takes ~10s with --no-hibeta, ~10min cold with hibeta).
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { EquityPipelineEnvelope } from "../api/client";
import { CockpitCard } from "./CockpitCard";
import { PlotlyChart } from "./PlotlyChart";

interface Props {
  strategy: string;
  label?: string;
}

export function EquityPipelineCard({ strategy, label = "latest" }: Props) {
  const [env, setEnv] = useState<EquityPipelineEnvelope | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.equityPipelineLatest(strategy, label);
      setEnv(data);
      setError(null);
    } catch (e) {
      // 404 = no artifact yet; show the runbook hint. Any other error
      // surfaces as a small message so the operator can act on it.
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("404")) setError("404");
      else setError(msg);
      setEnv(null);
    } finally {
      setLoading(false);
    }
  }, [strategy, label]);

  useEffect(() => { void load(); }, [load]);

  if (loading) {
    return (
      <CockpitCard id="equity-pipeline" title="Strategy validation">
        <div style={{ padding: 12, color: "var(--text-muted)", fontSize: 13 }}>
          Loading pipeline artifact…
        </div>
      </CockpitCard>
    );
  }

  if (error === "404" || !env) {
    return (
      <CockpitCard id="equity-pipeline" title="Strategy validation" fullWidth>
        <div style={{
          padding: 16, borderRadius: 8,
          background: "rgba(245,158,11,0.08)",
          border: "1px solid rgba(245,158,11,0.25)",
          fontSize: 13, color: "var(--text)",
        }}>
          <div style={{ fontWeight: 700, color: "#f59e0b", marginBottom: 6 }}>
            No pipeline artifact yet
          </div>
          <div style={{ color: "var(--text-dim)", marginBottom: 8 }}>
            The trader-spec backtest (sleeves + ensemble + walk-forward +
            Monte Carlo) hasn't been run for <code>{strategy}</code> yet.
            Run on the worker host:
          </div>
          <pre style={{
            background: "var(--surface-1, #0b1220)",
            border: "1px solid var(--border)",
            padding: "8px 10px", borderRadius: 6, fontSize: 12,
            overflow: "auto",
          }}>
            tradepro-equity-pipeline --push
          </pre>
        </div>
      </CockpitCard>
    );
  }

  if (error) {
    return (
      <CockpitCard id="equity-pipeline" title="Strategy validation">
        <div style={{ padding: 12, color: "var(--down)", fontSize: 12 }}>
          Pipeline fetch error: {error}
        </div>
      </CockpitCard>
    );
  }

  const a = env.artifact;
  const cfg = a.config;
  const window = `${cfg.start_date} → ${cfg.end_date}`;
  const sleeves = a.sleeves_meta.map((s) => `${s.name}(${s.n_tickers})`).join(", ");

  return (
    <CockpitCard
      id="equity-pipeline"
      title="Strategy validation — trader-spec backtest"
      fullWidth
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Header context */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            window <strong>{window}</strong> · sleeves <strong>{sleeves}</strong>
            {a.timings_sec.total ? <> · ran in <strong>{a.timings_sec.total}s</strong></> : null}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            as of {new Date(env.asOfUtc).toLocaleString()}
            {env.uploadedBy ? <> · {env.uploadedBy}</> : null}
          </div>
        </div>

        {/* Summary band */}
        <SummaryBand
          inSample={a.in_sample}
          oos={a.walk_forward.summary}
          spy={a.spy_benchmark}
        />

        {/* 4-panel backtest chart */}
        <PlotlyChart figure={buildBacktestFigure(a)} />

        {/* Walk-forward per-window table */}
        <WalkForwardTable rows={a.walk_forward.per_window} />

        {/* Monte Carlo */}
        {a.monte_carlo ? (
          <>
            <MonteCarloSummary mc={a.monte_carlo} />
            <PlotlyChart figure={buildMonteCarloFigure(a.monte_carlo)} />
          </>
        ) : (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Monte Carlo skipped on this run (--no-mc).
          </div>
        )}
      </div>
    </CockpitCard>
  );
}

// ── summary band ────────────────────────────────────────────────────────────

function SummaryBand({
  inSample, oos, spy,
}: {
  inSample: Record<string, number | string>;
  oos: Record<string, number | string>;
  spy: Record<string, number | string>;
}) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "auto repeat(4, 1fr)",
      gap: 1,
      background: "var(--border)",
      border: "1px solid var(--border)",
      borderRadius: 8, overflow: "hidden",
    }}>
      <Cell head>Source</Cell>
      <Cell head right>Sharpe</Cell>
      <Cell head right>CAGR %</Cell>
      <Cell head right>Max DD %</Cell>
      <Cell head right>Calmar</Cell>

      <Cell strong>In-sample</Cell>
      <Cell right mono>{fmt(inSample.sharpe)}</Cell>
      <Cell right mono>{fmt(inSample.cagr_pct)}</Cell>
      <Cell right mono>{fmt(inSample.max_drawdown_pct)}</Cell>
      <Cell right mono>{fmt(inSample.calmar)}</Cell>

      <Cell strong style={{ color: "#FFB703" }}>Walk-fwd OOS</Cell>
      <Cell right mono>{fmt(oos.sharpe)}</Cell>
      <Cell right mono>{fmt(oos.cagr_pct)}</Cell>
      <Cell right mono>{fmt(oos.max_drawdown_pct)}</Cell>
      <Cell right mono>{fmt(oos.calmar)}</Cell>

      <Cell strong style={{ color: "#A23B72" }}>SPY B&amp;H</Cell>
      <Cell right mono>{fmt(spy.sharpe)}</Cell>
      <Cell right mono>{fmt(spy.cagr_pct)}</Cell>
      <Cell right mono>{fmt(spy.max_drawdown_pct)}</Cell>
      <Cell right mono>{fmt(spy.calmar)}</Cell>
    </div>
  );
}

function Cell({
  children, head, right, strong, mono, style,
}: {
  children?: React.ReactNode;
  head?: boolean;
  right?: boolean;
  strong?: boolean;
  mono?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <div style={{
      padding: "6px 10px", fontSize: head ? 10 : 12,
      textTransform: head ? "uppercase" : undefined,
      letterSpacing: head ? "0.06em" : undefined,
      textAlign: right ? "right" : "left",
      fontWeight: head ? 700 : strong ? 600 : 400,
      color: head ? "var(--text-muted)" : "var(--text)",
      background: "var(--surface-1, #0b1220)",
      fontFamily: mono ? "ui-monospace, Menlo, Monaco, monospace" : undefined,
      ...style,
    }}>
      {children}
    </div>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v.toFixed(2);
  return String(v);
}

// ── walk-forward table ─────────────────────────────────────────────────────

function WalkForwardTable({ rows }: {
  rows: Array<{ test_year: string; vol_scalar: number; sharpe: number; cagr_pct: number; n_days: number }>;
}) {
  if (!rows.length) return null;
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
        Walk-forward windows
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ color: "var(--text-muted)", textAlign: "right" }}>
            <th style={{ textAlign: "left", padding: "4px 8px" }}>Test year</th>
            <th style={{ padding: "4px 8px" }}>Vol scalar</th>
            <th style={{ padding: "4px 8px" }}>Sharpe</th>
            <th style={{ padding: "4px 8px" }}>CAGR %</th>
            <th style={{ padding: "4px 8px" }}>Days</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.test_year} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "4px 8px", fontWeight: 600 }}>{r.test_year}</td>
              <td style={{ padding: "4px 8px", textAlign: "right", fontFamily: "ui-monospace, monospace" }}>{r.vol_scalar.toFixed(2)}</td>
              <td style={{ padding: "4px 8px", textAlign: "right", fontFamily: "ui-monospace, monospace" }}>{r.sharpe.toFixed(2)}</td>
              <td style={{ padding: "4px 8px", textAlign: "right", fontFamily: "ui-monospace, monospace" }}>{r.cagr_pct.toFixed(2)}</td>
              <td style={{ padding: "4px 8px", textAlign: "right", fontFamily: "ui-monospace, monospace" }}>{r.n_days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── monte carlo summary chips ──────────────────────────────────────────────

function MonteCarloSummary({ mc }: { mc: NonNullable<EquityPipelineEnvelope["artifact"]["monte_carlo"]> }) {
  const s = mc.summary as Record<string, unknown>;
  const pctls = (s.percentiles as Record<string, { final_value?: number; cagr_pct?: number }>) ?? {};
  const p5 = pctls.p5 ?? {};
  const p50 = pctls.p50 ?? {};
  const p95 = pctls.p95 ?? {};
  const pDouble = (s.p_double as number) ?? 0;
  const p5x = (s.p_5x as number) ?? 0;
  const pLose = (s.p_lose_money as number) ?? 0;
  const ddMedian = (s.max_dd_pct as { p50?: number })?.p50 ?? 0;

  const chips: { label: string; value: string; tone?: "good" | "bad" | "neutral" }[] = [
    { label: "Median final", value: `$${Math.round(p50.final_value ?? 0).toLocaleString()}`, tone: "neutral" },
    { label: "Median CAGR", value: `${(p50.cagr_pct ?? 0).toFixed(1)}%`, tone: "good" },
    { label: "P5 final", value: `$${Math.round(p5.final_value ?? 0).toLocaleString()}`, tone: "neutral" },
    { label: "P95 final", value: `$${Math.round(p95.final_value ?? 0).toLocaleString()}`, tone: "good" },
    { label: "P(double)", value: `${(pDouble * 100).toFixed(1)}%`, tone: "good" },
    { label: "P(5x)", value: `${(p5x * 100).toFixed(1)}%`, tone: pDouble > 0.5 ? "good" : "neutral" },
    { label: "P(lose)", value: `${(pLose * 100).toFixed(1)}%`, tone: pLose > 0.1 ? "bad" : "good" },
    { label: "Med max-DD", value: `${ddMedian.toFixed(1)}%`, tone: ddMedian < -20 ? "bad" : "neutral" },
  ];
  const toneColor = (t?: string) => t === "good" ? "#1fc16b" : t === "bad" ? "#ef4444" : "var(--text-dim)";

  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
        Monte Carlo — {mc.n_sims.toLocaleString()} sims · {mc.years}y · ${mc.initial.toLocaleString()} start
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
        {chips.map((c) => (
          <div key={c.label} style={{
            padding: "6px 10px",
            border: "1px solid var(--border)",
            borderRadius: 6,
            background: "var(--surface-1, #0b1220)",
          }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{c.label}</div>
            <div style={{ fontSize: 15, fontWeight: 700, fontFamily: "ui-monospace, monospace", color: toneColor(c.tone) }}>{c.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── plotly figure builders ─────────────────────────────────────────────────

const PALETTE = {
  strategy:  "#06A77D",
  oos:       "#FFB703",
  benchmark: "#A23B72",
  red:       "#E63946",
  blue:      "#2E86AB",
  band:      "rgba(6,167,125,0.18)",
};

function buildBacktestFigure(a: EquityPipelineEnvelope["artifact"]) {
  const { equity, oos_equity, spy_equity, drawdown, spy_drawdown,
          sleeve_cumulative, gross_exposure } = a.charts;
  const traces: Array<Record<string, unknown>> = [];
  const xy = (rows: Array<{ date: string; value: number }>) => ({
    x: rows.map((r) => r.date),
    y: rows.map((r) => r.value),
  });

  // Row 1 — equity curves
  traces.push({ ...xy(equity), type: "scatter", mode: "lines", name: "In-sample",
                line: { color: PALETTE.strategy, width: 2.5 }, xaxis: "x", yaxis: "y" });
  if (oos_equity.length) {
    traces.push({ ...xy(oos_equity), type: "scatter", mode: "lines", name: "Walk-fwd OOS",
                  line: { color: PALETTE.oos, width: 2, dash: "dot" }, xaxis: "x", yaxis: "y" });
  }
  traces.push({ ...xy(spy_equity), type: "scatter", mode: "lines", name: "SPY B&H",
                line: { color: PALETTE.benchmark, width: 1.5, dash: "dash" }, xaxis: "x", yaxis: "y" });

  // Row 2 — drawdown
  traces.push({ ...xy(drawdown), type: "scatter", mode: "lines", name: "Strategy DD",
                fill: "tozeroy", line: { color: PALETTE.strategy }, showlegend: false,
                xaxis: "x2", yaxis: "y2" });
  traces.push({ ...xy(spy_drawdown), type: "scatter", mode: "lines", name: "SPY DD",
                line: { color: PALETTE.benchmark, dash: "dash", width: 1 }, showlegend: false,
                xaxis: "x2", yaxis: "y2" });

  // Row 3 — sleeve cumulative returns
  const colors = [PALETTE.blue, PALETTE.red, PALETTE.oos, PALETTE.strategy];
  Object.entries(sleeve_cumulative).forEach(([name, rows], i) => {
    traces.push({ ...xy(rows), type: "scatter", mode: "lines", name,
                  line: { color: colors[i % colors.length], width: 1.5 },
                  xaxis: "x3", yaxis: "y3" });
  });

  // Row 4 — gross exposure
  traces.push({ ...xy(gross_exposure), type: "scatter", mode: "lines", name: "Gross %",
                fill: "tozeroy", line: { color: PALETTE.strategy, width: 1 }, showlegend: false,
                xaxis: "x4", yaxis: "y4" });

  return {
    data: traces,
    layout: {
      grid: { rows: 4, columns: 1, pattern: "independent" },
      template: "plotly_dark",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      hovermode: "x unified",
      height: 700,
      legend: { orientation: "h", y: 1.04, x: 1, xanchor: "right" },
      margin: { l: 60, r: 20, t: 50, b: 40 },
      xaxis:  { domain: [0, 1], anchor: "y",  showticklabels: false, matches: "x4" },
      yaxis:  { domain: [0.60, 1.00], title: "Equity ($)", type: "log" },
      xaxis2: { domain: [0, 1], anchor: "y2", showticklabels: false, matches: "x4" },
      yaxis2: { domain: [0.42, 0.58], title: "DD (%)" },
      xaxis3: { domain: [0, 1], anchor: "y3", showticklabels: false, matches: "x4" },
      yaxis3: { domain: [0.22, 0.38], title: "Cum (×)", type: "log" },
      xaxis4: { domain: [0, 1], anchor: "y4" },
      yaxis4: { domain: [0.00, 0.18], title: "Gross %" },
    },
    config: { responsive: true, displaylogo: false },
  };
}

function buildMonteCarloFigure(mc: NonNullable<EquityPipelineEnvelope["artifact"]["monte_carlo"]>) {
  const { years_axis, q05, q25, q50, q75, q95 } = mc.fan_chart;
  // Stacked bands: outer (q05/q95) lightest, inner (q25/q75) medium, median solid.
  const traces: Array<Record<string, unknown>> = [
    { x: years_axis, y: q95, type: "scatter", mode: "lines", name: "P95",
      line: { color: PALETTE.strategy, width: 0 }, showlegend: false },
    { x: years_axis, y: q05, type: "scatter", mode: "lines", name: "P5",
      fill: "tonexty", fillcolor: "rgba(6,167,125,0.10)",
      line: { color: PALETTE.strategy, width: 0 } },
    { x: years_axis, y: q75, type: "scatter", mode: "lines", name: "P75",
      line: { color: PALETTE.strategy, width: 0 }, showlegend: false },
    { x: years_axis, y: q25, type: "scatter", mode: "lines", name: "P25",
      fill: "tonexty", fillcolor: "rgba(6,167,125,0.22)",
      line: { color: PALETTE.strategy, width: 0 } },
    { x: years_axis, y: q50, type: "scatter", mode: "lines", name: "Median",
      line: { color: PALETTE.strategy, width: 2.5 } },
  ];
  return {
    data: traces,
    layout: {
      template: "plotly_dark",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      title: `Monte Carlo projection — ${mc.n_sims.toLocaleString()} bootstrap paths`,
      xaxis: { title: "Years from now" },
      yaxis: { title: `Portfolio value ($, starting $${mc.initial.toLocaleString()})`, type: "log" },
      hovermode: "x unified",
      height: 450,
      margin: { l: 80, r: 20, t: 50, b: 50 },
      legend: { orientation: "h", y: 1.04, x: 1, xanchor: "right" },
    },
    config: { responsive: true, displaylogo: false },
  };
}
