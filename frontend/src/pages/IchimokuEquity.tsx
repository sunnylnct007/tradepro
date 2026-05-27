/**
 * IchimokuEquity — dedicated page for the ichimoku_equity strategy.
 *
 * Shows (all on one screen, collapsible CockpitCards):
 *   1. Signal Grid    — latest session decisions
 *   2. Ichimoku Charts — Plotly cloud charts per symbol
 *   3. Positions       — T212 demo positions filtered to signal symbols
 *   4. OMS Orders      — order lifecycle gantt + table
 *   5. Session Queue   — last 10 sessions for this strategy
 *
 * Data refreshes every 30 s via setInterval; OMS events also trigger
 * an immediate orders reload via useOmsEvents.
 */
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import type { OmsOrderRow } from "../api/client";
import { config } from "../config";
import { CockpitCard } from "../components/CockpitCard";
import { EquityPipelineCard } from "../components/EquityPipelineCard";
import { PlotlyChart } from "../components/PlotlyChart";
import { buildOrderLifecycleFigure } from "../viz/orderLifecycle";
import { useOmsEvents } from "../hooks/useOmsEvents";

const STRATEGY_ID = "ichimoku_equity";
// Default universe the in-page "Run Session" button scans against.
// large_50 = trader hand-curated 50-name sleeve. Quick (~1 min cold).
// Pickable later if needed; for now this is the demo-friendly choice.
const RUN_UNIVERSE = "large_50";

// ── colour palette ────────────────────────────────────────────────────────────
const C = {
  green:  "#1fc16b",
  red:    "#ef4444",
  amber:  "#d97706",
  blue:   "#4f8cff",
  grey:   "#9ca3af",
  muted:  "var(--text-muted)",
};

const STATE_COLOR: Record<string, string> = {
  PENDING_APPROVAL: "#f59e0b",
  SUBMITTED:        C.blue,
  WORKING:          C.blue,
  PARTIALLY_FILLED: "#06A77D",
  FILLED:           C.green,
  CANCELLED:        C.grey,
  EXPIRED:          C.grey,
  REJECTED:         C.red,
};

// ── tiny shared helpers ───────────────────────────────────────────────────────

function Pill({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 999,
      fontSize: 11,
      fontWeight: 700,
      color,
      background: bg,
      letterSpacing: "0.04em",
    }}>
      {label}
    </span>
  );
}

function ActionPill({ action }: { action: string }) {
  const a = action.toUpperCase();
  if (a === "BUY")  return <Pill label="BUY"  color={C.green} bg="rgba(31,193,107,0.14)" />;
  if (a === "SELL") return <Pill label="SELL" color={C.red}   bg="rgba(239,68,68,0.14)" />;
  return <Pill label={a} color={C.grey} bg="rgba(156,163,175,0.14)" />;
}

function StatePill({ state }: { state: string }) {
  const color = STATE_COLOR[state] ?? C.grey;
  return (
    <Pill
      label={state}
      color={color}
      bg={color + "22"}
    />
  );
}

function SessionStatePill({ state }: { state: string }) {
  const s = state.toLowerCase();
  if (s === "completed") return <Pill label="Completed" color={C.green} bg="rgba(31,193,107,0.14)" />;
  if (s === "failed")    return <Pill label="Failed"    color={C.red}   bg="rgba(239,68,68,0.14)" />;
  if (s === "claimed")   return <Pill label="Running"   color={C.amber} bg="rgba(217,119,6,0.14)" />;
  return <Pill label={state} color={C.grey} bg="rgba(156,163,175,0.14)" />;
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function fmtPct(v: number): string {
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

// ── types ─────────────────────────────────────────────────────────────────────

interface Decision {
  barTs: string | null;
  symbol: string;
  action: string;
  reason: string;
  detail: Record<string, unknown>;
}

interface SymbolResult {
  symbol: string;
  ok: boolean;
  data_window_start?: string | null;
}

interface ResultSummary {
  strategy?: string;
  requestId?: string;
  completedAtUtc?: string | null;
  barsSeen?: number;
  decisions?: Decision[];
  charts?: Record<string, unknown>;
  results?: SymbolResult[];
}

interface SessionRow {
  request_id: string;
  kind: string;
  params: unknown;
  state: string;
  requested_at_utc: string;
  claimed_at_utc: string | null;
  claimed_by?: string | null;
  completed_at_utc: string | null;
  result_summary: unknown;
  error: string | null;
}

interface T212Position {
  ticker: string;
  yahooSymbol?: string;
  quantity: number;
  averagePricePaid: number;
  currentPrice: number;
  unrealisedPct: number;
  unrealisedAbs: number;
  currency?: string;
}

interface T212Response {
  enabled: boolean;
  mode: string;
  positionCount: number;
  positions: T212Position[];
  error?: string | null;
}

// ── helpers to parse session rows ─────────────────────────────────────────────

function matchesStrategy(row: SessionRow): boolean {
  const params = row.params as Record<string, unknown> | null | undefined;
  const rs = row.result_summary as Record<string, unknown> | null | undefined;
  const pStrat = String(params?.strategy ?? "");
  const rStrat = String(rs?.strategy ?? "");
  return (
    pStrat === STRATEGY_ID ||
    pStrat.includes(STRATEGY_ID) ||
    rStrat === STRATEGY_ID
  );
}

function getResultSummary(row: SessionRow): ResultSummary | null {
  if (!row.result_summary) return null;
  return row.result_summary as ResultSummary;
}

// ── main component ────────────────────────────────────────────────────────────

export function IchimokuEquity() {
  const [allSessions, setAllSessions] = useState<SessionRow[]>([]);
  const [orders, setOrders] = useState<OmsOrderRow[]>([]);
  const [t212, setT212] = useState<T212Response | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // ── fetch helpers ───────────────────────────────────────────────────────────

  const fetchSessions = useCallback(async () => {
    // Was hitting api.paperSessions() which lists kind=paper_session
    // rows. The Run Session button + /scan both enqueue kind=intraday
    // (the trader-quant intraday engine is what runs ichimoku_equity
    // these days), so the page never saw its own sessions. Switch to
    // ops sessions filtered to intraday so matchesStrategy actually
    // matches.
    const res = await api.opsSessions("intraday", 30);
    setAllSessions(res.sessions as SessionRow[]);
  }, []);

  const fetchOrders = useCallback(async () => {
    const res = await api.omsOrders(undefined, 200);
    setOrders(res.orders);
  }, []);

  const fetchT212 = useCallback(async () => {
    const url = `${config.apiBaseUrl}/api/integrations/trading212/positions?account=demo`;
    const resp = await fetch(url);
    if (resp.ok) {
      const body = (await resp.json()) as T212Response;
      setT212(body);
    }
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      await Promise.all([fetchSessions(), fetchOrders(), fetchT212()]);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [fetchSessions, fetchOrders, fetchT212]);

  // ── initial load + polling ──────────────────────────────────────────────────

  useEffect(() => {
    void fetchAll();
    const id = setInterval(() => { void fetchAll(); }, 30_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  // ── SSE order refresh ───────────────────────────────────────────────────────

  const onOmsEvent = useCallback((_eventType: string, _seq: number) => {
    void fetchOrders();
  }, [fetchOrders]);

  useOmsEvents(onOmsEvent);

  // ── derived data ────────────────────────────────────────────────────────────

  const strategySessions = allSessions.filter(matchesStrategy);

  const latestCompleted = strategySessions.find(
    (s) => s.state.toLowerCase() === "completed",
  ) ?? null;

  const latestSession = strategySessions[0] ?? null;

  const resultSummary = latestCompleted ? getResultSummary(latestCompleted) : null;

  const decisions: Decision[] = (resultSummary?.decisions ?? []).slice().sort(
    (a, b) => {
      if (!a.barTs && !b.barTs) return 0;
      if (!a.barTs) return 1;
      if (!b.barTs) return -1;
      return new Date(b.barTs).getTime() - new Date(a.barTs).getTime();
    },
  );

  const charts = resultSummary?.charts ?? {};
  const chartSymbols = Object.keys(charts);

  const strategyOrders = orders.filter(
    (o) => o.strategyId === STRATEGY_ID,
  ).sort(
    (a, b) => new Date(b.createdAtUtc).getTime() - new Date(a.createdAtUtc).getTime(),
  );

  const signalSymbols = new Set(decisions.map((d) => d.symbol));

  // Collect distinct data_window_start dates from the latest completed session.
  // When a bank holiday is skipped, this shows the actual date bars came from.
  const dataWindowDates: string[] = (() => {
    const results = resultSummary?.results ?? [];
    const dates = new Set(results.map((r) => r.data_window_start).filter(Boolean) as string[]);
    return [...dates].sort();
  })();

  const t212Positions: T212Position[] = (() => {
    if (!t212?.positions?.length) return [];
    const filtered = t212.positions.filter(
      (p) => signalSymbols.has(p.ticker) || signalSymbols.has(p.yahooSymbol ?? ""),
    );
    return filtered.length > 0 ? filtered : t212.positions;
  })();

  // ── run session ─────────────────────────────────────────────────────────────

  async function handleRun() {
    setRunning(true);
    setRunError(null);
    try {
      // The intraday engine reads cfg["symbols"] (a list), not
      // cfg["universe"] (a name). Fetch the universe's tickers first
      // and send the explicit symbol list. Defaults to large_50
      // (trader hand-curated 50-name sleeve) for fast demo.
      const u = await api.universe(RUN_UNIVERSE);
      const symbols = u.symbols
        .filter((s) => s.effective)
        .map((s) => s.ticker);
      if (symbols.length === 0) {
        throw new Error(`Universe ${RUN_UNIVERSE} is empty — re-ingest from the Mac.`);
      }
      const today = new Date().toISOString().slice(0, 10);
      await api.runIntraday({
        strategy: STRATEGY_ID,
        symbols,
        session_date: today,
        // Off-hours runs need this — otherwise the engine completes
        // instantly with skipped="outside session window".
        bypass_window: true,
        lookback_days: 1,
        placement_mode: "manual",
        capital_usd: 100_000,
      });
      await fetchSessions();
    } catch (e) {
      setRunError(String(e));
    } finally {
      setRunning(false);
    }
  }

  // ── render ──────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div style={{ padding: 32, color: C.muted, fontSize: 14 }}>Loading…</div>
    );
  }

  const sessionState = latestSession?.state ?? "—";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 1200 }}>
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Ichimoku Equity</h1>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: C.muted }}>
            ichimoku_equity · {RUN_UNIVERSE}
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
          <SessionStatePill state={sessionState} />
          <button
            className="primary"
            onClick={() => { void handleRun(); }}
            disabled={running}
            style={{ fontSize: 12, padding: "6px 14px" }}
          >
            {running ? "Queuing…" : "Run Session"}
          </button>
        </div>
        {error && (
          <span style={{ fontSize: 12, color: C.red }}>{error}</span>
        )}
        {runError && (
          <span style={{ fontSize: 12, color: C.red }}>Run failed: {runError}</span>
        )}
      </div>

      {/* ── Strategy validation (trader-spec backtest) ─────────────────────── */}
      <EquityPipelineCard strategy={STRATEGY_ID} />

      {/* ── Card 1: Signal Grid ─────────────────────────────────────────────── */}
      <CockpitCard
        id="ichi-eq-signals"
        title="Signal Grid"
        badge={decisions.length || undefined}
        fullWidth
      >
        {dataWindowDates.length > 0 && (
          <div style={{ marginBottom: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
            {dataWindowDates.map((d) => (
              <span
                key={d}
                title="Actual date bars were fetched from — may differ from session date when there is a bank holiday"
                style={{
                  display: "inline-block",
                  padding: "2px 8px",
                  borderRadius: 999,
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#60a5fa",
                  background: "rgba(96,165,250,0.12)",
                  border: "1px solid rgba(96,165,250,0.25)",
                  letterSpacing: "0.03em",
                  cursor: "default",
                }}
              >
                Data: {d}
              </span>
            ))}
          </div>
        )}
        {decisions.length === 0 ? (
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>
            No signals yet — session pending or not yet started
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: C.muted, textAlign: "left" }}>
                  <th style={thStyle}>Symbol</th>
                  <th style={thStyle}>Action</th>
                  <th style={thStyle}>Reason</th>
                  <th style={thStyle}>Bar Time</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d, i) => (
                  <tr key={i} style={i % 2 === 0 ? rowEven : rowOdd}>
                    <td style={tdStyle}><strong>{d.symbol}</strong></td>
                    <td style={tdStyle}><ActionPill action={d.action} /></td>
                    <td style={{ ...tdStyle, maxWidth: 420, color: "var(--text-dim)" }}>{d.reason}</td>
                    <td style={{ ...tdStyle, whiteSpace: "nowrap", fontFamily: "monospace" }}>
                      {fmtTime(d.barTs)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CockpitCard>

      {/* ── Card 2: Ichimoku Charts ─────────────────────────────────────────── */}
      <CockpitCard
        id="ichi-eq-charts"
        title="Ichimoku Charts"
        badge={chartSymbols.length || undefined}
        fullWidth
      >
        {chartSymbols.length === 0 ? (
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>No charts yet</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            {chartSymbols.map((sym) => (
              <div key={sym}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: "var(--text)" }}>
                  {sym}
                </div>
                <PlotlyChart figure={charts[sym] as Record<string, unknown>} />
              </div>
            ))}
          </div>
        )}
      </CockpitCard>

      {/* ── Card 3: Positions ───────────────────────────────────────────────── */}
      <CockpitCard
        id="ichi-eq-positions"
        title="Positions"
        badge={t212Positions.length || undefined}
        fullWidth
      >
        {t212Positions.length === 0 ? (
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>No open positions</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: C.muted, textAlign: "left" }}>
                  <th style={thStyle}>Symbol</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Qty</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Avg Price</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Current Price</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>Unrealised</th>
                </tr>
              </thead>
              <tbody>
                {t212Positions.map((p, i) => {
                  const pnlColor = p.unrealisedPct >= 0 ? C.green : C.red;
                  return (
                    <tr key={p.ticker} style={i % 2 === 0 ? rowEven : rowOdd}>
                      <td style={tdStyle}><strong>{p.ticker}</strong></td>
                      <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                        {p.quantity}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                        {p.averagePricePaid.toFixed(2)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                        {p.currentPrice.toFixed(2)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>
                        <span style={{ color: pnlColor, fontFamily: "monospace" }}>
                          {fmtPct(p.unrealisedPct)}
                        </span>
                        <span style={{ color: C.muted, fontSize: 10, marginLeft: 6 }}>
                          ({p.unrealisedAbs >= 0 ? "+" : ""}{p.unrealisedAbs.toFixed(2)})
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CockpitCard>

      {/* ── Card 4: OMS Orders ──────────────────────────────────────────────── */}
      <CockpitCard
        id="ichi-eq-orders"
        title="OMS Orders"
        badge={strategyOrders.length || undefined}
        tone={strategyOrders.some((o) => o.state === "REJECTED") ? "down" : "default"}
        fullWidth
      >
        {strategyOrders.length === 0 ? (
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>No orders yet</p>
        ) : (
          <>
            <div style={{ overflowX: "auto", marginBottom: 16 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: C.muted, textAlign: "left" }}>
                    <th style={thStyle}>Symbol</th>
                    <th style={thStyle}>Side</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>Qty</th>
                    <th style={thStyle}>State</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>Fill Price</th>
                    <th style={thStyle}>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {strategyOrders.map((o, i) => (
                    <tr key={o.id} style={i % 2 === 0 ? rowEven : rowOdd}>
                      <td style={tdStyle}><strong>{o.symbol}</strong></td>
                      <td style={tdStyle}>
                        <span style={{ color: o.side === "BUY" ? C.green : C.red, fontWeight: 600 }}>
                          {o.side}
                        </span>
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                        {o.qty}
                      </td>
                      <td style={tdStyle}>
                        <StatePill state={o.state} />
                      </td>
                      <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                        {o.avgFillPrice != null ? o.avgFillPrice.toFixed(2) : "—"}
                      </td>
                      <td style={{ ...tdStyle, whiteSpace: "nowrap", fontFamily: "monospace", fontSize: 11 }}>
                        {fmtTime(o.createdAtUtc)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <PlotlyChart
              figure={buildOrderLifecycleFigure(strategyOrders)}
              fallback="Loading order timeline…"
            />
          </>
        )}
      </CockpitCard>

      {/* ── Card 5: Session Queue ───────────────────────────────────────────── */}
      <CockpitCard
        id="ichi-eq-sessions"
        title="Session Queue"
        badge={strategySessions.length || undefined}
      >
        {strategySessions.length === 0 ? (
          <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>No sessions yet</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {strategySessions.slice(0, 10).map((s) => {
              const rs = getResultSummary(s);
              const fills = rs?.decisions?.filter(
                (d) => d.action.toUpperCase() === "BUY" || d.action.toUpperCase() === "SELL"
              ).length ?? 0;
              return (
                <div
                  key={s.request_id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "6px 8px",
                    borderRadius: 6,
                    background: "rgba(255,255,255,0.03)",
                    flexWrap: "wrap",
                  }}
                >
                  <SessionStatePill state={s.state} />
                  <span style={{ fontSize: 11, color: C.muted, fontFamily: "monospace" }}>
                    {fmtTime(s.requested_at_utc)}
                  </span>
                  {rs?.barsSeen != null && (
                    <Pill
                      label={`${rs.barsSeen} bars`}
                      color={C.blue}
                      bg="rgba(79,140,255,0.14)"
                    />
                  )}
                  {fills > 0 && (
                    <Pill
                      label={`${fills} signals`}
                      color={C.green}
                      bg="rgba(31,193,107,0.14)"
                    />
                  )}
                  {s.error && (
                    <span style={{ fontSize: 11, color: C.red, maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.error}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CockpitCard>
    </div>
  );
}

// ── table style constants ─────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
  padding: "6px 10px",
  fontWeight: 600,
  fontSize: 11,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  borderBottom: "1px solid var(--border)",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 10px",
  verticalAlign: "middle",
};

const rowEven: React.CSSProperties = {
  background: "transparent",
};

const rowOdd: React.CSSProperties = {
  background: "rgba(255,255,255,0.02)",
};
