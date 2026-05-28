import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

// Backtests page — trigger a quant-engine backtest via the ops queue
// (.NET enqueues, Mac daemon claims, posts result_summary with the
// trader-anchor charts back). The existing Session Detail page at
// /paper-live/session/:id renders the charts via PlotlyChart, so we
// only own the trigger form + queue list here.
//
// Auto-refreshes the queue every 15 s while there are non-terminal
// rows so the trader sees Pending → Claimed → Completed without
// reloading. The poll loop pauses once everything's terminal to save
// network round trips.

type Backtest = {
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

const DEFAULT_STRATEGY = "ichimoku_equity";
const DEFAULT_SYMBOLS = "AAPL,MSFT,NVDA,GLD";

function relativeTime(isoUtc: string): string {
  const diff = Date.now() - new Date(isoUtc).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function stateBadge(state: string) {
  const lower = state.toLowerCase();
  const colour =
    lower === "pending" ? "#d97706"
    : lower === "claimed" ? "#4f8cff"
    : lower === "completed" ? "#1fc16b"
    : lower === "failed" ? "#ef4444"
    : "#6b7280";
  const bg =
    lower === "pending" ? "rgba(217,119,6,0.12)"
    : lower === "claimed" ? "rgba(79,140,255,0.12)"
    : lower === "completed" ? "rgba(31,193,107,0.12)"
    : lower === "failed" ? "rgba(239,68,68,0.12)"
    : "rgba(107,114,128,0.12)";
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 999,
      fontSize: 11, fontWeight: 600, color: colour, background: bg,
      letterSpacing: "0.04em", textTransform: "uppercase",
    }}>
      {state}
    </span>
  );
}

function summaryFinalEquity(rs: unknown): string {
  if (!rs || typeof rs !== "object") return "—";
  const r = rs as Record<string, unknown>;
  const summary = r.summary;
  if (summary && typeof summary === "object") {
    const fe = (summary as Record<string, unknown>).final_equity;
    if (typeof fe === "number")
      return fe.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  return "—";
}

function summaryCharts(rs: unknown): number {
  if (!rs || typeof rs !== "object") return 0;
  const charts = (rs as Record<string, unknown>).charts;
  if (charts && typeof charts === "object")
    return Object.keys(charts as Record<string, unknown>).length;
  return 0;
}

function paramsLabel(params: unknown): { strategy: string; symbols: string } {
  if (!params || typeof params !== "object") return { strategy: "—", symbols: "—" };
  const p = params as Record<string, unknown>;
  const strategy = typeof p.strategy === "string" ? p.strategy : "—";
  let symbols = "—";
  if (Array.isArray(p.symbols)) {
    const arr = p.symbols as unknown[];
    symbols = arr.length <= 4
      ? arr.join(",")
      : `${arr.slice(0, 4).join(",")} +${arr.length - 4}`;
  }
  return { strategy, symbols };
}

export function Backtests() {
  const [backtests, setBacktests] = useState<Backtest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const resp = await api.listBacktests(50);
      setBacktests(resp.backtests);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Auto-refresh while anything is non-terminal so the UI shows
  // pending → claimed → completed without manual reload. Once
  // everything settles we stop polling to keep the page idle.
  useEffect(() => {
    const hasInFlight = backtests.some(
      (b) => b.state === "Pending" || b.state === "Claimed",
    );
    if (!hasInFlight) return;
    const id = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(id);
  }, [backtests]);

  return (
    <div style={{ padding: "16px 24px", maxWidth: 1200, margin: "0 auto" }}>
      <header style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Backtests</h1>
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
          Trigger a quant-engine backtest (Ensemble + vol-targeting + Monte Carlo) and
          view the 4-panel + percentile-fan charts when the Mac worker completes.
        </p>
      </header>

      <TriggerForm onTriggered={() => void load()} />

      <section style={{
        marginTop: 24,
        background: "var(--bg-panel)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 16,
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <h2 style={{ fontSize: 13, fontWeight: 600, margin: 0, letterSpacing: "0.04em", textTransform: "uppercase" }}>
            Recent runs
          </h2>
          <button
            onClick={() => void load()}
            style={{
              padding: "3px 10px", fontSize: 11, borderRadius: 4,
              border: "1px solid var(--border)", background: "transparent",
              color: "var(--text-dim)", cursor: "pointer",
            }}
          >
            Refresh
          </button>
        </div>

        {error && (
          <div style={{ color: "var(--down)", fontSize: 12, marginBottom: 12 }}>
            {error}
          </div>
        )}

        {loading ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading…</div>
        ) : backtests.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            No backtests yet — fill in the form above and click Run.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={th}>State</th>
                <th style={th}>Strategy</th>
                <th style={th}>Symbols</th>
                <th style={th}>Requested</th>
                <th style={th}>Final equity</th>
                <th style={th}>Charts</th>
                <th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {backtests.map((b) => {
                const { strategy, symbols } = paramsLabel(b.params);
                const charts = summaryCharts(b.result_summary);
                return (
                  <tr key={b.request_id} style={{ borderBottom: "1px solid var(--border-dim, var(--border))" }}>
                    <td style={td}>{stateBadge(b.state)}</td>
                    <td style={{ ...td, fontFamily: "monospace" }}>{strategy}</td>
                    <td style={{ ...td, fontFamily: "monospace", color: "var(--text-dim)" }}>{symbols}</td>
                    <td style={td}>{relativeTime(b.requested_at_utc)}</td>
                    <td style={{ ...td, fontFamily: "monospace" }}>
                      ${summaryFinalEquity(b.result_summary)}
                    </td>
                    <td style={td}>
                      {charts > 0 ? (
                        <span style={{ color: "#1fc16b" }}>{charts}</span>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </td>
                    <td style={td}>
                      {b.state === "Completed" ? (
                        <Link
                          to={`/paper-live/session/${encodeURIComponent(b.request_id)}`}
                          style={{ color: "#4f8cff", fontSize: 11 }}
                        >
                          View charts →
                        </Link>
                      ) : b.state === "Pending" ? (
                        <button
                          onClick={async () => {
                            try {
                              await api.cancelBacktest(b.request_id);
                              void load();
                            } catch (e) {
                              setError(String(e));
                            }
                          }}
                          style={{
                            padding: "2px 7px", fontSize: 11, borderRadius: 3,
                            border: "1px solid #ef4444", background: "transparent",
                            color: "#ef4444", cursor: "pointer",
                          }}
                        >
                          Cancel
                        </button>
                      ) : b.state === "Failed" ? (
                        <span style={{ color: "var(--down)", fontSize: 11 }}>
                          {b.error ? b.error.slice(0, 60) : "Failed"}
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
                          {relativeTime(b.claimed_at_utc ?? b.requested_at_utc)}
                        </span>
                      )}
                    </td>
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

/**
 * TriggerForm — compact inline form mirroring the TraderCockpit's
 * TriggerPanel pattern. No dropdowns (per the user's "avoid
 * dropdowns" rule): strategy lives in a text input so the user can
 * type any sleeve label the worker knows about.
 */
function TriggerForm({ onTriggered }: { onTriggered: () => void }) {
  const [strategy, setStrategy] = useState(DEFAULT_STRATEGY);
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const todayIso = new Date().toISOString().slice(0, 10);
  const sixYearsAgo = new Date();
  sixYearsAgo.setFullYear(sixYearsAgo.getFullYear() - 6);
  const [start, setStart] = useState(sixYearsAgo.toISOString().slice(0, 10));
  const [end, setEnd] = useState(todayIso);
  const [capital, setCapital] = useState(100_000);
  const [nSims, setNSims] = useState(500);
  const [years, setYears] = useState(5);
  const [label, setLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const run = async () => {
    const symbolList = symbols
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter((s) => s.length > 0);
    if (symbolList.length === 0) {
      setFeedback("Add at least one symbol");
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      const resp = await api.runBacktest({
        Strategy: strategy.trim(),
        Symbols: symbolList,
        Start: start,
        End: end,
        InitialCapital: capital,
        NSims: nSims,
        Years: years,
        Label: label.trim() || null,
      });
      setFeedback(`Queued #${resp.requestId.slice(0, 8)} — Mac will pick up on next poll`);
      onTriggered();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section style={{
      background: "var(--bg-panel)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: 16,
    }}>
      <h2 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 12px 0", letterSpacing: "0.04em", textTransform: "uppercase" }}>
        Trigger a new backtest
      </h2>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "end" }}>
        <Field label="Strategy">
          <input
            type="text" value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            style={{ ...inputStyle, width: 160 }}
          />
        </Field>
        <Field label="Symbols (comma-sep)">
          <input
            type="text" value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            style={{ ...inputStyle, width: 240 }}
          />
        </Field>
        <Field label="Start">
          <input type="date" value={start} max={end}
            onChange={(e) => setStart(e.target.value)} style={inputStyle} />
        </Field>
        <Field label="End">
          <input type="date" value={end} max={todayIso} min={start}
            onChange={(e) => setEnd(e.target.value)} style={inputStyle} />
        </Field>
        <Field label="Capital ($)">
          <input
            type="number" min={1000} step={1000} value={capital}
            onChange={(e) => setCapital(Number(e.target.value))}
            style={{ ...inputStyle, width: 110 }}
          />
        </Field>
        <Field label="MC sims">
          <input
            type="number" min={50} max={5000} step={50} value={nSims}
            onChange={(e) => setNSims(Number(e.target.value))}
            style={{ ...inputStyle, width: 80 }}
          />
        </Field>
        <Field label="MC years">
          <input
            type="number" min={1} max={30} value={years}
            onChange={(e) => setYears(Number(e.target.value))}
            style={{ ...inputStyle, width: 70 }}
          />
        </Field>
        <Field label="Label (optional)">
          <input
            type="text" value={label}
            placeholder="weekly research"
            onChange={(e) => setLabel(e.target.value)}
            style={{ ...inputStyle, width: 180 }}
          />
        </Field>
        <button
          onClick={() => void run()}
          disabled={submitting}
          style={{
            padding: "6px 16px", fontSize: 12, fontWeight: 600,
            background: submitting ? "var(--text-muted)" : "#1fc16b",
            color: "white", border: "none", borderRadius: 4,
            cursor: submitting ? "wait" : "pointer",
          }}
        >
          {submitting ? "Queueing…" : "Run"}
        </button>
      </div>
      {feedback && (
        <div style={{
          marginTop: 10, fontSize: 11,
          color: feedback.startsWith("Failed")
            ? "var(--down)"
            : "#1fc16b",
        }}>
          {feedback}
        </div>
      )}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        letterSpacing: "0.04em", textTransform: "uppercase",
      }}>
        {label}
      </span>
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "5px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
};

const th: React.CSSProperties = {
  padding: "6px 10px", textAlign: "left",
  fontSize: 10, fontWeight: 700,
  letterSpacing: "0.04em", textTransform: "uppercase",
  color: "var(--text-muted)",
};

const td: React.CSSProperties = {
  padding: "8px 10px",
};
