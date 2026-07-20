/**
 * DailyScreener — /screener route.
 *
 * Two panels:
 *  1. Live Scan — real-time IBKR snapshot for all universe tickers
 *     (IV rank, put yield, dividend, price, change). Refreshes on demand.
 *  2. Last Run — stored results from the most recent daily_run.py execution.
 *     "Run Screener" button triggers a full run + email.
 */
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

// ── types ────────────────────────────────────────────────────────────────────

type LiveRow = {
  ticker: string;
  price: number | null;
  change_pct: number | null;
  change_abs: number | null;
  ivp_52w: number | null;
  iv_annual: number | null;
  hv30: number | null;
  div_yield: number | null;
  put_yield_pct: number | null;
  high_52w: number | null;
  low_52w: number | null;
  dist_low_pct: number | null;
  avg_vol_90d: number | null;
};

type LiveResponse = {
  fetched_at_utc: string;
  rows: LiveRow[];
};

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

// ── formatters ───────────────────────────────────────────────────────────────

function fmtPct(n: number | null, d = 1): string {
  if (n == null) return "—";
  return `${n.toFixed(d)}%`;
}
function fmtMoney(n: number | null): string {
  if (n == null) return "—";
  return `$${n.toFixed(2)}`;
}
function fmtVol(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}
function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// ── styles ───────────────────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 10px",
  fontWeight: 600,
  fontSize: 11,
  color: "var(--text-dim)",
  borderBottom: "1px solid var(--sep, #1b2233)",
  whiteSpace: "nowrap",
  cursor: "pointer",
  userSelect: "none",
};
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD: React.CSSProperties = {
  padding: "7px 10px",
  fontSize: 12,
  fontFamily: "monospace",
  borderBottom: "1px solid var(--sep, #141b2b)",
};
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };

// ── ivp colour ───────────────────────────────────────────────────────────────

function ivpColour(v: number | null): string {
  if (v == null) return "var(--text-muted)";
  if (v >= 60) return "#1fc16b";
  if (v >= 30) return "#e0b341";
  return "#ef4444";
}
function changePctColour(v: number | null): string {
  if (v == null) return "var(--text-muted)";
  if (v > 0) return "#1fc16b";
  if (v < 0) return "#ef4444";
  return "var(--text-muted)";
}

// ── sort helper ──────────────────────────────────────────────────────────────

type SortKey = keyof LiveRow;

function sortRows(rows: LiveRow[], key: SortKey, asc: boolean): LiveRow[] {
  return [...rows].sort((a, b) => {
    const av = a[key] ?? (asc ? Infinity : -Infinity);
    const bv = b[key] ?? (asc ? Infinity : -Infinity);
    if (av < bv) return asc ? -1 : 1;
    if (av > bv) return asc ? 1 : -1;
    return 0;
  });
}

// ── main component ────────────────────────────────────────────────────────────

export function DailyScreener() {
  // live scan
  const [live, setLive] = useState<LiveResponse | null>(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("ivp_52w");
  const [sortAsc, setSortAsc] = useState(false);

  // stored candidates (last run)
  const [data, setData] = useState<CandidatesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // run screener
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);

  useEffect(() => {
    api.optionsCandidates()
      .then(setData)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const fetchLive = useCallback(() => {
    setLiveLoading(true);
    setLiveErr(null);
    api.screenerLive()
      .then(setLive)
      .catch((e: unknown) => setLiveErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLiveLoading(false));
  }, []);

  async function handleRunScreener() {
    setRunning(true);
    setRunResult(null);
    setRunErr(null);
    try {
      const res = await api.runScreener();
      setRunResult(res as unknown as RunResult);
      // refresh stored candidates after run
      api.optionsCandidates().then(setData).catch(() => {});
    } catch (e: unknown) {
      setRunErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortAsc((a) => !a);
    else { setSortKey(key); setSortAsc(false); }
  }

  function SortHdr({ k, label, right }: { k: SortKey; label: string; right?: boolean }) {
    const active = sortKey === k;
    const arrow = active ? (sortAsc ? " ▲" : " ▼") : "";
    return (
      <th style={right ? TH_R : TH} onClick={() => toggleSort(k)}>
        {label}{arrow}
      </th>
    );
  }

  const sortedRows = live ? sortRows(live.rows, sortKey, sortAsc) : [];
  const candidates = data?.candidates ?? [];
  const eligible = candidates.filter((c) => c.eligible);
  const blocked = candidates.filter((c) => !c.eligible);

  return (
    <div style={{ maxWidth: 1200 }}>

      {/* ── LIVE SCAN ──────────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "var(--text)" }}>Live Scan</h2>
            <p style={{ margin: "3px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
              Real-time IV rank, put yield &amp; dividend for all screener tickers. Click any column header to sort.
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {live && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Updated {fmtTs(live.fetched_at_utc)}
              </span>
            )}
            <button
              onClick={fetchLive}
              disabled={liveLoading}
              style={{
                background: liveLoading ? "rgba(96,165,250,0.1)" : "rgba(96,165,250,0.15)",
                border: "1px solid rgba(96,165,250,0.4)",
                borderRadius: 7,
                color: liveLoading ? "var(--text-muted)" : "#60a5fa",
                cursor: liveLoading ? "not-allowed" : "pointer",
                fontSize: 13,
                fontWeight: 600,
                padding: "7px 16px",
                whiteSpace: "nowrap",
              }}
            >
              {liveLoading ? "Fetching…" : "⟳ Refresh Live Data"}
            </button>
          </div>
        </div>

        {liveErr && <Note tone="error">Live scan failed: {liveErr}</Note>}

        {!live && !liveLoading && !liveErr && (
          <Note>Click <strong>Refresh Live Data</strong> to load real-time IBKR snapshots.</Note>
        )}

        {liveLoading && <Note>Fetching live snapshots for {30} tickers…</Note>}

        {live && live.rows.length > 0 && (
          <div style={{ border: "1px solid var(--sep, #1b2233)", borderRadius: 8, overflow: "hidden", background: "rgba(255,255,255,0.015)" }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
                <thead>
                  <tr>
                    <SortHdr k="ticker" label="Ticker" />
                    <SortHdr k="price" label="Price" right />
                    <SortHdr k="change_pct" label="Chg %" right />
                    <SortHdr k="ivp_52w" label="IVP 52w" right />
                    <SortHdr k="iv_annual" label="IV Ann%" right />
                    <SortHdr k="hv30" label="HV30%" right />
                    <SortHdr k="put_yield_pct" label="Put Yield/mo" right />
                    <SortHdr k="div_yield" label="Div Yield" right />
                    <SortHdr k="dist_low_pct" label="vs 52w Low" right />
                    <SortHdr k="avg_vol_90d" label="Avg Vol 90d" right />
                  </tr>
                </thead>
                <tbody>
                  {sortedRows.map((r) => (
                    <tr key={r.ticker} style={{ background: "transparent" }}>
                      <td style={{ ...TD, fontWeight: 700, color: "var(--text)" }}>{r.ticker}</td>
                      <td style={TD_R}>{fmtMoney(r.price)}</td>
                      <td style={{ ...TD_R, color: changePctColour(r.change_pct) }}>
                        {r.change_pct != null ? `${r.change_pct > 0 ? "+" : ""}${r.change_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td style={{ ...TD_R, color: ivpColour(r.ivp_52w), fontWeight: 600 }}>
                        {fmtPct(r.ivp_52w, 0)}
                      </td>
                      <td style={TD_R}>{fmtPct(r.iv_annual != null ? r.iv_annual : null, 1)}</td>
                      <td style={TD_R}>{fmtPct(r.hv30 != null ? r.hv30 : null, 1)}</td>
                      <td style={{ ...TD_R, color: r.put_yield_pct != null && r.put_yield_pct >= 1.5 ? "#1fc16b" : "var(--text)" }}>
                        {fmtPct(r.put_yield_pct, 2)}
                      </td>
                      <td style={{ ...TD_R, color: r.div_yield != null && r.div_yield >= 4 ? "#1fc16b" : "var(--text)" }}>
                        {fmtPct(r.div_yield, 1)}
                      </td>
                      <td style={{ ...TD_R, color: r.dist_low_pct != null && r.dist_low_pct <= 20 ? "#1fc16b" : "var(--text-muted)" }}>
                        {r.dist_low_pct != null ? `+${r.dist_low_pct.toFixed(1)}%` : "—"}
                      </td>
                      <td style={{ ...TD_R, color: "var(--text-muted)" }}>{fmtVol(r.avg_vol_90d)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ padding: "6px 12px", fontSize: 10, color: "var(--text-muted)", borderTop: "1px solid var(--sep, #1b2233)" }}>
              IVP = IV percentile 52w (green ≥60, amber ≥30) · Put Yield = BS ATM 30d estimate · green = ≥1.5%/mo
            </div>
          </div>
        )}
      </div>

      {/* ── RUN SCREENER / EMAIL ───────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, color: "var(--text)" }}>Full Screen + Email</h2>
          <p style={{ margin: "3px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
            Runs the wheel + swing screener and emails results to info@coreconsultingit.com.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {data && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>
              <div>Last run: {fmtTs(data.generated_at_utc)}</div>
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
            {running ? "Running…" : "▶ Run Screener + Email"}
          </button>
        </div>
      </div>

      {runErr && <Note tone="error" style={{ marginBottom: 14 }}>Run failed: {runErr}</Note>}

      {runResult && (
        <div style={{
          marginBottom: 14,
          padding: "12px 16px",
          border: `1px solid ${runResult.ok ? "rgba(31,193,107,0.25)" : "rgba(239,68,68,0.25)"}`,
          borderRadius: 8,
          background: runResult.ok ? "rgba(31,193,107,0.05)" : "rgba(239,68,68,0.05)",
          fontSize: 12,
        }}>
          <span style={{ fontWeight: 700, color: runResult.ok ? "#1fc16b" : "#ef4444" }}>
            Screener complete — {runResult.result?.run_date}
          </span>
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
          {runResult.stderr && runResult.stderr.trim() && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", color: "var(--text-muted)", fontSize: 11 }}>Show logs / errors</summary>
              <pre style={{
                marginTop: 6, padding: "8px 10px", background: "rgba(0,0,0,0.2)",
                borderRadius: 4, fontSize: 10, overflowX: "auto", whiteSpace: "pre-wrap",
                color: "var(--text-muted)", maxHeight: 200,
              }}>{runResult.stderr}</pre>
            </details>
          )}
        </div>
      )}

      {/* ── STORED CANDIDATES ─────────────────────────────────────────────── */}
      {err ? (
        <Note tone="error">Screener unavailable: {err}</Note>
      ) : loading ? (
        <Note>Loading last run results…</Note>
      ) : candidates.length === 0 ? (
        <Note>No stored results yet — click <strong>Run Screener + Email</strong> to fetch live data and run the screen.</Note>
      ) : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
            <Kpi label="Total screened" value={String(candidates.length)} />
            <Kpi label="Eligible" value={String(eligible.length)} accent="#1fc16b" />
            <Kpi label="Blocked" value={String(blocked.length)} accent="#ef4444" />
          </div>

          {eligible.length > 0 && (
            <Section title="Eligible Candidates">
              <CandidateTable rows={eligible} />
            </Section>
          )}
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

// ── sub-components ────────────────────────────────────────────────────────────

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
            <tr key={c.symbol}>
              <td style={{ ...TD, fontWeight: 700, color: c.eligible ? "#1fc16b" : "var(--text-muted)" }}>
                {c.symbol}
              </td>
              <td style={{ ...TD, color: "var(--text-dim)", textTransform: "capitalize" }}>{c.regime ?? "—"}</td>
              <td style={{ ...TD_R, color: ivpColour(c.iv_rank) }}>{fmtPct(c.iv_rank)}</td>
              <td style={TD_R}>{fmtPct(c.iv != null ? c.iv * 100 : null)}</td>
              <td style={TD_R}>{c.open_interest != null ? c.open_interest.toLocaleString() : "—"}</td>
              <td style={TD_R}>{fmtMoney(c.spread_usd)}</td>
              <td style={TD_R}>{fmtMoney(c.suggested_strike)}</td>
              <td style={TD_R}>{c.suggested_delta?.toFixed(2) ?? "—"}</td>
              <td style={TD_R}>{fmtMoney(c.suggested_premium)}</td>
              <td style={{ ...TD, maxWidth: 260 }}>
                {c.blocks.length > 0 && (
                  <span style={{ color: "#ef4444", fontSize: 11 }}>{c.blocks.join(" · ")}</span>
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
          background: "transparent", border: "none", cursor: "pointer",
          color: "var(--text)", fontWeight: 700, fontSize: 13,
          padding: "0 0 8px", display: "flex", alignItems: "center", gap: 6,
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
      fontSize: 13, padding: "12px 16px",
      color: tone === "error" ? "#ef4444" : "var(--text-muted)",
      background: tone === "error" ? "rgba(239,68,68,0.05)" : "transparent",
      border: `1px solid ${tone === "error" ? "rgba(239,68,68,0.2)" : "var(--sep, #1b2233)"}`,
      borderRadius: 8, ...style,
    }}>
      {children}
    </div>
  );
}
