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
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

type Strategy = {
  name: string;
  class: string;
  summary: string;
  default_params: Record<string, unknown>;
};

type Session = {
  requestId: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  claimedBy: string | null;
  enqueuedAtUtc: string;
  claimedAtUtc: string | null;
  completedAtUtc: string | null;
  resultSummary: Record<string, unknown> | null;
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
  // Per-strategy lookback (days). ichimoku_fx_mr defaults to 200 because
  // its signal early-returns 0 until n >= max(horizons)*4 + max(smooths) + 5
  // = 2573 bars — at ~13 trading-day 1h-bars after weekend gaps, 200
  // calendar days produces ~2600+ bars which clears the gate. Other
  // strategies don't need any lookback. The user can override per row.
  const lookbackDefault = (name: string) => (name === "ichimoku_fx_mr" ? 200 : 0);
  const [lookbackFor, setLookbackFor] = useState<Record<string, number>>({});

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
        const [s, q] = await Promise.all([
          api.paperStrategies(),
          api.opsSessions(undefined, 30),
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
  }, []);

  // Auto-refresh sessions every 5s while any row is non-terminal.
  useEffect(() => {
    const hasActive = sessions.some(s =>
      s.status === "Pending" || s.status === "Claimed");
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
    const lookback_days = lookbackFor[strategy.name] ?? lookbackDefault(strategy.name);
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
              <Th>Summary</Th>
              <Th>Symbol</Th>
              <Th>Session date</Th>
              <Th>Lookback (days)</Th>
              <Th>Trigger</Th>
            </tr>
          </thead>
          <tbody>
            {strategies.map(s => (
              <tr key={s.name}>
                <Td>
                  <div style={{ fontWeight: 600 }}>{s.name}</div>
                  <div style={smallMuted}>{s.class}</div>
                </Td>
                <Td>
                  <div style={{ fontSize: 13 }}>{s.summary}</div>
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
                    value={lookbackFor[s.name] ?? lookbackDefault(s.name)}
                    onChange={e =>
                      setLookbackFor({
                        ...lookbackFor,
                        [s.name]: Math.max(0, Number(e.target.value) || 0),
                      })
                    }
                    style={{ ...inputStyle, width: 80 }}
                    title="Extend bar fetch backwards from session date. ichimoku_fx_mr needs ~200 days to clear its 2573-bar gate; intraday strategies leave at 0. First run is slow; subsequent runs hit the parquet cache."
                  />
                </Td>
                <Td>
                  <button
                    onClick={() => runStrategy(s)}
                    disabled={busyName === s.name}
                    style={runButtonStyle(busyName === s.name)}
                  >
                    {busyName === s.name ? "Queueing…" : "Run intraday"}
                  </button>
                </Td>
              </tr>
            ))}
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
                const payload = sess.payload || {};
                const strategy = (payload as Record<string, string>).strategy
                  || "—";
                const symbols = (payload as Record<string, string[]>).symbols
                  ?.join(",") || "—";
                return (
                  <tr key={sess.requestId}>
                    <Td>
                      <span style={statusStyle(sess.status)}>{sess.status}</span>
                    </Td>
                    <Td>
                      <div style={{ fontWeight: 600 }}>{strategy}</div>
                      <div style={smallMuted}>{symbols}</div>
                    </Td>
                    <Td>
                      <div style={smallMuted}>
                        {new Date(sess.enqueuedAtUtc).toLocaleTimeString()}
                      </div>
                    </Td>
                    <Td>{sess.claimedBy || "—"}</Td>
                    <Td>
                      {sess.resultSummary
                        ? <code style={codeStyle}>
                            {JSON.stringify(sess.resultSummary).slice(0, 120)}
                          </code>
                        : "—"}
                    </Td>
                    <Td>
                      {sess.status === "Pending" && (
                        <button
                          onClick={() => cancel(sess.requestId)}
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
