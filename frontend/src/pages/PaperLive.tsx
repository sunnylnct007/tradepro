import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { PaperSubNav } from "../components/PaperSubNav";

// Paper-session trigger and monitor page.
// Lets the trader queue a paper trading session via the ops
// trigger queue, watch its state (Pending → Claimed → Completed/Failed),
// and cancel sessions that haven't been picked up yet.
// Auto-refreshes the queue every 30 seconds.

const STRATEGIES = ["ichimoku_equity", "ichimoku_fx_mr"] as const;
type Strategy = (typeof STRATEGIES)[number];

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
    lower === "pending"
      ? "#d97706"
      : lower === "claimed"
      ? "#4f8cff"
      : lower === "completed"
      ? "#1fc16b"
      : lower === "failed"
      ? "#ef4444"
      : "#6b7280"; // cancelled / unknown
  const bg =
    lower === "pending"
      ? "rgba(217,119,6,0.12)"
      : lower === "claimed"
      ? "rgba(79,140,255,0.12)"
      : lower === "completed"
      ? "rgba(31,193,107,0.12)"
      : lower === "failed"
      ? "rgba(239,68,68,0.12)"
      : "rgba(107,114,128,0.12)";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 600,
        color: colour,
        background: bg,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}
    >
      {state}
    </span>
  );
}

function getStrategyFromParams(params: unknown): string {
  if (params && typeof params === "object") {
    const p = params as Record<string, unknown>;
    if (typeof p.strategy === "string") return p.strategy;
  }
  return "—";
}

// Render result_summary as readable chips instead of raw-JSON soup.
// Order matters — fills first because that's the "did it trade?"
// signal the operator scans for. Symbols compressed if >6 to keep
// the cell width manageable.
function formatResultSummary(s: Session): React.ReactNode {
  if (s.error) {
    return <span style={{ color: "var(--down)", fontSize: 12 }}>{s.error}</span>;
  }
  const rs = s.result_summary;
  if (!rs || typeof rs !== "object") {
    return <span style={{ color: "var(--text-dim)", fontSize: 12 }}>—</span>;
  }
  const r = rs as Record<string, unknown>;
  const num = (k: string) => (typeof r[k] === "number" ? (r[k] as number) : undefined);
  const arr = (k: string) => (Array.isArray(r[k]) ? (r[k] as unknown[]) : undefined);

  const fills = num("fills") ?? 0;
  const equity = num("equity");
  const realised = num("realised_pnl");
  const positions = num("positions") ?? 0;
  const symbols = (arr("symbols") as string[] | undefined) ?? [];
  const omsPosted = num("oms_orders_posted");
  const strategies = arr("strategies") ?? [];
  let decisions = 0;
  for (const st of strategies) {
    const ds = (st as Record<string, unknown> | null)?.decisions;
    if (Array.isArray(ds)) decisions += ds.length;
  }

  const symbolLabel =
    symbols.length === 0
      ? "0 symbols"
      : symbols.length <= 6
      ? symbols.join(", ")
      : `${symbols.slice(0, 6).join(", ")} +${symbols.length - 6}`;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, fontSize: 11, lineHeight: 1.5 }}>
      <Chip
        label={`${fills} fills`}
        tone={fills > 0 ? "ok" : "muted"}
      />
      {equity !== undefined && (
        <Chip label={`equity ${equity.toFixed(2)}`} tone="muted" />
      )}
      {realised !== undefined && realised !== 0 && (
        <Chip
          label={`pnl ${realised >= 0 ? "+" : ""}${realised.toFixed(2)}`}
          tone={realised >= 0 ? "ok" : "down"}
        />
      )}
      {positions > 0 && <Chip label={`${positions} open`} tone="muted" />}
      <Chip label={symbolLabel} tone="muted" mono title={symbols.join(",")} />
      {omsPosted !== undefined && omsPosted > 0 && (
        <Chip label={`oms ${omsPosted}`} tone="ok" />
      )}
      {decisions > 0 && (
        <Chip label={`${decisions} decisions`} tone="muted" />
      )}
    </div>
  );
}

function Chip({
  label,
  tone = "muted",
  mono = false,
  title,
}: {
  label: string;
  tone?: "ok" | "down" | "muted";
  mono?: boolean;
  title?: string;
}) {
  const colour =
    tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--text-dim)";
  const bg =
    tone === "ok"
      ? "rgba(31,193,107,0.10)"
      : tone === "down"
      ? "rgba(239,68,68,0.10)"
      : "rgba(255,255,255,0.04)";
  return (
    <span
      title={title}
      style={{
        padding: "2px 7px",
        borderRadius: 4,
        background: bg,
        color: colour,
        fontFamily: mono ? "monospace" : undefined,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

function downloadSessionJson(s: Session) {
  const filename = `paper-session-${s.request_id.slice(0, 8)}.json`;
  const blob = new Blob([JSON.stringify(s, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

const actionButtonStyle: React.CSSProperties = {
  fontSize: 11,
  padding: "4px 10px",
  color: "var(--text-muted)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  background: "transparent",
  cursor: "pointer",
};

// Static schedule definition — mirrors scripts/launchd/*.plist
const SCHEDULE = [
  {
    job: "paper-equity",
    schedule: "Weekdays 13:35",
    strategy: "ichimoku_equity" as Strategy,
    symbols: ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "BRK-B", "JPM", "V"],
    capital_usd: 100_000,
    notes: "8:35 ET — just after US open",
  },
  {
    job: "paper-fx",
    schedule: "Weekdays 22:05",
    strategy: "ichimoku_fx_mr" as Strategy,
    symbols: [],
    capital_usd: 50_000,
    notes: "6:05 ET — NY FX session · safe on UK holidays",
  },
  {
    job: "paper-watch",
    schedule: "Every 2 min",
    strategy: null,
    symbols: [],
    capital_usd: 0,
    notes: "Picks up UI-triggered sessions (no adhoc trigger)",
  },
] as const;

export function PaperLive() {
  // ── Form state ────────────────────────────────────────────────────────────
  const [strategy, setStrategy] = useState<Strategy>("ichimoku_equity");
  const [symbolsRaw, setSymbolsRaw] = useState("AAPL,MSFT,NVDA,TSLA");
  const [capitalUsd, setCapitalUsd] = useState(100000);
  const [placementMode, setPlacementMode] = useState<"manual" | "auto">("manual");
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // ── Session queue state ───────────────────────────────────────────────────
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [triggeringJob, setTriggeringJob] = useState<string | null>(null);

  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(kind: "ok" | "err", msg: string) {
    setToast({ kind, msg });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 5000);
  }

  // ── Load + auto-refresh sessions ─────────────────────────────────────────
  function loadSessions() {
    api
      .paperSessions()
      .then((r) => setSessions(r.sessions))
      .catch((e) => setQueueError(String(e)));
  }

  useEffect(() => {
    loadSessions();
    const id = setInterval(loadSessions, 30_000);
    return () => clearInterval(id);
  }, []);

  // ── Submit handler ────────────────────────────────────────────────────────
  async function handleRun() {
    setSubmitting(true);
    try {
      const symbols =
        strategy === "ichimoku_fx_mr"
          ? [] // FX MR uses all G10 pairs — no symbol list needed
          : symbolsRaw
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean);
      const res = await api.runPaperSession({
        strategy,
        symbols,
        // Explicit broker so the daemon doesn't fall back to its
        // default. T212 → router posts intents to pending_orders
        // queue (manual placement_mode) → human Approve → .NET's
        // Trading212DemoClient places to demo.trading212.com. Without
        // this PaperLive triggers were going to broker=yfinance (the
        // daemon default after commit 770fb17) which simulates fills
        // locally and never touches T212.
        broker: "t212",
        capital_usd: capitalUsd,
        placement_mode: placementMode,
      });
      showToast("ok", `Session queued — request_id ${res.request_id.slice(0, 8)}`);
      loadSessions();
    } catch (e) {
      showToast("err", String(e));
    } finally {
      setSubmitting(false);
    }
  }

  // ── Trigger a scheduled job ad-hoc ───────────────────────────────────────
  async function handleTriggerScheduled(job: typeof SCHEDULE[0] | typeof SCHEDULE[1]) {
    setTriggeringJob(job.job);
    try {
      const res = await api.runPaperSession({
        strategy: job.strategy,
        symbols: [...job.symbols],
        broker: "t212",
        capital_usd: job.capital_usd,
        placement_mode: placementMode,
      });
      showToast("ok", `${job.job} queued — id ${res.request_id.slice(0, 8)}`);
      loadSessions();
    } catch (e) {
      showToast("err", `Failed to queue ${job.job}: ${String(e)}`);
    } finally {
      setTriggeringJob(null);
    }
  }

  // ── Cancel handler ────────────────────────────────────────────────────────
  async function handleCancel(requestId: string) {
    setCancellingId(requestId);
    try {
      await api.cancelPaperSession(requestId);
      loadSessions();
    } catch (e) {
      showToast("err", `Cancel failed: ${String(e)}`);
    } finally {
      setCancellingId(null);
    }
  }

  const isFxMr = strategy === "ichimoku_fx_mr";

  return (
    <div>
      <PaperSubNav />
      {/* ── Header ───────────────────────────────────────────────────────── */}
      <h2 style={{ margin: "0 0 4px" }}>Paper Trading</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginTop: 0, marginBottom: 20 }}>
        Run paper strategies against T212 demo · sessions are queued and
        picked up by the Mac worker
      </p>

      {/* ── Toast ────────────────────────────────────────────────────────── */}
      {toast && (
        <div
          style={{
            padding: "10px 14px",
            marginBottom: 16,
            border: `1px solid ${toast.kind === "ok" ? "var(--up)" : "var(--down)"}`,
            background:
              toast.kind === "ok" ? "rgba(31,193,107,0.08)" : "rgba(239,68,68,0.08)",
            color: toast.kind === "ok" ? "var(--up)" : "var(--down)",
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          {toast.msg}
        </div>
      )}

      {/* ── Run Now panel ────────────────────────────────────────────────── */}
      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: "18px 20px",
          background: "var(--bg-elev, var(--bg))",
          marginBottom: 28,
          maxWidth: 560,
        }}
      >
        <div
          style={{
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--text-dim)",
            marginBottom: 16,
          }}
        >
          Run Session
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Strategy picker */}
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Strategy</span>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as Strategy)}
              style={{
                padding: "7px 10px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg)",
                color: "var(--text)",
                fontSize: 13,
              }}
            >
              {STRATEGIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          {/* Symbols — hidden for FX MR */}
          {!isFxMr && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                Symbols{" "}
                <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
                  (comma-separated)
                </span>
              </span>
              <input
                type="text"
                value={symbolsRaw}
                onChange={(e) => setSymbolsRaw(e.target.value)}
                placeholder="AAPL,MSFT,NVDA,TSLA"
                style={{
                  padding: "7px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "var(--bg)",
                  color: "var(--text)",
                  fontSize: 13,
                  fontFamily: "monospace",
                }}
              />
            </label>
          )}
          {isFxMr && (
            <div
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                padding: "6px 10px",
                background: "rgba(79,140,255,0.06)",
                border: "1px solid rgba(79,140,255,0.2)",
                borderRadius: 6,
              }}
            >
              ichimoku_fx_mr trades all G10 pairs — no symbol list required.
            </div>
          )}

          {/* Capital USD */}
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Capital (USD)</span>
            <input
              type="number"
              value={capitalUsd}
              onChange={(e) => setCapitalUsd(Number(e.target.value))}
              min={1000}
              step={10000}
              style={{
                padding: "7px 10px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg)",
                color: "var(--text)",
                fontSize: 13,
                fontFamily: "monospace",
              }}
            />
          </label>

          {/* Placement mode */}
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Placement mode</span>
            <select
              value={placementMode}
              onChange={(e) =>
                setPlacementMode(e.target.value as "manual" | "auto")
              }
              style={{
                padding: "7px 10px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg)",
                color: "var(--text)",
                fontSize: 13,
              }}
            >
              <option value="manual">manual — orders queue for human review</option>
              <option value="auto">auto — orders placed without approval</option>
            </select>
          </label>

          <button
            className="primary"
            onClick={handleRun}
            disabled={submitting}
            style={{ padding: "9px 20px", fontSize: 13, marginTop: 4, alignSelf: "flex-start" }}
          >
            {submitting ? "Queuing…" : "Run Session"}
          </button>
        </div>
      </div>

      {/* ── Session queue ─────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 10,
            marginBottom: 12,
          }}
        >
          <h3 style={{ margin: 0, fontSize: 14, color: "var(--text-dim)" }}>
            Session queue
          </h3>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            auto-refreshes every 30s
          </span>
        </div>

        {queueError && (
          <div
            style={{
              padding: "10px 14px",
              marginBottom: 12,
              border: "1px solid var(--down)",
              background: "rgba(239,68,68,0.08)",
              color: "var(--down)",
              borderRadius: 8,
              fontSize: 13,
            }}
          >
            {queueError}
          </div>
        )}

        {sessions === null && !queueError && (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading…</div>
        )}

        {sessions !== null && sessions.length === 0 && (
          <div
            style={{
              padding: "14px 16px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            No sessions queued yet. Use the form above to trigger one.
          </div>
        )}

        {sessions !== null && sessions.length > 0 && (
          <div
            style={{
              border: "1px solid var(--border)",
              borderRadius: 10,
              overflow: "hidden",
            }}
          >
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12,
              }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid var(--border)",
                    color: "var(--text-dim)",
                    background: "var(--bg-hover, rgba(255,255,255,0.03))",
                  }}
                >
                  <th style={{ textAlign: "left", padding: "8px 12px" }}>ID</th>
                  <th style={{ textAlign: "left", padding: "8px 12px" }}>Strategy</th>
                  <th style={{ textAlign: "left", padding: "8px 12px" }}>State</th>
                  <th style={{ textAlign: "left", padding: "8px 12px" }}>Requested</th>
                  <th style={{ textAlign: "left", padding: "8px 12px" }}>Summary / Error</th>
                  <th style={{ textAlign: "right", padding: "8px 12px" }} />
                </tr>
              </thead>
              <tbody>
                {sessions.map((s, idx) => {
                  const stateLC = s.state.toLowerCase();
                  const isPending = stateLC === "pending";
                  const isClaimed = stateLC === "claimed";
                  const isCancelling = cancellingId === s.request_id;
                  const summary = formatResultSummary(s);
                  return (
                    <tr
                      key={s.request_id}
                      style={{
                        borderBottom:
                          idx < sessions.length - 1
                            ? "1px solid var(--border)"
                            : "none",
                        background: "transparent",
                      }}
                    >
                      <td
                        style={{
                          padding: "10px 12px",
                          fontFamily: "monospace",
                          color: "var(--text-dim)",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {s.request_id.slice(0, 8)}
                      </td>
                      <td style={{ padding: "10px 12px", color: "var(--text)" }}>
                        {getStrategyFromParams(s.params)}
                      </td>
                      <td style={{ padding: "10px 12px" }}>{stateBadge(s.state)}</td>
                      <td
                        style={{
                          padding: "10px 12px",
                          color: "var(--text-muted)",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {relativeTime(s.requested_at_utc)}
                      </td>
                      <td
                        style={{
                          padding: "10px 12px",
                          color: s.error ? "var(--down)" : "var(--text-dim)",
                          maxWidth: 360,
                        }}
                        title={
                          s.error
                            ? s.error
                            : s.result_summary
                            ? JSON.stringify(s.result_summary)
                            : undefined
                        }
                      >
                        {summary}
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "right", whiteSpace: "nowrap" }}>
                        <Link
                          to={`/paper-live/session/${encodeURIComponent(s.request_id)}`}
                          style={{ ...actionButtonStyle, textDecoration: "none", display: "inline-block" }}
                          title="Inspect bars, decisions, fills, positions"
                        >
                          Details →
                        </Link>
                        <button
                          onClick={() => downloadSessionJson(s)}
                          style={{ ...actionButtonStyle, marginLeft: 6 }}
                          title="Download the full session payload as JSON"
                        >
                          Export
                        </button>
                        {(isPending || isClaimed) && (
                          <button
                            onClick={() => handleCancel(s.request_id)}
                            disabled={isCancelling}
                            style={{ ...actionButtonStyle, marginLeft: 6 }}
                          >
                            {isCancelling ? "Cancelling…" : "Cancel"}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Automatic schedule ───────────────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <h3 style={{ margin: "0 0 12px", fontSize: 14, color: "var(--text-dim)" }}>
          Automatic schedule
        </h3>
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: 10,
            overflow: "hidden",
            maxWidth: 680,
          }}
        >
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr
                style={{
                  borderBottom: "1px solid var(--border)",
                  color: "var(--text-dim)",
                  background: "var(--bg-hover, rgba(255,255,255,0.03))",
                }}
              >
                <th style={{ textAlign: "left", padding: "8px 12px" }}>Job</th>
                <th style={{ textAlign: "left", padding: "8px 12px" }}>Schedule (UTC)</th>
                <th style={{ textAlign: "left", padding: "8px 12px" }}>Strategy</th>
                <th style={{ textAlign: "left", padding: "8px 12px" }}>Notes</th>
                <th style={{ textAlign: "right", padding: "8px 12px" }} />
              </tr>
            </thead>
            <tbody>
              {SCHEDULE.map((row, idx) => {
                const canTrigger = row.strategy !== null;
                const isTriggering = triggeringJob === row.job;
                return (
                  <tr
                    key={row.job}
                    style={{
                      borderBottom: idx < SCHEDULE.length - 1 ? "1px solid var(--border)" : "none",
                    }}
                  >
                    <td
                      style={{
                        padding: "10px 12px",
                        fontFamily: "monospace",
                        fontSize: 11,
                        color: "var(--text-dim)",
                      }}
                    >
                      {row.job}
                    </td>
                    <td style={{ padding: "10px 12px", color: "var(--text)" }}>
                      {row.schedule}
                    </td>
                    <td
                      style={{
                        padding: "10px 12px",
                        fontFamily: "monospace",
                        fontSize: 11,
                        color: "var(--text)",
                      }}
                    >
                      {row.strategy ?? "—"}
                    </td>
                    <td style={{ padding: "10px 12px", color: "var(--text-muted)" }}>
                      {row.notes}
                    </td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>
                      {canTrigger && (
                        <button
                          onClick={() => handleTriggerScheduled(row as typeof SCHEDULE[0])}
                          disabled={isTriggering || !!triggeringJob}
                          style={{
                            fontSize: 11,
                            padding: "4px 12px",
                            borderRadius: 6,
                            border: "1px solid var(--border)",
                            background: isTriggering
                              ? "var(--bg-hover)"
                              : "transparent",
                            color: "var(--text)",
                            cursor: isTriggering ? "default" : "pointer",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {isTriggering ? "Queuing…" : "▶ Run now"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
          Install once from the repo root:{" "}
          <code
            style={{
              fontFamily: "monospace",
              background: "var(--bg-hover)",
              padding: "1px 6px",
              borderRadius: 4,
            }}
          >
            bash scripts/install_paper_schedules.sh
          </code>
          {" · "}logs at{" "}
          <code style={{ fontFamily: "monospace" }}>/tmp/tradepro-paper-*.log</code>
        </p>
      </div>


      {/* ── Snapshots link ────────────────────────────────────────────────── */}
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
        <Link
          to="/paper-backtest"
          style={{ color: "var(--text-dim)", textDecoration: "underline" }}
        >
          View backtest reports →
        </Link>
      </div>
    </div>
  );
}
