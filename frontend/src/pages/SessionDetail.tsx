import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { PlotlyChart } from "../components/PlotlyChart";

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

const TABS = ["Overview", "Bars", "Decisions", "Fills", "Positions", "Charts"] as const;
type Tab = (typeof TABS)[number];

function extractStrategies(rs: unknown): StrategyEntry[] {
  if (!rs || typeof rs !== "object") return [];
  const s = (rs as Record<string, unknown>).strategies;
  return Array.isArray(s) ? (s as StrategyEntry[]) : [];
}

/**
 * Extract Plotly figure JSON dicts from the result_summary.
 *
 * Backend writes them to ``result_summary.charts`` as a
 * ``{chart_name: plotly_figure_dict}`` map. We accept either that
 * top-level shape or a per-strategy ``charts`` block so the engine
 * is free to attach charts at whichever scope is most natural.
 */
function extractCharts(rs: unknown, strategies: StrategyEntry[]): Array<{ key: string; title: string; figure: unknown }> {
  const out: Array<{ key: string; title: string; figure: unknown }> = [];
  if (rs && typeof rs === "object") {
    const top = (rs as Record<string, unknown>).charts;
    if (top && typeof top === "object") {
      for (const [name, fig] of Object.entries(top as Record<string, unknown>)) {
        out.push({ key: `session.${name}`, title: name, figure: fig });
      }
    }
  }
  for (const s of strategies) {
    const sc = (s as unknown as Record<string, unknown>).charts;
    if (sc && typeof sc === "object") {
      const sid = s.strategy_id ?? "—";
      for (const [name, fig] of Object.entries(sc as Record<string, unknown>)) {
        out.push({ key: `${sid}.${name}`, title: `${sid} · ${name}`, figure: fig });
      }
    }
  }
  return out;
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

  const charts = extractCharts(session.result_summary, strategies);

  const counts: Record<Tab, number> = {
    Overview: strategies.length,
    Bars: bars.length,
    Decisions: decisions.length,
    Fills: fills.length,
    Positions: positions.length,
    Charts: charts.length,
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

      <SessionSummaryHero
        session={session}
        strategies={strategies}
        fills={fills}
        decisions={decisions}
        bars={bars}
      />

      <AssumptionChips params={session.params} />

      <WhyNoOrdersBanner
        session={session}
        strategies={strategies}
        fills={fills}
        onJumpTo={(t) => setTab(t)}
      />

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
      {tab === "Charts" && <ChartsTab charts={charts} />}
    </div>
  );
}

/**
 * ChartsTab — renders every Plotly figure embedded in
 * result_summary.charts (or per-strategy .charts). Each figure is
 * rendered with the shared PlotlyChart component; new charts on the
 * backend show up here automatically.
 *
 * Empty-state mirrors the other tabs' tone — the trader sees an
 * explanation rather than a blank panel.
 */
function ChartsTab({
  charts,
}: {
  charts: Array<{ key: string; title: string; figure: unknown }>;
}) {
  if (charts.length === 0) {
    return (
      <div style={{ color: "var(--text-dim)", fontSize: 12 }}>
        No charts attached to this session. Strategies opt in by
        emitting Plotly figure JSON into result_summary.charts at
        session end — see the viz framework docs.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {charts.map((c) => (
        <div key={c.key}>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 4 }}>
            {c.title}
          </div>
          <PlotlyChart figure={c.figure as Record<string, unknown>} />
        </div>
      ))}
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

/**
 * SessionSummaryHero — narrative, trader-friendly summary at the top
 * of Session Detail. Replaces the data-tables-first layout with a
 * single block that answers "what ran, what happened, is there a
 * problem" without the trader having to scan five tabs.
 *
 * Verdict is one of: FIRED (≥1 fill) · NO_FIRES (decisions present
 * but none became orders) · NO_DECISIONS (bars seen but on_bar never
 * decided) · NO_BARS (data didn't reach strategy) · NOT_STARTED
 * (session not yet completed). Each verdict gets a colour + a one-
 * line action so the trader knows what to do.
 */
function SessionSummaryHero({
  session,
  strategies,
  fills,
  decisions,
  bars,
}: {
  session: Session;
  strategies: StrategyEntry[];
  fills: FillRow[];
  decisions: DecisionRow[];
  bars: BarRow[];
}) {
  const completed = (session.state ?? "").toLowerCase() === "completed";
  const fires = decisions.filter((d) => d.action.startsWith("fire-")).length;
  const skips = decisions.filter((d) => d.action.startsWith("skip-")).length;

  type Verdict = "FIRED" | "NO_FIRES" | "NO_DECISIONS" | "NO_BARS" | "NOT_STARTED";
  let verdict: Verdict;
  if (!completed) verdict = "NOT_STARTED";
  else if (fills.length > 0) verdict = "FIRED";
  else if (bars.length === 0) verdict = "NO_BARS";
  else if (decisions.length === 0) verdict = "NO_DECISIONS";
  else verdict = "NO_FIRES";

  const tone =
    verdict === "FIRED" ? { fg: "#1fc16b", bg: "rgba(31,193,107,0.10)", border: "rgba(31,193,107,0.35)" } :
    verdict === "NOT_STARTED" ? { fg: "var(--text-dim)", bg: "rgba(255,255,255,0.04)", border: "var(--border)" } :
    { fg: "#f59e0b", bg: "rgba(245,158,11,0.08)", border: "rgba(245,158,11,0.35)" };

  const label: Record<Verdict, string> = {
    FIRED: `✓ ${fills.length} fill${fills.length === 1 ? "" : "s"}`,
    NO_FIRES: "🟡 No fires (strategy decided, conditions didn't trigger)",
    NO_DECISIONS: "🟡 No decisions (bars arrived, on_bar never decided)",
    NO_BARS: "⚠ No bars delivered to strategy",
    NOT_STARTED: "⏳ Not yet completed",
  };
  const action: Record<Verdict, string> = {
    FIRED: "Review fills + positions tabs to validate execution.",
    NO_FIRES: "Open the Decisions tab to see the top skip reasons.",
    NO_DECISIONS: "Strategy may need a larger lookback to warm up — open the Bars tab to confirm bars arrived.",
    NO_BARS: "Likely causes: triggered pre-market (no intraday bars yet), wrong session date, or daemon running stale code. Try re-triggering after market open.",
    NOT_STARTED: "Mac worker hasn't picked up this request yet, or the run is still in flight.",
  };

  const params = (session.params || {}) as Record<string, unknown>;
  const strategyName = params.strategy as string | undefined;
  const symbolsArr = Array.isArray(params.symbols) ? (params.symbols as string[]) : [];

  // Wall clock summary — small + monospace.
  const enqAt = new Date(session.requested_at_utc);
  const compAt = session.completed_at_utc ? new Date(session.completed_at_utc) : null;
  const elapsedMs = compAt ? compAt.getTime() - enqAt.getTime() : null;
  const elapsedText = elapsedMs == null ? "in flight"
    : elapsedMs < 60_000 ? `${Math.round(elapsedMs / 1000)}s`
    : `${Math.floor(elapsedMs / 60_000)}m ${Math.round((elapsedMs % 60_000) / 1000)}s`;

  return (
    <div
      style={{
        padding: "14px 18px",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 8,
        marginBottom: 14,
      }}
    >
      <div style={{ display: "flex", gap: 16, alignItems: "baseline", flexWrap: "wrap" }}>
        <div style={{
          fontSize: 16, fontWeight: 700, color: tone.fg, letterSpacing: "0.02em",
        }}>
          {label[verdict]}
        </div>
        {strategyName && (
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            <span style={{ fontFamily: "monospace", fontWeight: 600 }}>{strategyName}</span>
            {symbolsArr.length > 0 && (
              <>
                {" on "}
                <span style={{ fontFamily: "monospace" }}>{symbolsArr.join(", ")}</span>
              </>
            )}
          </div>
        )}
        <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", fontFamily: "monospace" }}>
          {enqAt.toLocaleTimeString()} → {compAt ? compAt.toLocaleTimeString() : "…"} · {elapsedText}
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 12, marginTop: 14, marginBottom: 10,
        }}
      >
        <SummaryStat label="Bars seen" value={bars.length} />
        <SummaryStat
          label="Decisions"
          value={decisions.length}
          sub={decisions.length > 0 ? `${fires} fire · ${skips} skip` : undefined}
        />
        <SummaryStat label="Fills" value={fills.length} highlight={fills.length > 0} />
        <SummaryStat label="Strategies" value={strategies.length} />
      </div>

      <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.45 }}>
        {action[verdict]}
      </div>
    </div>
  );
}

function SummaryStat({
  label, value, sub, highlight,
}: {
  label: string;
  value: number;
  sub?: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <div style={{
        fontSize: 10, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 22, fontWeight: 700, fontFamily: "monospace",
        color: highlight ? "#1fc16b" : "var(--text)",
        marginTop: 2,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

/**
 * AssumptionChips — surface the strategy run's most diagnostic config
 * fields as chips directly under the session header so the trader
 * never has to scroll the JSON params blob to know what assumptions
 * the run was made under.
 *
 * Selection is heuristic: we cherry-pick known-important fields and
 * fall back gracefully when a field is missing. Unknown / one-off
 * params still live in the full ParamsCard on the Overview tab.
 */
function AssumptionChips({ params }: { params: unknown }) {
  if (!params || typeof params !== "object") return null;
  const p = params as Record<string, unknown>;
  const chips: { label: string; value: string; tone?: "warn" | "info" | "ok" }[] = [];
  const push = (label: string, value: unknown, tone?: "warn" | "info" | "ok") => {
    if (value === undefined || value === null || value === "") return;
    chips.push({ label, value: String(value), tone });
  };
  push("strategy", p.strategy, "info");
  if (Array.isArray(p.symbols)) {
    push("symbols", (p.symbols as unknown[]).join(", "));
  }
  push("session_date", p.session_date ?? p.date);
  push("placement_mode", p.placement_mode,
       p.placement_mode === "auto" ? "warn" : "info");
  push("lookback_days", p.lookback_days ?? p.lookback);
  push("capital_usd", p.capital_usd ?? p.capital);
  push("provider", p.provider);
  if (p.use_regime_filter !== undefined) {
    push("regime_filter", p.use_regime_filter ? "on" : "off",
         p.use_regime_filter === false ? "warn" : undefined);
  }
  if (p._llm_gate !== undefined) {
    push("llm_gate", p._llm_gate ? "on" : "off");
  }
  if (chips.length === 0) return null;
  return (
    <div
      style={{
        display: "flex", flexWrap: "wrap", gap: 6,
        marginBottom: 12,
      }}
    >
      {chips.map((c, i) => (
        <span
          key={i}
          style={{
            display: "inline-flex", gap: 6, alignItems: "baseline",
            fontSize: 11, lineHeight: 1.4,
            padding: "3px 9px", borderRadius: 999,
            background: c.tone === "warn" ? "rgba(245,158,11,0.10)"
              : c.tone === "ok" ? "rgba(31,193,107,0.10)"
              : "var(--bg-hover, rgba(255,255,255,0.04))",
            border: `1px solid ${c.tone === "warn" ? "rgba(245,158,11,0.30)"
              : c.tone === "ok" ? "rgba(31,193,107,0.30)"
              : "var(--border)"}`,
          }}
        >
          <span style={{ color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em", fontSize: 9 }}>
            {c.label}
          </span>
          <span style={{
            fontFamily: "monospace",
            color: c.tone === "warn" ? "#f59e0b"
              : c.tone === "ok" ? "#1fc16b"
              : "var(--text)",
          }}>
            {c.value}
          </span>
        </span>
      ))}
    </div>
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

/**
 * WhyNoOrdersBanner — shown above the tabs when the session completed
 * but emitted 0 fills. Without this the operator sees blank tabs and
 * has no idea whether the strategy was starved of data, blocked by a
 * filter, or just chose WAIT for every bar. We diagnose each
 * strategy and call out the most likely culprit so they don't have
 * to crawl the Decisions tab to find out.
 *
 * Render rules:
 *   - Hidden when fills.length > 0 (a normal run).
 *   - Hidden when the session isn't terminal (still running / failed
 *     → error banner above covers that). Match is case-insensitive
 *     because the backend ships ``Completed`` (PascalCase) while
 *     other call sites use ``completed``.
 *   - Per strategy verdict ordered: no_bars > no_decisions >
 *     all_wait > unknown.
 */
function WhyNoOrdersBanner({
  session,
  strategies,
  fills,
  onJumpTo,
}: {
  session: Session;
  strategies: StrategyEntry[];
  fills: FillRow[];
  onJumpTo: (tab: Tab) => void;
}) {
  if (fills.length > 0) return null;
  if ((session.state ?? "").toLowerCase() !== "completed") return null;
  if (strategies.length === 0) return null;

  // Aggregate per-strategy diagnosis.
  const items = strategies.map((s) => {
    const bars = (s.bars_seen ?? []).length;
    const decisions = s.decisions ?? [];
    const reasonCounts = new Map<string, number>();
    for (const d of decisions) {
      reasonCounts.set(d.reason, (reasonCounts.get(d.reason) ?? 0) + 1);
    }
    const topReason = Array.from(reasonCounts.entries())
      .sort((a, b) => b[1] - a[1])[0];

    let verdict: "no_bars" | "no_decisions" | "all_wait" | "unknown" = "unknown";
    let detail = "";
    if (bars === 0) {
      verdict = "no_bars";
      detail = "Strategy never saw a bar. Likely: source feed misconfigured, " +
        "symbols rejected (FX vs equity mapping), or data window empty for the session date.";
    } else if (decisions.length === 0) {
      verdict = "no_decisions";
      detail = `Saw ${bars} bars but logged 0 decisions. Likely: warmup not reached ` +
        "(strategy needs more lookback than provided), or on_bar errored silently.";
    } else if (topReason && /wait|hold|skip|warm/i.test(topReason[0])) {
      verdict = "all_wait";
      detail = `${decisions.length} decisions, top reason: "${topReason[0]}" ` +
        `(${topReason[1]}×). Strategy ran cleanly but conditions never triggered an entry.`;
    } else {
      verdict = "unknown";
      detail = `${bars} bars, ${decisions.length} decisions, 0 fills. ` +
        "Check Decisions tab for the actual reasoning chain.";
    }
    return { sid: s.strategy_id ?? "—", bars, decisions: decisions.length, verdict, detail, topReason };
  });

  return (
    <div
      style={{
        padding: "10px 14px",
        background: "rgba(245,158,11,0.08)",
        border: "1px solid rgba(245,158,11,0.35)",
        borderRadius: 6,
        marginBottom: 16,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "#f59e0b" }}>
        Why no orders?
      </div>
      <ul style={{ margin: 0, padding: "0 0 0 18px", fontSize: 12, color: "var(--text)" }}>
        {items.map((it, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            <span style={{ fontFamily: "monospace", color: "var(--text-dim)" }}>
              {it.sid}
            </span>
            {" — "}
            <span style={{
              color: it.verdict === "all_wait" ? "var(--text-dim)" : "#f59e0b",
              fontWeight: 500,
            }}>
              {it.verdict.replace("_", " ")}
            </span>
            {": "}
            {it.detail}
          </li>
        ))}
      </ul>
      <div style={{ marginTop: 8, fontSize: 11 }}>
        <button
          onClick={() => onJumpTo("Decisions")}
          style={{
            background: "transparent", border: "1px solid var(--border)",
            color: "var(--text-dim)", padding: "3px 10px", borderRadius: 4,
            fontSize: 11, cursor: "pointer", marginRight: 6,
          }}
        >
          Open Decisions tab →
        </button>
        <button
          onClick={() => onJumpTo("Bars")}
          style={{
            background: "transparent", border: "1px solid var(--border)",
            color: "var(--text-dim)", padding: "3px 10px", borderRadius: 4,
            fontSize: 11, cursor: "pointer",
          }}
        >
          Open Bars tab →
        </button>
      </div>
    </div>
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
      headers={["bar_ts", "strategy_id", "symbol", "action", "reason", "detail", "→"]}
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
          <td style={{ ...td, whiteSpace: "nowrap" }}>
            {d.action.startsWith("fire-") && d.strategy_id ? (
              <Link
                to={`/oms?strategy=${encodeURIComponent(d.strategy_id)}`}
                title="Open OMS filtered to orders from this strategy"
                style={{
                  fontSize: 10, color: "var(--text-muted)",
                  textDecoration: "none",
                  borderBottom: "1px dotted var(--text-muted)",
                }}
              >
                OMS
              </Link>
            ) : ""}
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
