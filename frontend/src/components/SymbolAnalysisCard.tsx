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
 * v1 reads what's already on the CompareRow (technical lens is fully
 * populated by compare.py). The fundamental lens carries existing
 * `row.fundamentals` plus placeholders for the Track 2 verdicts
 * (quality_snapshot / valuation / dividend / entry_timing /
 * long_term_grade) — those need backend wiring next: either Mac
 * worker pushes the orchestrator output or the .NET API gains a
 * /api/symbol/{ticker}/analysis endpoint that proxies the Python
 * helper.
 *
 * primary_horizon_recommendation is computed client-side from the
 * technical bucket + conviction + RR gate. When the fundamental
 * verdicts land, the same rules in symbol_analysis_card.py
 * `_recommend_horizon()` apply.
 */
import type React from "react";
import type { CompareRow } from "../api/types";

type Horizon =
  | "LONG_TERM_HOLD"
  | "MEDIUM_TERM_ADD"
  | "SHORT_TERM_TRADE"
  | "AVOID"
  | "WATCH"
  | "INSUFFICIENT";

function recommendHorizon(row: CompareRow): { token: Horizon; reason: string } {
  const bucket = row.bucket;
  const conviction = (row as unknown as { conviction?: string }).conviction;
  const rrGatePassed = ((row as unknown as { rr_gate?: { passed?: boolean } }).rr_gate)?.passed;
  if (bucket === "AVOID") {
    return {
      token: "AVOID",
      reason: "Technical AVOID — fundamentals not yet wired, full card pending.",
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
      reason: "Technical WAIT — mixed signals; re-check next signal cycle.",
    };
  }
  return {
    token: "INSUFFICIENT",
    reason: "Fundamentals not yet wired into the row — long-term verdict pending backend integration.",
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

export function SymbolAnalysisCard(props: { row: CompareRow }) {
  const { row } = props;
  const { token, reason } = recommendHorizon(row);

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
        <FundamentalColumn row={row} />
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
  const earnings = (row as unknown as { earnings_suppressed?: boolean; earnings_proximity_days?: number }).earnings_suppressed;
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

function FundamentalColumn(props: { row: CompareRow }) {
  const { row } = props;
  const f = row.fundamentals;

  return (
    <div style={columnStyle}>
      <div style={lensHeaderStyle}>FUNDAMENTAL</div>

      {f?.expense_ratio_pct != null && (
        <Kv label="Expense ratio" value={`${fmt(f.expense_ratio_pct, 2)}%`} />
      )}
      {f?.dividend_yield_pct != null && (
        <Kv label="Dividend yield" value={`${fmt(f.dividend_yield_pct, 2)}%`} />
      )}
      {f?.aum_usd != null && (
        <Kv label="AUM" value={`$${fmtCompact(f.aum_usd)}`} />
      )}
      {row.valuation_flag?.flag && row.valuation_flag.flag !== "n/a" && (
        <Kv label="Valuation flag" value={
          <span style={valuationStyle(row.valuation_flag.flag)}>
            {row.valuation_flag.flag.toUpperCase()}
          </span>
        } />
      )}

      <div style={track2BoxStyle}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
          Track 2 (Compounder) — pending backend wire
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>
          Quality ★ · Valuation ATTRACTIVE/FAIR/STRETCHED ·
          Dividend STRONG/STEADY/UNDER_PRESSURE ·
          Entry Timing ACCUMULATE/WATCH/NEUTRAL ·
          Long-term grade A–F.
          <br />
          Compute via MCP <code>get_symbol_analysis</code> or wire
          <code>build_symbol_analysis_card()</code> into the worker push
          to make these per-row.
        </div>
      </div>
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

function valuationStyle(flag: string): React.CSSProperties {
  const colour =
    flag === "cheap" ? "#1f9e6e" :
    flag === "expensive" ? "#a83a3a" :
    "#666";
  return { fontWeight: 600, color: colour };
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
