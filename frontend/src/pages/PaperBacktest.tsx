import { useEffect, useState } from "react";
import { api } from "../api/client";

// Paper-trading backtest dashboard. List of reports the Mac has
// pushed (single-strategy walk-forward OR multi-strategy comparator).
// Click a report to see the per-entry scoreboard + per-session equity
// curve. The report payload is pre-shaped on the Mac side — see
// validator.to_summary() / comparator.to_summary() — so this page
// is a thin renderer with no business logic.

type ReportSummary = {
  reportId: string;
  kind: string;
  symbol: string;
  start?: string;
  end?: string;
  entryCount: number;
  receivedAtUtc: string;
};

type ComparatorEntry = {
  strategy_id: string;
  label: string;
  symbol: string;
  session_count: number;
  total_realised_pnl: number;
  total_fills: number;
  total_commission: number;
  avg_session_pnl: number;
  stdev_session_pnl: number;
  win_session_pct: number;
  sharpe_per_session: number;
  max_drawdown: number;
  best_session?: { date: string; realised_pnl: number };
  worst_session?: { date: string; realised_pnl: number };
  equity_curve: Array<[string, number]>;
};

type ComparatorPayload = {
  symbol: string;
  start: string;
  end: string;
  entries: ComparatorEntry[];
  rankings: {
    by_total_pnl: string[];
    by_sharpe: string[];
    by_drawdown: string[];
  };
};

type BacktestPayload = ComparatorEntry & {
  kind: string;
  report_id: string;
  // Single-backtest payload is one strategy's WalkForwardResult.to_summary();
  // we normalise it into a one-entry comparator shape so the renderer
  // doesn't fork on payload type.
};

function isComparatorPayload(p: unknown): p is ComparatorPayload {
  return !!p && typeof p === "object" && Array.isArray((p as ComparatorPayload).entries);
}

function normalisePayload(p: unknown): ComparatorPayload {
  if (isComparatorPayload(p)) return p;
  const single = p as BacktestPayload;
  return {
    symbol: single.symbol,
    start: single.equity_curve?.[0]?.[0] ?? "",
    end: single.equity_curve?.[single.equity_curve.length - 1]?.[0] ?? "",
    entries: [{ ...single, label: single.strategy_id }],
    rankings: {
      by_total_pnl: [single.strategy_id],
      by_sharpe: [single.strategy_id],
      by_drawdown: [single.strategy_id],
    },
  };
}

export function PaperBacktest() {
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [payload, setPayload] = useState<ComparatorPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    api
      .paperBacktestReports()
      .then(setReports)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setPayload(null);
      return;
    }
    setLoadingDetail(true);
    api
      .paperBacktestReport(selectedId)
      .then((p) => setPayload(normalisePayload(p)))
      .catch((e) => setError(String(e)))
      .finally(() => setLoadingDetail(false));
  }, [selectedId]);

  return (
    <div>
      <h2 style={{ margin: "0 0 4px" }}>Backtest</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginTop: 0 }}>
        Paper-trading walk-forward + multi-strategy comparator results pushed
        from the Mac (<code>tradepro-paper-compare --push</code>,
        <code> tradepro-paper-backtest --push</code>).
      </p>
      {error && (
        <div
          style={{
            padding: "10px 14px",
            margin: "8px 0",
            border: "1px solid var(--down)",
            background: "var(--down-soft)",
            color: "var(--down)",
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 1fr) 2fr", gap: 16, marginTop: 16 }}>
        <ReportList
          reports={reports}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <ReportDetail loading={loadingDetail} payload={payload} />
      </div>
    </div>
  );
}

function ReportList(props: {
  reports: ReportSummary[] | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { reports, selectedId, onSelect } = props;
  if (reports === null) return <div style={{ color: "var(--text-muted)" }}>Loading reports…</div>;
  if (reports.length === 0) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        No reports yet. Run a backtest from the Mac:
        <pre
          style={{
            marginTop: 8,
            padding: 8,
            background: "var(--bg-elev)",
            borderRadius: 6,
            fontSize: 12,
            overflowX: "auto",
          }}
        >
{`uv run tradepro-paper-compare --symbol AAPL \\
  --from 2026-04-01 --to 2026-04-30 \\
  --entry "ORB-15::orb?range_minutes=15" \\
  --entry "ORB-30::orb?range_minutes=30" \\
  --push`}
        </pre>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {reports.map((r) => {
        const active = r.reportId === selectedId;
        return (
          <button
            key={r.reportId}
            onClick={() => onSelect(r.reportId)}
            style={{
              textAlign: "left",
              padding: "10px 12px",
              border: `1px solid ${active ? "var(--up)" : "var(--border)"}`,
              background: active ? "var(--bg-hover)" : "transparent",
              borderRadius: 8,
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <strong style={{ fontSize: 13 }}>{r.symbol}</strong>
              <span style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>{r.kind}</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
              {r.start && r.end ? `${r.start} → ${r.end}` : "single session"}
              {" · "}
              {r.entryCount} {r.entryCount === 1 ? "entry" : "entries"}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              received {new Date(r.receivedAtUtc).toLocaleString()}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ReportDetail(props: { loading: boolean; payload: ComparatorPayload | null }) {
  if (props.loading) return <div style={{ color: "var(--text-muted)" }}>Loading details…</div>;
  if (!props.payload) return <div style={{ color: "var(--text-muted)" }}>Pick a report on the left.</div>;
  const p = props.payload;
  const winnerId = p.rankings.by_total_pnl[0];
  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <strong style={{ fontSize: 14 }}>{p.symbol}</strong>
        <span style={{ color: "var(--text-dim)", fontSize: 12, marginLeft: 8 }}>
          {p.start} → {p.end}
        </span>
      </div>
      <table
        className="num"
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}
      >
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-dim)" }}>
            <th style={{ textAlign: "left", padding: "6px 8px" }}>Strategy</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Total P&amp;L</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Win %</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Sharpe/sess</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Max DD</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Sessions</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Fills</th>
          </tr>
        </thead>
        <tbody>
          {p.entries.map((e) => {
            const isWinner = e.strategy_id === winnerId;
            return (
              <tr
                key={e.strategy_id}
                style={{
                  borderBottom: "1px solid var(--border-soft)",
                  background: isWinner ? "var(--up-soft)" : "transparent",
                }}
              >
                <td style={{ padding: "6px 8px" }}>
                  {isWinner && <span style={{ marginRight: 6, color: "var(--up)" }}>★</span>}
                  {e.label}
                </td>
                <td style={{ textAlign: "right", padding: "6px 8px", color: e.total_realised_pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                  {e.total_realised_pnl.toFixed(2)}
                </td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{(e.win_session_pct * 100).toFixed(1)}%</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{e.sharpe_per_session.toFixed(2)}</td>
                <td style={{ textAlign: "right", padding: "6px 8px", color: "var(--down)" }}>{e.max_drawdown.toFixed(2)}</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{e.session_count}</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{e.total_fills}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: 24 }}>
        <h4 style={{ margin: "0 0 8px" }}>Equity curves</h4>
        <EquityChart entries={p.entries} />
      </div>
    </div>
  );
}

function EquityChart(props: { entries: ComparatorEntry[] }) {
  // Inline SVG sparkline-style chart. Sized to the parent column.
  // No charting library required — keeps the bundle thin and the
  // first-paint instant. Switch to a real chart lib if/when overlays
  // (drawdown shading, trade markers) become needed.
  const width = 640;
  const height = 220;
  const pad = { top: 8, right: 12, bottom: 24, left: 48 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  if (props.entries.length === 0) return null;

  // Build a unified date axis across all entries (typically identical
  // since they were run on the same range, but guard for partial data).
  const dates = Array.from(
    new Set(props.entries.flatMap((e) => e.equity_curve.map(([d]) => d))),
  ).sort();
  if (dates.length < 2) {
    return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Need ≥2 sessions to draw a curve.</div>;
  }
  const dateIndex = new Map(dates.map((d, i) => [d, i] as const));

  const allY = props.entries.flatMap((e) => e.equity_curve.map(([, v]) => v)).concat([0]);
  const yMin = Math.min(...allY);
  const yMax = Math.max(...allY);
  const ySpan = yMax - yMin || 1;

  const xFor = (d: string) => pad.left + (dateIndex.get(d)! / (dates.length - 1)) * innerW;
  const yFor = (v: number) => pad.top + innerH - ((v - yMin) / ySpan) * innerH;

  const colours = ["#4f8cff", "#1fc16b", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4"];

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ background: "var(--bg-elev)", borderRadius: 8 }}>
      {/* Y zero line */}
      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={yFor(0)}
        y2={yFor(0)}
        stroke="var(--border)"
        strokeDasharray="3 3"
      />
      {/* Y axis labels: min / 0 / max */}
      {[yMin, 0, yMax].map((v) => (
        <text
          key={v}
          x={pad.left - 6}
          y={yFor(v)}
          textAnchor="end"
          alignmentBaseline="middle"
          fontSize="10"
          fill="var(--text-muted)"
        >
          {v.toFixed(0)}
        </text>
      ))}
      {/* X axis labels: first / last */}
      <text x={pad.left} y={height - 6} fontSize="10" fill="var(--text-muted)">{dates[0]}</text>
      <text x={width - pad.right} y={height - 6} fontSize="10" textAnchor="end" fill="var(--text-muted)">
        {dates[dates.length - 1]}
      </text>
      {/* One polyline per entry */}
      {props.entries.map((e, idx) => {
        const colour = colours[idx % colours.length];
        const points = e.equity_curve
          .map(([d, v]) => `${xFor(d).toFixed(1)},${yFor(v).toFixed(1)}`)
          .join(" ");
        return (
          <g key={e.strategy_id}>
            <polyline
              fill="none"
              stroke={colour}
              strokeWidth="2"
              points={points}
            />
            {/* Label at the end of the line */}
            <text
              x={width - pad.right + 2}
              y={yFor(e.equity_curve[e.equity_curve.length - 1]?.[1] ?? 0)}
              fontSize="10"
              fill={colour}
              alignmentBaseline="middle"
            >
              {e.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
