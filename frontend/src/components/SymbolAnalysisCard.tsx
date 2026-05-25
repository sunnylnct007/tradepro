/**
 * Symbol Analysis Card — the platform-level unified view.
 *
 *   "the whole idea of tradepro is to have a platform to show technical
 *    and fundamental analysis"  — user, 2026-05-24
 *
 * One card per symbol with both lenses side-by-side:
 *
 *   ┌─ TECHNICAL ──────────────┐  ┌─ FUNDAMENTAL ────────────┐
 *   │ bucket · conviction      │  │ Quality ★★★★★            │
 *   │ exit / RR / sizing       │  │ Valuation ATTRACTIVE     │
 *   │ IBKR bracket instructions│  │ Dividend STEADY          │
 *   │ news / earnings context  │  │ Long-term grade A        │
 *   └──────────────────────────┘  └──────────────────────────┘
 *               ↓                              ↓
 *      primary_horizon_recommendation: LONG_TERM_HOLD
 *
 * Reads:
 *   - TECHNICAL block from the CompareRow already on the page
 *     (bucket / conviction / exit / RR / sizing / IBKR / news /
 *     earnings) — no extra fetch.
 *   - FUNDAMENTAL block + primary_horizon_recommendation from the
 *     Python sidecar GET /symbol/{ticker}/analysis (tradepro-analysis-server).
 *     This is the real Track 2 verdicts (Quality ★ / Valuation /
 *     Dividend / Entry Timing / A-F long-term grade).
 *
 * Graceful degradation: when the sidecar is unreachable (not running
 * locally; production not yet wired through .NET), the card falls
 * back to a client-side primary_horizon_recommendation derived from
 * the technical lens alone, and the fundamental panel renders a
 * "sidecar offline — start with `uv run tradepro-analysis-server`"
 * hint. The rest of the card still works.
 */
import { useEffect, useState } from "react";
import type React from "react";
import { config } from "../config";
import { getIdToken } from "../firebase";
import type { CompareRow } from "../api/types";

type Horizon =
  | "LONG_TERM_HOLD"
  | "MEDIUM_TERM_ADD"
  | "SHORT_TERM_TRADE"
  | "AVOID"
  | "WATCH"
  | "INSUFFICIENT";

interface AnalysisCardEnvelope {
  symbol: string;
  fetched_at: string;
  technical: Record<string, unknown> | null;
  fundamental: {
    quality_snapshot?: {
      stars?: number | null;
      stars_display?: string | null;
      overall_score?: number | null;
      missing_metrics?: string[];
    } | null;
    valuation?: {
      overall_verdict?: string | null;
      rationale?: string | null;
    } | null;
    dividend?: {
      verdict?: string | null;
      current_yield_pct?: number | null;
      five_year_cagr_pct?: number | null;
      payout_ratio?: number | null;
      rationale?: string | null;
    } | null;
    entry_timing?: {
      verdict?: string | null;
      signals_passing?: number | null;
      quality_source?: string | null;
      long_term_grade?: string | null;
      rationale?: string | null;
    } | null;
    long_term_grade?: {
      grade?: string | null;
      score?: number | null;
      positives?: string[];
      negatives?: string[];
    } | null;
  } | null;
  primary_horizon_recommendation: Horizon;
  rationale: string;
  warnings: string[];
  _source?: string;
  compare_row_source?: string | null;
}

type Loaded =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ok"; envelope: AnalysisCardEnvelope }
  | { state: "error"; message: string };

function fallbackHorizon(row: CompareRow): { token: Horizon; reason: string } {
  const bucket = row.bucket;
  const conviction = (row as unknown as { conviction?: string }).conviction;
  const rrGatePassed = ((row as unknown as { rr_gate?: { passed?: boolean } }).rr_gate)?.passed;
  if (bucket === "AVOID") {
    return {
      token: "AVOID",
      reason: "Technical AVOID — sidecar offline, fundamentals not loaded.",
    };
  }
  if (
    bucket === "BUY" &&
    (conviction === "HIGH" || conviction === "MEDIUM") &&
    rrGatePassed
  ) {
    return {
      token: "SHORT_TERM_TRADE",
      reason: `Technical BUY at conviction ${conviction}, RR gate passed — swing/intraday entry.`,
    };
  }
  if (bucket === "WAIT") {
    return {
      token: "WATCH",
      reason: "Technical WAIT — sidecar offline; long-term verdict needs both lenses.",
    };
  }
  return {
    token: "INSUFFICIENT",
    reason: "Sidecar offline — start with `uv run tradepro-analysis-server` to enable Track 2.",
  };
}

const HORIZON_COLOUR: Record<Horizon, string> = {
  LONG_TERM_HOLD: "#1f9e6e",
  MEDIUM_TERM_ADD: "#3b82a4",
  SHORT_TERM_TRADE: "#5b3aa8",
  AVOID: "#a83a3a",
  WATCH: "#a07e1c",
  INSUFFICIENT: "#777",
};

const HORIZON_LABEL: Record<Horizon, string> = {
  LONG_TERM_HOLD: "LONG-TERM HOLD",
  MEDIUM_TERM_ADD: "MEDIUM-TERM ADD",
  SHORT_TERM_TRADE: "SHORT-TERM TRADE",
  AVOID: "AVOID",
  WATCH: "WATCH",
  INSUFFICIENT: "DATA PENDING",
};

export function SymbolAnalysisCard(props: { row: CompareRow; universe?: string | null }) {
  const { row, universe } = props;
  const [analysis, setAnalysis] = useState<Loaded>({ state: "idle" });

  useEffect(() => {
    let cancelled = false;
    setAnalysis({ state: "loading" });
    const params = new URLSearchParams();
    if (universe) params.set("universe", universe);
    // Skip the multi-year long-term fetch by default — it's the
    // slowest call (~3 yfinance round-trips). The user can opt in
    // via a follow-up "Refresh long-term" button.
    params.set("skip_long_term", "true");
    const ticker = encodeURIComponent(row.symbol);
    // Direct: GET /symbol/{ticker}/analysis (sidecar shape, dev only).
    // Proxied: GET /api/symbol-analysis/{ticker} (.NET auth-enforced).
    const path = config.analysisDirect
      ? `/symbol/${ticker}/analysis`
      : `/api/symbol-analysis/${ticker}`;
    const url = `${config.analysisBaseUrl}${path}?${params}`;
    (async () => {
      try {
        const headers: Record<string, string> = {};
        if (!config.analysisDirect) {
          const token = await getIdToken();
          if (token) headers.Authorization = `Bearer ${token}`;
        }
        const resp = await fetch(url, { headers });
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
        const envelope = (await resp.json()) as AnalysisCardEnvelope;
        if (!cancelled) setAnalysis({ state: "ok", envelope });
      } catch (e) {
        if (!cancelled) setAnalysis({ state: "error", message: String(e) });
      }
    })();
    return () => { cancelled = true; };
  }, [row.symbol, universe]);

  // Pick the canonical horizon + rationale: prefer the server-computed
  // one when available, fall back to client-side derivation otherwise.
  let token: Horizon;
  let reason: string;
  if (analysis.state === "ok") {
    token = analysis.envelope.primary_horizon_recommendation;
    reason = analysis.envelope.rationale;
  } else {
    const fb = fallbackHorizon(row);
    token = fb.token;
    reason = fb.reason;
  }

  const envelope = analysis.state === "ok" ? analysis.envelope : null;

  return (
    <section style={cardStyle}>
      <header style={headerStyle}>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            Symbol Analysis · both lenses
          </div>
          <h3 style={{ margin: "4px 0 0", fontSize: 18 }}>{row.symbol}</h3>
        </div>
        <div style={pillStyle(HORIZON_COLOUR[token])}>
          {HORIZON_LABEL[token]}
        </div>
      </header>

      <p style={rationaleStyle}>{reason}</p>

      <div style={columnsStyle}>
        <TechnicalColumn row={row} />
        <FundamentalColumn row={row} envelope={envelope} loadingState={analysis.state} />
      </div>
    </section>
  );
}

function TechnicalColumn(props: { row: CompareRow }) {
  const { row } = props;
  const conviction = (row as unknown as { conviction?: string; conviction_reason?: string }).conviction;
  const exit = (row as unknown as { exit?: ExitBlock }).exit;
  const rrGate = (row as unknown as { rr_gate?: RRGate }).rr_gate;
  const sizing = (row as unknown as { sizing?: Sizing }).sizing;
  const ibkr = (row as unknown as { ibkr_order_instructions?: IBKRInstr }).ibkr_order_instructions;
  const news = (row as unknown as { news_context?: NewsContextBlock }).news_context;
  const earnings = (row as unknown as { earnings_suppressed?: boolean }).earnings_suppressed;
  const earningsDays = (row as unknown as { earnings_proximity_days?: number }).earnings_proximity_days;

  return (
    <div style={columnStyle}>
      <div style={lensHeaderStyle}>TECHNICAL</div>

      <Kv label="Bucket" value={
        <span style={bucketStyle(row.bucket || "")}>{row.bucket || "—"}</span>
      } />
      <Kv label="Reason" value={row.bucket_reason || "—"} multiline />

      {conviction && (
        <Kv label="Conviction" value={
          <span style={{ fontWeight: 600 }}>{conviction}</span>
        } />
      )}

      {exit && (exit.stop_loss || exit.take_profit) && (
        <Kv label="Exit" value={
          <span>
            stop {fmt(exit.stop_loss)} · target {fmt(exit.take_profit)}
            {exit.time_exit ? ` · ${exit.time_exit}` : ""}
          </span>
        } />
      )}

      {rrGate && (
        <Kv label="RR gate" value={
          <span style={{ color: rrGate.passed ? "#1f9e6e" : "#a83a3a" }}>
            {rrGate.passed ? "PASS" : "FAIL"} ({fmt(rrGate.rr, 2)}×)
          </span>
        } />
      )}

      {sizing && (
        <Kv label="Sizing" value={
          <span>{fmt(sizing.shares, 0)} sh · £{fmt(sizing.notional_gbp, 0)}</span>
        } />
      )}

      {ibkr && (
        <Kv label="IBKR" value={
          <code style={codeStyle}>
            {ibkr.direction} {ibkr.quantity} @ {fmt(ibkr.entry_price)} ·
            STOP {fmt(ibkr.stop_loss)} · TGT {fmt(ibkr.take_profit)}
          </code>
        } multiline />
      )}

      {earnings && (
        <Kv label="Earnings" value={
          <span style={{ color: "#a07e1c" }}>
            ⚠ Suppressed — earnings in {earningsDays}d
          </span>
        } />
      )}

      {news && (
        <Kv label="News" value={
          <span>
            sentiment {fmt(news.sentiment_score, 2)} ·{" "}
            {news.headline_count} headlines
            {news.suppress ? " · ⚠ suppressed" : ""}
          </span>
        } />
      )}
    </div>
  );
}

function FundamentalColumn(props: {
  row: CompareRow;
  envelope: AnalysisCardEnvelope | null;
  loadingState: Loaded["state"];
}) {
  const { row, envelope, loadingState } = props;
  const f = row.fundamentals;

  const qs = envelope?.fundamental?.quality_snapshot;
  const val = envelope?.fundamental?.valuation;
  const div = envelope?.fundamental?.dividend;
  const timing = envelope?.fundamental?.entry_timing;
  const grade = envelope?.fundamental?.long_term_grade?.grade;

  return (
    <div style={columnStyle}>
      <div style={lensHeaderStyle}>FUNDAMENTAL</div>

      {/* Existing CompareRow fundamentals — always present on equity/ETF rows */}
      {f?.expense_ratio_pct != null && (
        <Kv label="Expense ratio" value={`${fmt(f.expense_ratio_pct, 2)}%`} />
      )}
      {f?.dividend_yield_pct != null && (
        <Kv label="Dividend yield" value={`${fmt(f.dividend_yield_pct, 2)}%`} />
      )}
      {f?.aum_usd != null && (
        <Kv label="AUM" value={`$${fmtCompact(f.aum_usd)}`} />
      )}

      {/* Track 2 verdicts — from the sidecar */}
      {loadingState === "loading" && (
        <div style={{ ...track2BoxStyle, color: "var(--text-muted)", fontSize: 11 }}>
          Loading Track 2 …
        </div>
      )}

      {loadingState === "ok" && (
        <>
          {qs?.stars != null && (
            <Kv label="Quality" value={
              <span style={{ fontWeight: 600 }}>
                {qs.stars_display ?? "★".repeat(Math.round(qs.stars))} ({qs.stars}/5)
              </span>
            } />
          )}
          {val?.overall_verdict && val.overall_verdict !== "UNKNOWN" && (
            <Kv label="Valuation" value={
              <span style={valuationVerdictStyle(val.overall_verdict)}>
                {val.overall_verdict}
              </span>
            } />
          )}
          {div?.verdict && div.verdict !== "NONE" && (
            <Kv label="Dividend" value={
              <span style={{ fontWeight: 600 }}>
                {div.verdict}
                {div.current_yield_pct != null
                  ? ` · ${fmt(div.current_yield_pct, 2)}%`
                  : ""}
                {div.five_year_cagr_pct != null
                  ? ` · ${fmt(div.five_year_cagr_pct, 1)}% 5y CAGR`
                  : ""}
              </span>
            } />
          )}
          {timing?.verdict && (
            <Kv label="Entry timing" value={
              <span style={entryTimingStyle(timing.verdict)}>
                {timing.verdict}
                {timing.quality_source ? ` (via ${timing.quality_source})` : ""}
              </span>
            } />
          )}
          {grade && (
            <Kv label="Long-term grade" value={
              <span style={gradeStyle(grade)}>{grade}</span>
            } />
          )}
        </>
      )}

      {loadingState === "error" && (
        <div style={track2BoxStyle}>
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
            Track 2 sidecar offline
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>
            Start it locally with{" "}
            <code style={codeStyle}>uv run tradepro-analysis-server</code>
            {" "}then refresh. Default port 8002; override via
            <code style={codeStyle}>VITE_ANALYSIS_BASE_URL</code>.
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// helpers
// ──────────────────────────────────────────────────────────────────

function Kv(props: {
  label: string;
  value: React.ReactNode;
  multiline?: boolean;
}) {
  return (
    <div style={{
      display: "flex",
      gap: 8,
      fontSize: 13,
      lineHeight: 1.5,
      flexDirection: props.multiline ? "column" : "row",
      alignItems: props.multiline ? "stretch" : "baseline",
    }}>
      <span style={{
        minWidth: 92,
        color: "var(--text-muted)",
        fontSize: 11,
        textTransform: "uppercase",
        letterSpacing: 0.5,
      }}>
        {props.label}
      </span>
      <span style={{ flex: 1 }}>{props.value}</span>
    </div>
  );
}

function fmt(x: number | null | undefined, dp = 2): string {
  if (x == null) return "—";
  return x.toFixed(dp);
}

function fmtCompact(x: number): string {
  if (x >= 1e9) return `${(x / 1e9).toFixed(1)}B`;
  if (x >= 1e6) return `${(x / 1e6).toFixed(0)}M`;
  if (x >= 1e3) return `${(x / 1e3).toFixed(0)}K`;
  return `${x.toFixed(0)}`;
}

function bucketStyle(bucket: string): React.CSSProperties {
  const colour =
    bucket === "BUY" ? "#1f9e6e" :
    bucket === "WAIT" ? "#a07e1c" :
    bucket === "AVOID" ? "#a83a3a" :
    "#777";
  return {
    fontWeight: 700,
    color: colour,
  };
}

function valuationVerdictStyle(v: string): React.CSSProperties {
  const colour =
    v === "ATTRACTIVE" ? "#1f9e6e" :
    v === "STRETCHED" ? "#a83a3a" :
    "#666";
  return { fontWeight: 700, color: colour };
}

function entryTimingStyle(v: string): React.CSSProperties {
  const colour =
    v === "ACCUMULATE" ? "#1f9e6e" :
    v === "NEUTRAL" ? "#666" :
    "#a07e1c";
  return { fontWeight: 700, color: colour };
}

function gradeStyle(g: string): React.CSSProperties {
  const colour =
    g === "A" ? "#1f9e6e" :
    g === "B" ? "#3b82a4" :
    g === "C" ? "#a07e1c" :
    "#a83a3a";
  return {
    fontWeight: 700,
    color: colour,
    fontSize: 16,
  };
}

function pillStyle(bg: string): React.CSSProperties {
  return {
    padding: "6px 12px",
    borderRadius: 999,
    background: bg,
    color: "#fff",
    fontWeight: 700,
    fontSize: 11,
    letterSpacing: 0.5,
  };
}

const cardStyle: React.CSSProperties = {
  background: "var(--surface-1, #fff)",
  border: "1px solid var(--border-1, #e5e5e5)",
  borderRadius: 8,
  padding: 16,
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 12,
};

const rationaleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 13,
  lineHeight: 1.5,
  color: "var(--text-muted)",
};

const columnsStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
  gap: 16,
};

const columnStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: 12,
  background: "var(--surface-2, #f8f8f8)",
  borderRadius: 6,
};

const lensHeaderStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: 1,
  color: "var(--text-muted)",
  marginBottom: 4,
};

const codeStyle: React.CSSProperties = {
  fontSize: 11,
  background: "var(--surface-3, #efefef)",
  padding: "2px 6px",
  borderRadius: 4,
  fontFamily: "var(--font-mono, monospace)",
};

const track2BoxStyle: React.CSSProperties = {
  marginTop: 8,
  padding: 8,
  background: "var(--surface-3, #efefef)",
  borderRadius: 4,
  border: "1px dashed var(--border-1, #ccc)",
};

// ──────────────────────────────────────────────────────────────────
// Inline types for fields not (yet) on the generated CompareRow type.
// These mirror what compare.py decorates onto each row — once
// types.ts is regenerated they'll move there.
// ──────────────────────────────────────────────────────────────────

interface ExitBlock {
  stop_loss: number | null;
  take_profit: number | null;
  time_exit?: string | null;
}
interface RRGate {
  passed: boolean;
  rr: number | null;
  reason?: string;
}
interface Sizing {
  shares: number;
  notional_gbp: number;
  notional_usd?: number;
}
interface IBKRInstr {
  direction: string;
  quantity: number;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
}
interface NewsContextBlock {
  sentiment_score: number | null;
  headline_count: number;
  suppress: boolean;
}
