/**
 * Strategies page — UI-driven catalogue + ad-hoc runner.
 *
 * Two panels:
 *
 *   1. Registered strategies (from /api/paper/strategies/) — the seven
 *      Layer-1 daily signal strategies + the four Layer-2 intraday
 *      paper strategies + Lane A's new quant_engine strategies
 *      (ichimoku_equity, ichimoku_fx_mr). Each row shows the strategy
 *      summary + default params + a "Run intraday" button that
 *      enqueues a session_request via /api/ops/run-intraday. The Mac
 *      worker claims, runs, posts status back.
 *
 *   2. Sessions queue (from /api/ops/sessions) — recent runs with
 *      status (Pending / Claimed / Completed / Failed / Cancelled),
 *      result summary when present, and a Cancel button for Pending
 *      rows. Auto-refreshes every 5s while any row is non-terminal.
 *
 * Closes the trader's loop: "trigger the quant strategy from the
 * browser, see it run, see the output" — no Mac CLI required.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

type Strategy = {
  name: string;
  class: string;
  summary: string;
  source?: string;                 // "trader-quant" | "alpha-engine" | "scaffold"
  status?: string;                 // code default
  default_lookback_days?: number;
  default_params: Record<string, unknown>;
};

type StatusOverride = {
  StrategyId: string;
  Status: string;
  UpdatedAtUtc: string;
  UpdatedBy: string;
};

// Promotion lifecycle — keep ordered so "promote" cycles forward.
const STATUS_FLOW = ["evaluating", "backtest-ok", "scheduled", "live-eligible"] as const;
type LifecycleStatus = (typeof STATUS_FLOW)[number];

function nextStatus(current: string): LifecycleStatus | null {
  const idx = (STATUS_FLOW as readonly string[]).indexOf(current);
  if (idx === -1 || idx === STATUS_FLOW.length - 1) return null;
  return STATUS_FLOW[idx + 1];
}

function sourceBadge(source: string | undefined): { label: string; bg: string; fg: string } {
  switch (source) {
    case "trader-quant":
      return { label: "TRADER", bg: "rgba(31,193,107,0.12)", fg: "#1fc16b" };
    case "alpha-engine":
      return { label: "ALPHA", bg: "rgba(79,140,255,0.12)", fg: "#4f8cff" };
    default:
      return { label: "SCAFFOLD", bg: "rgba(107,114,128,0.12)", fg: "#9ca3af" };
  }
}

function statusBadge(status: string): { bg: string; fg: string } {
  switch (status) {
    case "live-eligible":
      return { bg: "rgba(31,193,107,0.18)", fg: "#1fc16b" };
    case "scheduled":
      return { bg: "rgba(79,140,255,0.15)", fg: "#4f8cff" };
    case "backtest-ok":
      return { bg: "rgba(217,119,6,0.15)", fg: "#d97706" };
    default: // evaluating
      return { bg: "rgba(107,114,128,0.15)", fg: "#9ca3af" };
  }
}

// Mirrors api.opsSessions wire shape — snake_case to match the
// .NET Envelope() in OpsEndpoints.cs. Was previously camelCase here,
// which silently meant every read was `undefined` and the strategies
// activity table never rendered.
type Session = {
  request_id: string;
  kind: string;
  state: string;
  params: Record<string, unknown>;
  claimed_by: string | null;
  requested_at_utc: string;
  claimed_at_utc: string | null;
  completed_at_utc: string | null;
  result_summary: Record<string, unknown> | null;
  error: string | null;
};

export function Strategies() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [symbolFor, setSymbolFor] = useState<Record<string, string>>({});
  // Session date per strategy. Missing entries fall back to today —
  // lets the trader trigger "what would equity have done last Friday"
  // on weekends/holidays when today's data is empty.
  const todayIso = new Date().toISOString().slice(0, 10);
  const [dateFor, setDateFor] = useState<Record<string, string>>({});
  // Per-strategy lookback (days). Default comes from the catalog's
  // `default_lookback_days` (declared on the strategy ClassVar) so a
  // new trader-shipped strategy with N-day warmup just works. User can
  // override per row before triggering.
  const [lookbackFor, setLookbackFor] = useState<Record<string, number>>({});
  // Runtime status overrides keyed by strategy_id. Merge with catalog
  // status: override wins when set, otherwise catalog default.
  const [statusOverrides, setStatusOverrides] = useState<Record<string, StatusOverride>>({});
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [promotingName, setPromotingName] = useState<string | null>(null);

  const effectiveStatus = (s: Strategy): string =>
    statusOverrides[s.name]?.Status || s.status || "evaluating";

  const loadStatusOverrides = useCallback(async () => {
    try {
      const { overrides } = await api.strategyStatusOverrides();
      const map: Record<string, StatusOverride> = {};
      for (const o of overrides) map[o.StrategyId] = o;
      setStatusOverrides(map);
    } catch (e) {
      // Non-fatal: page still works without overrides (catalog defaults
      // are used). Log so a stale deploy is visible.
      console.warn("strategyStatusOverrides failed:", e);
    }
  }, []);

  const promote = async (s: Strategy) => {
    const target = nextStatus(effectiveStatus(s));
    if (!target) return;
    setPromotingName(s.name);
    setError(null);
    try {
      await api.setStrategyStatus(s.name, target);
      await loadStatusOverrides();
    } catch (e) {
      setError(String(e));
    } finally {
      setPromotingName(null);
    }
  };

  const resetStatus = async (s: Strategy) => {
    setPromotingName(s.name);
    setError(null);
    try {
      await api.clearStrategyStatus(s.name);
      await loadStatusOverrides();
    } catch (e) {
      setError(String(e));
    } finally {
      setPromotingName(null);
    }
  };

  const loadSessions = useCallback(async () => {
    try {
      const { sessions: rows } = await api.opsSessions(undefined, 30);
      setSessions(rows);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        // Load catalog + sessions in parallel; status overrides are
        // best-effort (loadStatusOverrides swallows its own errors so
        // a stale deploy doesn't blank the page).
        const [s, q] = await Promise.all([
          api.paperStrategies(),
          api.opsSessions(undefined, 30),
          loadStatusOverrides(),
        ]);
        if (cancelled) return;
        setStrategies(s.strategies);
        setSessions(q.sessions);
        setLoading(false);
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [loadStatusOverrides]);

  // Auto-refresh sessions every 5s while any row is non-terminal.
  useEffect(() => {
    const hasActive = sessions.some(s =>
      s.state === "Pending" || s.state === "Claimed");
    if (!hasActive) return;
    const t = setInterval(() => { void loadSessions(); }, 5000);
    return () => clearInterval(t);
  }, [sessions, loadSessions]);

  const runStrategy = async (strategy: Strategy) => {
    const symbol = (symbolFor[strategy.name] || "").trim().toUpperCase();
    if (!symbol) {
      setError(`enter a symbol for ${strategy.name} before triggering`);
      return;
    }
    const session_date = dateFor[strategy.name] || todayIso;
    const lookback_days =
      lookbackFor[strategy.name] ?? strategy.default_lookback_days ?? 0;
    setBusyName(strategy.name);
    setError(null);
    try {
      await api.runIntraday({
        strategy: strategy.name,
        symbols: [symbol],
        session_date,
        lookback_days,
        params: strategy.default_params,
      });
      await loadSessions();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyName(null);
    }
  };

  const cancel = async (requestId: string) => {
    try {
      await api.cancelOpsSession(requestId);
      await loadSessions();
    } catch (e) {
      setError(String(e));
    }
  };

  if (loading) {
    return <div style={page}>Loading strategies and queue…</div>;
  }

  return (
    <div style={page}>
      <header style={pageHeader}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Strategies</h1>
        <p style={subhead}>
          Trigger a strategy run from the browser. Sessions queue
          tracks status as the Mac worker claims and runs the request.
        </p>
      </header>

      {error && <div style={errorBox}>{error}</div>}

      <section style={cardStyle}>
        <h2 style={sectionHead}>Registered ({strategies.length})</h2>
        <table style={tableStyle}>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Status</Th>
              <Th>Symbol</Th>
              <Th>Session date</Th>
              <Th>Lookback</Th>
              <Th>Actions</Th>
            </tr>
          </thead>
          <tbody>
            {strategies.map(s => {
              const srcB = sourceBadge(s.source);
              const status = effectiveStatus(s);
              const statB = statusBadge(status);
              const isOverridden = !!statusOverrides[s.name];
              const next = nextStatus(status);
              const isPromoting = promotingName === s.name;
              const expanded = expandedName === s.name;
              return (
                <React.Fragment key={s.name}>
                  <tr>
                    <Td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <button
                          onClick={() => setExpandedName(expanded ? null : s.name)}
                          style={chevronStyle}
                          title={expanded ? "Hide params" : "Show default params"}
                        >
                          {expanded ? "▾" : "▸"}
                        </button>
                        <div>
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <span style={{ fontWeight: 600 }}>{s.name}</span>
                            <span
                              style={{
                                fontSize: 9,
                                fontWeight: 700,
                                letterSpacing: "0.06em",
                                padding: "2px 5px",
                                borderRadius: 4,
                                background: srcB.bg,
                                color: srcB.fg,
                              }}
                              title={`source: ${s.source ?? "scaffold"}`}
                            >
                              {srcB.label}
                            </span>
                          </div>
                          <div style={smallMuted}>{s.summary}</div>
                        </div>
                      </div>
                    </Td>
                    <Td>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 8px",
                            borderRadius: 999,
                            background: statB.bg,
                            color: statB.fg,
                            letterSpacing: "0.03em",
                          }}
                          title={isOverridden ? "operator override" : "code default"}
                        >
                          {status}
                          {isOverridden ? " *" : ""}
                        </span>
                      </div>
                    </Td>
                    <Td>
                      <input
                        type="text"
                        placeholder="AAPL"
                        value={symbolFor[s.name] || ""}
                        onChange={e => setSymbolFor({ ...symbolFor, [s.name]: e.target.value })}
                        style={inputStyle}
                      />
                    </Td>
                    <Td>
                      <input
                        type="date"
                        value={dateFor[s.name] || todayIso}
                        max={todayIso}
                        onChange={e => setDateFor({ ...dateFor, [s.name]: e.target.value })}
                        style={inputStyle}
                        title="Use a past trading day on weekends/holidays to get real bars"
                      />
                    </Td>
                    <Td>
                      <input
                        type="number"
                        min={0}
                        max={365}
                        value={lookbackFor[s.name] ?? s.default_lookback_days ?? 0}
                        onChange={e =>
                          setLookbackFor({
                            ...lookbackFor,
                            [s.name]: Math.max(0, Number(e.target.value) || 0),
                          })
                        }
                        style={{ ...inputStyle, width: 70 }}
                        title="Extend bar fetch backwards from session date. Default comes from strategy.default_lookback_days. First run is slow; subsequent runs hit the parquet cache."
                      />
                    </Td>
                    <Td>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          onClick={() => runStrategy(s)}
                          disabled={busyName === s.name}
                          style={runButtonStyle(busyName === s.name)}
                        >
                          {busyName === s.name ? "Queueing…" : "Run"}
                        </button>
                        {next && (
                          <button
                            onClick={() => promote(s)}
                            disabled={isPromoting}
                            style={promoteButtonStyle(isPromoting)}
                            title={`Promote to ${next}`}
                          >
                            {isPromoting ? "…" : `→ ${next}`}
                          </button>
                        )}
                        {isOverridden && (
                          <button
                            onClick={() => resetStatus(s)}
                            disabled={isPromoting}
                            style={resetButtonStyle}
                            title="Drop the runtime override and fall back to the code default"
                          >
                            reset
                          </button>
                        )}
                      </div>
                    </Td>
                  </tr>
                  {expanded && (
                    <tr style={{ background: "rgba(255,255,255,0.02)" }}>
                      <td colSpan={6} style={{ padding: "10px 14px" }}>
                        <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 6 }}>
                          {s.class} · default_lookback_days={s.default_lookback_days ?? 0}
                          {isOverridden && (
                            <>
                              {" · "}
                              override:{" "}
                              <span style={{ color: "var(--text-muted)" }}>
                                {statusOverrides[s.name]?.UpdatedBy} at{" "}
                                {new Date(statusOverrides[s.name]?.UpdatedAtUtc).toLocaleString()}
                              </span>
                            </>
                          )}
                        </div>
                        <pre
                          style={{
                            margin: 0,
                            padding: 10,
                            background: "var(--bg-hover, rgba(255,255,255,0.03))",
                            border: "1px solid var(--border)",
                            borderRadius: 6,
                            fontSize: 11,
                            color: "var(--text-dim)",
                            overflowX: "auto",
                          }}
                        >
                          {JSON.stringify(s.default_params, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </section>

      <section style={cardStyle}>
        <h2 style={sectionHead}>Sessions ({sessions.length})</h2>
        {sessions.length === 0 && (
          <div style={smallMuted}>No sessions yet. Trigger one above.</div>
        )}
        {sessions.length > 0 && (
          <table style={tableStyle}>
            <thead>
              <tr>
                <Th>Status</Th>
                <Th>Strategy / Symbol</Th>
                <Th>Enqueued</Th>
                <Th>Claimed by</Th>
                <Th>Result</Th>
                <Th>{" "}</Th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(sess => {
                const params = sess.params || {};
                const strategy = (params as Record<string, string>).strategy
                  || "—";
                const symbols = (params as Record<string, string[]>).symbols
                  ?.join(",") || "—";
                return (
                  <tr key={sess.request_id}>
                    <Td>
                      <span style={statusStyle(sess.state)}>{sess.state}</span>
                    </Td>
                    <Td>
                      <div style={{ fontWeight: 600 }}>{strategy}</div>
                      <div style={smallMuted}>{symbols}</div>
                    </Td>
                    <Td>
                      <div style={smallMuted}>
                        {new Date(sess.requested_at_utc).toLocaleTimeString()}
                      </div>
                    </Td>
                    <Td>{sess.claimed_by || "—"}</Td>
                    <Td>
                      {sess.result_summary
                        ? <code style={codeStyle}>
                            {JSON.stringify(sess.result_summary).slice(0, 120)}
                          </code>
                        : "—"}
                    </Td>
                    <Td>
                      {sess.state === "Pending" && (
                        <button
                          onClick={() => cancel(sess.request_id)}
                          style={cancelButtonStyle}
                        >
                          Cancel
                        </button>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

const Th = ({ children }: { children?: React.ReactNode }) =>
  <th style={{
    textAlign: "left",
    padding: "8px 10px",
    fontSize: 11,
    textTransform: "uppercase",
    color: "var(--text-muted)",
    letterSpacing: 0.5,
    fontWeight: 600,
    borderBottom: "1px solid var(--border)",
  }}>{children}</th>;

const Td = ({ children }: { children: React.ReactNode }) =>
  <td style={{
    padding: "10px",
    fontSize: 13,
    borderBottom: "1px solid var(--border)",
    verticalAlign: "top",
  }}>{children}</td>;

function statusStyle(status: string): React.CSSProperties {
  const colour =
    status === "Completed" ? "#1f9e6e" :
    status === "Pending"   ? "#a07e1c" :
    status === "Claimed"   ? "#3b82a4" :
    status === "Failed"    ? "#a83a3a" :
    status === "Cancelled" ? "#888"    :
                             "#666";
  return {
    background: colour,
    color: "#fff",
    padding: "3px 9px",
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: 0.4,
  };
}

const page: React.CSSProperties = {
  maxWidth: 1100,
  margin: "32px auto",
  padding: "0 20px",
  display: "flex",
  flexDirection: "column",
  gap: 18,
};
const pageHeader: React.CSSProperties = { marginBottom: 4 };
const subhead: React.CSSProperties = {
  margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13,
};
const cardStyle: React.CSSProperties = {
  background: "var(--surface-1, #fff)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: 16,
};
const sectionHead: React.CSSProperties = {
  margin: "0 0 12px", fontSize: 14, fontWeight: 700,
  textTransform: "uppercase", letterSpacing: 0.7,
  color: "var(--text-muted)",
};
const tableStyle: React.CSSProperties = {
  width: "100%", borderCollapse: "collapse", fontSize: 13,
};
const smallMuted: React.CSSProperties = {
  fontSize: 11, color: "var(--text-muted)",
};
const inputStyle: React.CSSProperties = {
  width: 90, padding: "4px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "var(--surface-2, #f8f8f8)",
};
const codeStyle: React.CSSProperties = {
  fontSize: 11, fontFamily: "var(--font-mono, monospace)",
  background: "var(--surface-3, #efefef)",
  padding: "2px 5px", borderRadius: 3,
};
const errorBox: React.CSSProperties = {
  padding: 10, border: "1px solid #a83a3a", borderRadius: 6,
  background: "#fdecec", color: "#7a1a1a", fontSize: 13,
};
function runButtonStyle(busy: boolean): React.CSSProperties {
  return {
    padding: "5px 11px",
    fontSize: 12,
    fontWeight: 600,
    background: busy ? "#888" : "#3b82a4",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    cursor: busy ? "wait" : "pointer",
  };
}
const cancelButtonStyle: React.CSSProperties = {
  padding: "4px 9px",
  fontSize: 11,
  background: "transparent",
  color: "#a83a3a",
  border: "1px solid #a83a3a",
  borderRadius: 4,
  cursor: "pointer",
};
const chevronStyle: React.CSSProperties = {
  width: 18,
  height: 18,
  padding: 0,
  fontSize: 11,
  background: "transparent",
  color: "var(--text-muted)",
  border: "1px solid var(--border)",
  borderRadius: 3,
  cursor: "pointer",
  lineHeight: "1",
};
function promoteButtonStyle(busy: boolean): React.CSSProperties {
  return {
    padding: "4px 9px",
    fontSize: 11,
    fontWeight: 500,
    background: busy ? "transparent" : "transparent",
    color: busy ? "var(--text-muted)" : "#1fc16b",
    border: `1px solid ${busy ? "var(--border)" : "#1fc16b"}`,
    borderRadius: 4,
    cursor: busy ? "wait" : "pointer",
    whiteSpace: "nowrap",
  };
}
const resetButtonStyle: React.CSSProperties = {
  padding: "4px 9px",
  fontSize: 11,
  background: "transparent",
  color: "var(--text-muted)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  cursor: "pointer",
};
