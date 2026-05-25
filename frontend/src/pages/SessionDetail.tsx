import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";

// Session Detail page — opens via /paper-live/session/:id from PaperLive.
// Renders the full per-session snapshot as tabbed data frames:
//   Overview · Bars (input) · Decisions (filters) · Fills (output) · Positions
// Each tab has a Download CSV button so investigations can move into
// pandas / a spreadsheet without manual scraping.

type Session = {
  request_id: string;
  kind: string;
  params: unknown;
  state: string;
  requested_at_utc: string;
  claimed_at_utc: string | null;
  claimed_by: string | null;
  completed_at_utc: string | null;
  result_summary: unknown;
  error: string | null;
};

type BarRow = {
  ts: string;
  symbol: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  strategy_id: string;
};

type DecisionRow = {
  bar_ts: string | null;
  symbol: string;
  action: string;
  reason: string;
  detail: Record<string, unknown>;
  strategy_id: string;
};

type FillRow = {
  fill_time: string;
  symbol: string;
  side: string;
  quantity: number;
  fill_price: number;
  commission: number;
  order_id: string;
  strategy_id: string;
};

type PositionRow = {
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  last_mark: number;
  unrealised_pnl: number;
  strategy_id: string;
};

type StrategyEntry = {
  strategy_id?: string;
  equity?: number;
  realised_pnl?: number;
  unrealised_pnl?: number;
  fills_count?: number;
  commission_paid?: number;
  decisions?: Omit<DecisionRow, "strategy_id">[];
  bars_seen?: Omit<BarRow, "strategy_id">[];
  recent_fills?: Omit<FillRow, "strategy_id">[];
  positions?: Omit<PositionRow, "strategy_id">[];
};

const TABS = ["Overview", "Bars", "Decisions", "Fills", "Positions"] as const;
type Tab = (typeof TABS)[number];

function extractStrategies(rs: unknown): StrategyEntry[] {
  if (!rs || typeof rs !== "object") return [];
  const s = (rs as Record<string, unknown>).strategies;
  return Array.isArray(s) ? (s as StrategyEntry[]) : [];
}

function flatten<TIn, TOut>(
  strategies: StrategyEntry[],
  pick: (s: StrategyEntry) => TIn[] | undefined,
  withSid: (row: TIn, sid: string) => TOut,
): TOut[] {
  const out: TOut[] = [];
  for (const s of strategies) {
    const sid = s.strategy_id || "—";
    for (const row of pick(s) ?? []) out.push(withSid(row, sid));
  }
  return out;
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function toCsv(rows: Record<string, unknown>[], headers: string[]): string {
  const head = headers.join(",");
  const body = rows.map((r) => headers.map((h) => csvCell(r[h])).join(",")).join("\n");
  return head + "\n" + body;
}

function downloadCsv(filename: string, csv: string) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function ActionPill({ action }: { action: string }) {
  const colour = action.startsWith("fire-")
    ? "#1fc16b"
    : action.startsWith("skip-")
    ? "var(--text-muted)"
    : "var(--text-dim)";
  const bg = action.startsWith("fire-")
    ? "rgba(31,193,107,0.12)"
    : "transparent";
  return (
    <span
      style={{
        padding: "2px 6px",
        borderRadius: 4,
        background: bg,
        color: colour,
        fontFamily: "monospace",
        fontSize: 11,
      }}
    >
      {action}
    </span>
  );
}

const td: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: 12,
  borderTop: "1px solid var(--border)",
};
const th: React.CSSProperties = {
  padding: "8px 10px",
  fontSize: 11,
  color: "var(--text-dim)",
  textAlign: "left",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-hover, rgba(255,255,255,0.03))",
};

export function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api
      .getOpsSession(id)
      .then((s) => {
        if (!cancelled) setSession(s);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const strategies = useMemo(
    () => extractStrategies(session?.result_summary),
    [session],
  );

  const bars: BarRow[] = useMemo(
    () =>
      flatten<Omit<BarRow, "strategy_id">, BarRow>(
        strategies,
        (s) => s.bars_seen,
        (b, sid) => ({ ...b, strategy_id: sid }),
      ),
    [strategies],
  );

  const decisions: DecisionRow[] = useMemo(
    () =>
      flatten<Omit<DecisionRow, "strategy_id">, DecisionRow>(
        strategies,
        (s) => s.decisions,
        (d, sid) => ({ ...d, strategy_id: sid }),
      ),
    [strategies],
  );

  const fills: FillRow[] = useMemo(
    () =>
      flatten<Omit<FillRow, "strategy_id">, FillRow>(
        strategies,
        (s) => s.recent_fills,
        (f, sid) => ({ ...f, strategy_id: sid }),
      ),
    [strategies],
  );

  const positions: PositionRow[] = useMemo(
    () =>
      flatten<Omit<PositionRow, "strategy_id">, PositionRow>(
        strategies,
        (s) => s.positions,
        (p, sid) => ({ ...p, strategy_id: sid }),
      ),
    [strategies],
  );

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Link to="/paper-live" style={{ color: "var(--text-dim)", fontSize: 12 }}>
          ← Back to sessions
        </Link>
        <div style={{ marginTop: 16, color: "var(--down)" }}>
          Failed to load session: {error}
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <div style={{ padding: 24, color: "var(--text-dim)" }}>Loading session…</div>
    );
  }

  const counts: Record<Tab, number> = {
    Overview: strategies.length,
    Bars: bars.length,
    Decisions: decisions.length,
    Fills: fills.length,
    Positions: positions.length,
  };

  return (
    <div style={{ padding: 24 }}>
      <Link
        to="/paper-live"
        style={{ color: "var(--text-dim)", fontSize: 12, textDecoration: "none" }}
      >
        ← Back to sessions
      </Link>

      <h1 style={{ margin: "8px 0 4px", fontSize: 20 }}>
        Session{" "}
        <span style={{ fontFamily: "monospace", color: "var(--text-dim)" }}>
          {session.request_id.slice(0, 8)}
        </span>
      </h1>
      <div style={{ color: "var(--text-dim)", fontSize: 12, marginBottom: 16 }}>
        kind={session.kind} · state={session.state} · enqueued={new Date(
          session.requested_at_utc,
        ).toLocaleString()}
        {session.completed_at_utc &&
          ` · completed=${new Date(session.completed_at_utc).toLocaleString()}`}
        {session.claimed_by && ` · runner=${session.claimed_by}`}
      </div>
      {session.error && (
        <div
          style={{
            padding: "8px 12px",
            background: "rgba(239,68,68,0.08)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 6,
            color: "var(--down)",
            fontSize: 12,
            marginBottom: 16,
          }}
        >
          {session.error}
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: 4,
          borderBottom: "1px solid var(--border)",
          marginBottom: 12,
        }}
      >
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "8px 14px",
              border: "none",
              background: "transparent",
              borderBottom:
                tab === t ? "2px solid var(--accent, #4f8cff)" : "2px solid transparent",
              color: tab === t ? "var(--text)" : "var(--text-dim)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            {t}{" "}
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
              {counts[t]}
            </span>
          </button>
        ))}
      </div>

      {tab === "Overview" && <OverviewTab session={session} strategies={strategies} />}
      {tab === "Bars" && <BarsTab rows={bars} sessionId={session.request_id} />}
      {tab === "Decisions" && (
        <DecisionsTab rows={decisions} sessionId={session.request_id} />
      )}
      {tab === "Fills" && <FillsTab rows={fills} sessionId={session.request_id} />}
      {tab === "Positions" && (
        <PositionsTab rows={positions} sessionId={session.request_id} />
      )}
    </div>
  );
}

function ExportButton({
  rows,
  headers,
  filename,
}: {
  rows: Record<string, unknown>[];
  headers: string[];
  filename: string;
}) {
  return (
    <button
      onClick={() => downloadCsv(filename, toCsv(rows, headers))}
      disabled={rows.length === 0}
      style={{
        fontSize: 11,
        padding: "4px 10px",
        color: rows.length === 0 ? "var(--text-muted)" : "var(--text-dim)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        background: "transparent",
        cursor: rows.length === 0 ? "not-allowed" : "pointer",
      }}
    >
      Download CSV ({rows.length})
    </button>
  );
}

function ParamsCard({ params }: { params: unknown }) {
  return (
    <pre
      style={{
        padding: 12,
        background: "var(--bg-hover, rgba(255,255,255,0.03))",
        border: "1px solid var(--border)",
        borderRadius: 6,
        fontSize: 11,
        color: "var(--text-dim)",
        overflow: "auto",
        margin: 0,
      }}
    >
      {JSON.stringify(params, null, 2)}
    </pre>
  );
}

function OverviewTab({
  session,
  strategies,
}: {
  session: Session;
  strategies: StrategyEntry[];
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <div>
        <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>Per-strategy summary</h3>
        {strategies.length === 0 ? (
          <div style={{ color: "var(--text-dim)", fontSize: 12 }}>
            No structured strategy data on this session yet.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>Strategy</th>
                <th style={th}>Fills</th>
                <th style={th}>Equity</th>
                <th style={th}>Realised P&L</th>
                <th style={th}>Decisions</th>
                <th style={th}>Bars seen</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s, i) => (
                <tr key={i}>
                  <td style={td}>{s.strategy_id || "—"}</td>
                  <td style={td}>{s.fills_count ?? 0}</td>
                  <td style={td}>{(s.equity ?? 0).toFixed(2)}</td>
                  <td style={td}>{(s.realised_pnl ?? 0).toFixed(2)}</td>
                  <td style={td}>{(s.decisions ?? []).length}</td>
                  <td style={td}>{(s.bars_seen ?? []).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div>
        <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>Session params</h3>
        <ParamsCard params={session.params} />
      </div>
    </div>
  );
}

function BarsTab({ rows, sessionId }: { rows: BarRow[]; sessionId: string }) {
  return (
    <DataTab
      sessionId={sessionId}
      kind="bars"
      empty="No bars captured. Either the daemon predates the bars-seen trace, or the strategy never reached on_bar."
      rows={rows}
      headers={["ts", "strategy_id", "symbol", "open", "high", "low", "close", "volume"]}
      render={(b) => (
        <>
          <td style={{ ...td, fontFamily: "monospace", color: "var(--text-muted)" }}>
            {b.ts.slice(0, 19).replace("T", " ")}
          </td>
          <td style={td}>{b.strategy_id}</td>
          <td style={td}>{b.symbol}</td>
          <td style={td}>{b.open.toFixed(5)}</td>
          <td style={td}>{b.high.toFixed(5)}</td>
          <td style={td}>{b.low.toFixed(5)}</td>
          <td style={td}>{b.close.toFixed(5)}</td>
          <td style={td}>{b.volume}</td>
        </>
      )}
    />
  );
}

function DecisionsTab({ rows, sessionId }: { rows: DecisionRow[]; sessionId: string }) {
  return (
    <DataTab
      sessionId={sessionId}
      kind="decisions"
      empty="No decisions captured. The strategy may predate the decision trace, or never reached on_bar."
      rows={rows}
      headers={["bar_ts", "strategy_id", "symbol", "action", "reason", "detail"]}
      render={(d) => (
        <>
          <td style={{ ...td, fontFamily: "monospace", color: "var(--text-muted)" }}>
            {d.bar_ts ? d.bar_ts.slice(0, 19).replace("T", " ") : "—"}
          </td>
          <td style={td}>{d.strategy_id}</td>
          <td style={td}>{d.symbol}</td>
          <td style={td}>
            <ActionPill action={d.action} />
          </td>
          <td style={td}>{d.reason}</td>
          <td style={{ ...td, fontFamily: "monospace", fontSize: 10, color: "var(--text-muted)" }}>
            {Object.keys(d.detail || {}).length ? JSON.stringify(d.detail) : ""}
          </td>
        </>
      )}
    />
  );
}

function FillsTab({ rows, sessionId }: { rows: FillRow[]; sessionId: string }) {
  return (
    <DataTab
      sessionId={sessionId}
      kind="fills"
      empty="No fills. Either the strategy emitted no orders, or paper_session was run with --push-fills 0."
      rows={rows}
      headers={["fill_time", "strategy_id", "symbol", "side", "quantity", "fill_price", "commission", "order_id"]}
      render={(f) => (
        <>
          <td style={{ ...td, fontFamily: "monospace", color: "var(--text-muted)" }}>
            {f.fill_time.slice(0, 19).replace("T", " ")}
          </td>
          <td style={td}>{f.strategy_id}</td>
          <td style={td}>{f.symbol}</td>
          <td style={{ ...td, color: f.side === "BUY" ? "#1fc16b" : "#ef4444" }}>{f.side}</td>
          <td style={td}>{f.quantity}</td>
          <td style={td}>{f.fill_price.toFixed(5)}</td>
          <td style={td}>{f.commission.toFixed(2)}</td>
          <td style={{ ...td, fontFamily: "monospace", color: "var(--text-muted)", fontSize: 10 }}>
            {f.order_id}
          </td>
        </>
      )}
    />
  );
}

function PositionsTab({ rows, sessionId }: { rows: PositionRow[]; sessionId: string }) {
  return (
    <DataTab
      sessionId={sessionId}
      kind="positions"
      empty="No open positions at session end. Either nothing was held overnight, or the strategy flattens at close."
      rows={rows}
      headers={["strategy_id", "symbol", "quantity", "avg_entry_price", "last_mark", "unrealised_pnl"]}
      render={(p) => (
        <>
          <td style={td}>{p.strategy_id}</td>
          <td style={td}>{p.symbol}</td>
          <td style={{ ...td, color: p.quantity > 0 ? "#1fc16b" : p.quantity < 0 ? "#ef4444" : "var(--text-dim)" }}>
            {p.quantity}
          </td>
          <td style={td}>{p.avg_entry_price.toFixed(5)}</td>
          <td style={td}>{p.last_mark.toFixed(5)}</td>
          <td style={{ ...td, color: p.unrealised_pnl >= 0 ? "#1fc16b" : "#ef4444" }}>
            {p.unrealised_pnl.toFixed(2)}
          </td>
        </>
      )}
    />
  );
}

function DataTab<T extends Record<string, unknown>>({
  sessionId,
  kind,
  rows,
  headers,
  empty,
  render,
}: {
  sessionId: string;
  kind: string;
  rows: T[];
  headers: string[];
  empty: string;
  render: (row: T) => React.ReactNode;
}) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{rows.length} rows</div>
        <ExportButton
          rows={rows as unknown as Record<string, unknown>[]}
          headers={headers}
          filename={`session-${sessionId.slice(0, 8)}-${kind}.csv`}
        />
      </div>
      {rows.length === 0 ? (
        <div style={{ padding: 16, color: "var(--text-dim)", fontSize: 12, fontStyle: "italic" }}>
          {empty}
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {headers.map((h) => (
                  <th key={h} style={th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{render(r)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
