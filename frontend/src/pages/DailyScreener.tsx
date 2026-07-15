/**
 * DailyScreener — /screener route.
 *
 * Shows the latest wheel + options candidate screen results from the
 * TradePro daily screener job. Data comes from /api/options/candidates
 * (pushed by the Mac tradepro-options-screen job after each run).
 *
 * The full wheel + swing email report (with PDF) is delivered to
 * info@coreconsultingit.com each trading day — this page shows the same
 * candidate list inline for quick reference.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";

type Candidate = {
  symbol: string;
  regime: string | null;
  iv_rank: number | null;
  iv: number | null;
  open_interest: number | null;
  spread_usd: number | null;
  eligible: boolean;
  blocks: string[];
  warnings: string[];
  suggested_strike: number | null;
  suggested_delta: number | null;
  suggested_premium: number | null;
};

type CandidatesResponse = {
  generated_at_utc: string | null;
  market_open: boolean;
  candidates: Candidate[];
};

function fmtPct(n: number | null): string {
  if (n == null) return "—";
  return `${n.toFixed(1)}%`;
}

function fmtNum(n: number | null, d = 2): string {
  if (n == null) return "—";
  return n.toFixed(d);
}

function fmtMoney(n: number | null): string {
  if (n == null) return "—";
  return `$${n.toFixed(2)}`;
}

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const TH: React.CSSProperties = {
  textAlign: "left",
  padding: "7px 10px",
  fontWeight: 600,
  fontSize: 11,
  color: "var(--text-dim)",
  borderBottom: "1px solid var(--sep, #1b2233)",
  whiteSpace: "nowrap",
};

const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };

const TD: React.CSSProperties = {
  padding: "8px 10px",
  fontSize: 12,
  fontFamily: "monospace",
  borderBottom: "1px solid var(--sep, #141b2b)",
};

const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };

type RunResult = {
  ok: boolean;
  result?: {
    run_date: string;
    tickers_screened: number;
    wheel_top: string[];
    swing_top: string[];
    dual_candidates: string[];
  };
  stderr?: string;
};

export function DailyScreener() {
  const [data, setData] = useState<CandidatesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);

  useEffect(() => {
    api.optionsCandidates()
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  async function handleRunScreener() {
    setRunning(true);
    setRunResult(null);
    setRunErr(null);
    try {
      const res = await api.runScreener();
      setRunResult(res as RunResult);
    } catch (e: unknown) {
      setRunErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const candidates = data?.candidates ?? [];
  const eligible = candidates.filter((c) => c.eligible);
  const blocked = candidates.filter((c) => !c.eligible);

  return (
    <div style={{ maxWidth: 1100 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 18 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "var(--text)" }}>Daily Screener</h2>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
            Wheel + swing candidates. Run the screener on demand or wait for the scheduled daily email.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {data && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>
              <div>Generated: {fmtTs(data.generated_at_utc)}</div>
              <div style={{ marginTop: 2 }}>
                Market: <span style={{ color: data.market_open ? "#1fc16b" : "#ef4444", fontWeight: 600 }}>
                  {data.market_open ? "Open" : "Closed"}
                </span>
              </div>
            </div>
          )}
          <button
            onClick={handleRunScreener}
            disabled={running}
            style={{
              background: running ? "rgba(31,193,107,0.15)" : "rgba(31,193,107,0.2)",
              border: "1px solid rgba(31,193,107,0.4)",
              borderRadius: 7,
              color: running ? "var(--text-muted)" : "#1fc16b",
              cursor: running ? "not-allowed" : "pointer",
              fontSize: 13,
              fontWeight: 600,
              padding: "8px 18px",
              whiteSpace: "nowrap",
            }}
          >
            {running ? "Running…" : "▶ Run Screener"}
          </button>
        </div>
      </div>

      {/* Run result banner */}
      {runErr && (
        <Note tone="error" style={{ marginBottom: 14 }}>Run failed: {runErr}</Note>
      )}
      {runResult && (
        <div style={{
          marginBottom: 14,
          padding: "12px 16px",
          border: "1px solid rgba(31,193,107,0.25)",
          borderRadius: 8,
          background: "rgba(31,193,107,0.05)",
          fontSize: 12,
        }}>
          <span style={{ fontWeight: 700, color: "#1fc16b" }}>Screener complete — {runResult.result?.run_date}</span>
          <span style={{ color: "var(--text-muted)", marginLeft: 10 }}>
            {runResult.result?.tickers_screened} tickers screened ·{" "}
            Wheel top: {runResult.result?.wheel_top?.join(", ") || "none"} ·{" "}
            Swing top: {runResult.result?.swing_top?.join(", ") || "none"}
          </span>
          {runResult.result?.dual_candidates && runResult.result.dual_candidates.length > 0 && (
            <span style={{ color: "#e0b341", marginLeft: 8 }}>
              ★ Dual: {runResult.result.dual_candidates.join(", ")}
            </span>
          )}
        </div>
      )}

      {err ? (
        <Note tone="error">Screener unavailable: {err}</Note>
      ) : loading ? (
        <Note>Loading screener results…</Note>
      ) : candidates.length === 0 ? (
        <Note>No stored results yet — click <strong>Run Screener</strong> to fetch live data and run the screen now.</Note>
      ) : (
        <>
          {/* Summary strip */}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
            <Kpi label="Total screened" value={String(candidates.length)} />
            <Kpi label="Eligible" value={String(eligible.length)} accent="#1fc16b" />
            <Kpi label="Blocked" value={String(blocked.length)} accent="#ef4444" />
          </div>

          {/* Eligible candidates */}
          {eligible.length > 0 && (
            <Section title="Eligible Candidates">
              <CandidateTable rows={eligible} />
            </Section>
          )}

          {/* Blocked candidates */}
          {blocked.length > 0 && (
            <Section title="Blocked / Excluded" collapsed>
              <CandidateTable rows={blocked} />
            </Section>
          )}
        </>
      )}
    </div>
  );
}

function CandidateTable({ rows }: { rows: Candidate[] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 780 }}>
        <thead>
          <tr>
            <th style={TH}>Symbol</th>
            <th style={TH}>Regime</th>
            <th style={TH_R}>IV Rank</th>
            <th style={TH_R}>IV</th>
            <th style={TH_R}>Open Int</th>
            <th style={TH_R}>Spread</th>
            <th style={TH_R}>Strike</th>
            <th style={TH_R}>Delta</th>
            <th style={TH_R}>Premium</th>
            <th style={TH}>Flags</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.symbol} style={{ background: c.eligible ? "transparent" : "rgba(239,68,68,0.03)" }}>
              <td style={{ ...TD, fontWeight: 700, color: c.eligible ? "#1fc16b" : "var(--text-muted)" }}>
                {c.symbol}
              </td>
              <td style={{ ...TD, color: "var(--text-dim)", textTransform: "capitalize" }}>
                {c.regime ?? "—"}
              </td>
              <td style={{ ...TD_R, color: ivRankColour(c.iv_rank) }}>{fmtPct(c.iv_rank)}</td>
              <td style={TD_R}>{fmtPct(c.iv != null ? c.iv * 100 : null)}</td>
              <td style={TD_R}>{c.open_interest != null ? c.open_interest.toLocaleString() : "—"}</td>
              <td style={TD_R}>{fmtMoney(c.spread_usd)}</td>
              <td style={TD_R}>{fmtMoney(c.suggested_strike)}</td>
              <td style={TD_R}>{fmtNum(c.suggested_delta, 2)}</td>
              <td style={TD_R}>{fmtMoney(c.suggested_premium)}</td>
              <td style={{ ...TD, maxWidth: 260 }}>
                {c.blocks.length > 0 && (
                  <span style={{ color: "#ef4444", fontSize: 11 }}>
                    {c.blocks.join(" · ")}
                  </span>
                )}
                {c.warnings.length > 0 && (
                  <span style={{ color: "#e0b341", fontSize: 11, marginLeft: c.blocks.length ? 6 : 0 }}>
                    {c.warnings.join(" · ")}
                  </span>
                )}
                {c.blocks.length === 0 && c.warnings.length === 0 && (
                  <span style={{ color: "var(--text-muted)", fontSize: 11 }}>—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ivRankColour(ivr: number | null): string {
  if (ivr == null) return "var(--text-muted)";
  if (ivr >= 40) return "#1fc16b";
  if (ivr >= 20) return "#e0b341";
  return "#ef4444";
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.03)",
      border: "1px solid var(--sep, #1b2233)",
      borderRadius: 8,
      padding: "10px 16px",
      minWidth: 100,
    }}>
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: accent ?? "var(--text)", fontFamily: "monospace" }}>
        {value}
      </div>
    </div>
  );
}

function Section({ title, children, collapsed = false }: { title: string; children: React.ReactNode; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  return (
    <div style={{ marginBottom: 20 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          background: "transparent",
          border: "none",
          cursor: "pointer",
          color: "var(--text)",
          fontWeight: 700,
          fontSize: 13,
          padding: "0 0 8px",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{open ? "▼" : "▶"}</span>
        {title}
      </button>
      {open && (
        <div style={{
          border: "1px solid var(--sep, #1b2233)",
          borderRadius: 8,
          background: "rgba(255,255,255,0.015)",
          overflow: "hidden",
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

function Note({ children, tone, style }: { children: React.ReactNode; tone?: "error"; style?: React.CSSProperties }) {
  return (
    <div style={{
      fontSize: 13,
      padding: "12px 16px",
      color: tone === "error" ? "#ef4444" : "var(--text-muted)",
      background: tone === "error" ? "rgba(239,68,68,0.05)" : "transparent",
      border: `1px solid ${tone === "error" ? "rgba(239,68,68,0.2)" : "var(--sep, #1b2233)"}`,
      borderRadius: 8,
      ...style,
    }}>
      {children}
    </div>
  );
}
