import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../components/CockpitCard";
import { InlineHint } from "../components/InlineHint";
import { PlotlyChart } from "../components/PlotlyChart";
import { api, OmsOrderRow } from "../api/client";
import { config } from "../config";
import { buildOrderLifecycleFigure } from "../viz/orderLifecycle";
import { buildActivityFeed, activityTone, type ActivityEvent } from "../viz/activityFeed";

/**
 * Trader cockpit — every piece of context the trader needs in one
 * screen as show/hide widgets. Per the trader's UX rule (single-
 * screen, no nav). All business logic is server-side; this file is
 * pure composition: fetch from existing endpoints, render in cards.
 *
 * Widgets (v1):
 *   ⚠ Warnings        — system errors (T212 down, daemon stale, drift)
 *   💵 Cash           — free balance, invested, total
 *   ⏳ Intents        — orders generated, awaiting approval (inline approve)
 *   ⛵ Submitted      — orders placed, awaiting fill
 *   ✓ Recent fills    — trades executed in the last 24h
 *   📊 Positions      — open positions + P&L + drift indicator
 *
 * Each widget is a `CockpitCard` whose open/closed state persists
 * per-card in localStorage so the operator's layout survives reloads.
 */

type T212Cash = Awaited<ReturnType<typeof api.t212Cash>>;

type DecisionEntry = {
  barTs: string | null;
  symbol: string;
  action: string;
  reason: string;
  detail: Record<string, unknown>;
};
type LatestSession = {
  strategy: string;
  requestId: string;
  completedAtUtc: string | null;
  decisions: DecisionEntry[];
  barsSeen: number;
  // Per-strategy Plotly figure dicts emitted by the strategy's
  // recent_charts() hook (ichimoku_equity emits one cloud chart
  // per symbol). Lets the cockpit embed live signal viz without
  // forcing the trader to drill into Session Detail.
  charts: Record<string, unknown>;
};
type T212PosResp = {
  enabled: boolean;
  mode: string;
  positionCount: number;
  positions: Array<{
    ticker: string;
    yahooSymbol: string | null;
    quantity: number;
    averagePricePaid: number | null;
    currentPrice: number | null;
    unrealisedPct: number | null;
    unrealisedAbs: number | null;
    currency: string | null;
  }>;
  error?: string | null;
  fromCache?: boolean;
  ageSeconds?: number;
};

const ACCOUNT_KEY = "cockpit.account";

export function TraderCockpit() {
  const [account, setAccount] = useState<"demo" | "live">(() => {
    if (typeof window === "undefined") return "demo";
    return (localStorage.getItem(ACCOUNT_KEY) as "demo" | "live") || "demo";
  });
  useEffect(() => {
    try { localStorage.setItem(ACCOUNT_KEY, account); } catch { /* noop */ }
  }, [account]);

  const [orders, setOrders] = useState<OmsOrderRow[]>([]);
  const [ordersErr, setOrdersErr] = useState<string | null>(null);
  const [cash, setCash] = useState<T212Cash | null>(null);
  const [cashErr, setCashErr] = useState<string | null>(null);
  const [positions, setPositions] = useState<T212PosResp | null>(null);
  const [posErr, setPosErr] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  // Latest completed session per strategy so the trader can validate
  // signal output (fire-buy / fire-sell decisions) at-a-glance even
  // when nothing actually executes at the broker — useful for FX
  // strategies on CFD where T212 has no public API for placement.
  const [latestSessions, setLatestSessions] = useState<LatestSession[]>([]);

  // Loaders — each fires independently so one slow source doesn't
  // block the others rendering.
  const loadOrders = useCallback(async () => {
    try {
      const { orders } = await api.omsOrders(undefined, 100);
      setOrders(orders);
      setOrdersErr(null);
    } catch (e) {
      setOrdersErr(String(e));
    }
  }, []);
  const loadCash = useCallback(async () => {
    try {
      const c = await api.t212Cash(account);
      setCash(c);
      setCashErr(null);
    } catch (e) {
      setCashErr(String(e));
    }
  }, [account]);
  const loadPositions = useCallback(async () => {
    try {
      const resp = await fetch(
        `${config.apiBaseUrl}/api/integrations/trading212/positions?account=${account}`,
      );
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const d = await resp.json();
      setPositions(d as T212PosResp);
      setPosErr(null);
    } catch (e) {
      setPosErr(String(e));
    }
  }, [account]);

  useEffect(() => { void loadOrders(); }, [loadOrders]);
  useEffect(() => { void loadCash(); void loadPositions(); }, [loadCash, loadPositions]);

  // Latest sessions — pull the most recent Completed per strategy
  // and extract the decision trace. Server-side data; we just project
  // to a flat row list for rendering. Polls every 30s.
  const loadSessions = useCallback(async () => {
    try {
      const { sessions } = await api.opsSessions(undefined, 20);
      const completed = sessions.filter((s) => s.status === "Completed");
      // Group by strategy and pick the newest per strategy.
      const byStrategy = new Map<string, LatestSession>();
      for (const s of completed) {
        const rs = (s.resultSummary ?? {}) as Record<string, unknown>;
        const strategy = (rs.strategy as string) || (s.payload as Record<string, unknown>)?.strategy as string;
        if (!strategy) continue;
        if (byStrategy.has(strategy)) continue;
        const strategies = (rs.strategies as Array<Record<string, unknown>>) || [];
        const decisions: DecisionEntry[] = [];
        let barsSeen = 0;
        const charts: Record<string, unknown> = {};
        // Top-level result_summary.charts (the quant-backtest CLI
        // emits here; ichimoku_equity emits per-strategy).
        const topCharts = rs.charts as Record<string, unknown> | undefined;
        if (topCharts && typeof topCharts === "object") {
          Object.assign(charts, topCharts);
        }
        for (const st of strategies) {
          const bs = st.bars_seen as Array<unknown> | undefined;
          if (Array.isArray(bs)) barsSeen += bs.length;
          const sc = st.charts as Record<string, unknown> | undefined;
          if (sc && typeof sc === "object") Object.assign(charts, sc);
          const ds = st.decisions as Array<Record<string, unknown>>;
          if (!Array.isArray(ds)) continue;
          for (const d of ds) {
            decisions.push({
              barTs: (d.bar_ts as string) || null,
              symbol: (d.symbol as string) || "",
              action: (d.action as string) || "",
              reason: (d.reason as string) || "",
              detail: (d.detail as Record<string, unknown>) || {},
            });
          }
        }
        // Sort newest-first; cap to 30 per strategy to keep render light.
        decisions.sort((a, b) => (b.barTs ?? "").localeCompare(a.barTs ?? ""));
        byStrategy.set(strategy, {
          strategy,
          requestId: s.requestId,
          completedAtUtc: s.completedAtUtc,
          decisions: decisions.slice(0, 30),
          barsSeen,
          charts,
        });
      }
      setLatestSessions(Array.from(byStrategy.values()));
    } catch {
      // Non-fatal; signals panel just won't update.
    }
  }, []);
  useEffect(() => { void loadSessions(); }, [loadSessions]);
  useEffect(() => {
    const t = setInterval(() => void loadSessions(), 30_000);
    return () => clearInterval(t);
  }, [loadSessions]);

  // Auto-refresh orders every 15s when ANY are open (not terminal).
  useEffect(() => {
    const hasOpen = orders.some((o) =>
      ["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(o.state),
    );
    if (!hasOpen) return;
    const t = setInterval(() => void loadOrders(), 15_000);
    return () => clearInterval(t);
  }, [orders, loadOrders]);

  // Bucket orders by state for the three order widgets.
  const pending = orders.filter((o) => o.state === "PENDING_APPROVAL");
  const submitted = orders.filter((o) =>
    ["SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(o.state),
  );
  const recent = orders
    .filter((o) => o.state === "FILLED" || o.state === "PARTIALLY_FILLED")
    .slice(0, 25);

  // Warnings — server-side data with frontend pattern-matching for
  // surfacing only. No new computation: just summarise what's wrong.
  const warnings: { tone: "warn" | "down"; text: string }[] = [];
  if (cashErr) warnings.push({ tone: "down", text: `Cash fetch failed: ${cashErr}` });
  else if (cash && cash.error) warnings.push({ tone: "down", text: `T212 cash: ${cash.error}` });
  else if (cash && !cash.enabled) warnings.push({ tone: "warn", text: cash.message ?? "T212 cash unavailable" });
  if (posErr) warnings.push({ tone: "down", text: `Positions fetch failed: ${posErr}` });
  else if (positions && positions.error) warnings.push({ tone: "down", text: `T212 positions: ${positions.error}` });
  if (ordersErr) warnings.push({ tone: "down", text: `OMS fetch failed: ${ordersErr}` });
  const rejectedRecent = orders.filter((o) =>
    o.state === "REJECTED" || o.state === "EXPIRED"
  ).length;
  if (rejectedRecent > 0) {
    warnings.push({
      tone: "warn",
      text: `${rejectedRecent} order${rejectedRecent === 1 ? " was" : "s were"} rejected — check /oms`,
    });
  }

  const act = async (orderId: string, kind: "approve" | "reject" | "cancel") => {
    setActing(orderId + ":" + kind);
    try {
      if (kind === "approve") await api.omsApprove(orderId);
      else if (kind === "reject") {
        const r = prompt("Reject reason?", "") || "rejected";
        await api.omsReject(orderId, r);
      } else {
        const r = prompt("Cancel reason?", "user_cancel") || "user_cancel";
        await api.omsCancel(orderId, r);
      }
      await loadOrders();
    } catch (e) {
      setOrdersErr(String(e));
    } finally {
      setActing(null);
    }
  };

  // Merge orders + recent-session decisions into one chronological
  // event stream. Top-of-cockpit "what happened today" view that
  // replaces the three-panel (generated / placed / executed) read
  // pattern with a single timeline.
  const activityEvents = buildActivityFeed(
    orders,
    latestSessions.map((s) => ({
      strategy: s.strategy,
      requestId: s.requestId,
      decisions: s.decisions,
    })),
    { limit: 50 },
  );

  const approveAll = async () => {
    if (!pending.length) return;
    if (!confirm(`Approve all ${pending.length} pending intent${pending.length === 1 ? "" : "s"}?`)) return;
    for (const o of pending) {
      try { await api.omsApprove(o.id); } catch { /* keep going through the queue */ }
    }
    await loadOrders();
  };

  return (
    <div style={{ padding: 20, maxWidth: 1280, margin: "0 auto" }}>
      {/* ── Account selector strip ──────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Trader cockpit</h1>
        <div style={{ display: "flex", gap: 4 }}>
          {(["demo", "live"] as const).map((a) => (
            <button
              key={a}
              onClick={() => setAccount(a)}
              style={{
                padding: "3px 10px", fontSize: 11, borderRadius: 999,
                border: `1px solid ${
                  account === a
                    ? a === "live" ? "#ef4444" : "#4f8cff"
                    : "var(--border)"
                }`,
                background: account === a
                  ? a === "live" ? "rgba(239,68,68,0.10)" : "rgba(79,140,255,0.10)"
                  : "transparent",
                color: account === a
                  ? a === "live" ? "#ef4444" : "#4f8cff"
                  : "var(--text-dim)",
                cursor: "pointer", letterSpacing: "0.04em",
                textTransform: "uppercase", fontWeight: 600,
              }}
              title={a === "live" ? "Trading 212 LIVE (real money)" : "Trading 212 demo (paper)"}
            >
              {a}
            </button>
          ))}
        </div>
      </div>

      {/* ── System health row — trust before breadth ─────────────── */}
      <SystemHealthRow />

      {/* ── KPI strip — always-visible single-glance status ──────── */}
      <KpiStrip
        cash={cash}
        orders={orders}
        positions={positions}
        warningCount={warnings.length}
      />

      {/* ── Today's outcome — English summary of the day ─────────── */}
      <TodayOutcome
        orders={orders}
        positions={positions}
        latestSessions={latestSessions}
      />

      {/* ── Cockpit panels grid — 2-col on wide screens, full-width
           cards (charts, wide tables) opt in via fullWidth prop. ──── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))",
          gap: 12,
          alignItems: "start",
        }}
      >
      {/* ── Warnings (only visible when any) ────────────────────── */}
      {warnings.length > 0 && (
        <CockpitCard
          id="warnings"
          title="Warnings"
          badge={warnings.length}
          tone="warn"
          defaultOpen
          fullWidth
        >
          {warnings.map((w, i) => (
            <div
              key={i}
              style={{
                fontSize: 12, padding: "4px 0",
                color: w.tone === "down" ? "var(--down)" : "#d97706",
              }}
            >
              {w.text}
            </div>
          ))}
        </CockpitCard>
      )}

      {/* ── Trigger (compact run form) ──────────────────────────── */}
      <CockpitCard id="trigger" title="Trigger session" defaultOpen={false}>
        <TriggerPanel onTriggered={() => { void loadOrders(); void loadSessions(); }} />
      </CockpitCard>

      {/* ── Manual test placement (skip the strategy, smoke the chain) */}
      <CockpitCard id="testorder" title="Test placement (manual OMS → T212 demo)" defaultOpen={false}>
        <TestPlacementPanel onPlaced={() => void loadOrders()} />
      </CockpitCard>

      {/* ── Cash ─────────────────────────────────────────────────── */}
      <CockpitCard
        id="cash"
        title="Cash"
        badge={cash?.free != null ? `${cash.currency ?? ""} ${cash.free.toLocaleString()}` : undefined}
        tone="ok"
      >
        {!cash ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading…</span>
        ) : !cash.enabled || cash.error ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {cash.error ?? cash.message ?? "Cash unavailable"}
          </span>
        ) : (
          <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
            <Stat label="Free" value={fmtMoney(cash.free, cash.currency)} big tone="ok" />
            <Stat label="Invested" value={fmtMoney(cash.invested, cash.currency)} />
            <Stat label="Total" value={fmtMoney(cash.total, cash.currency)} />
            {cash.ppl != null && (
              <Stat
                label="Open P&L"
                value={fmtMoney(cash.ppl, cash.currency)}
                tone={(cash.ppl ?? 0) >= 0 ? "ok" : "down"}
              />
            )}
            <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}>
              T212 {cash.mode.toUpperCase()} · Invest (CFD cash separate)
            </span>
          </div>
        )}
      </CockpitCard>

      {/* ── Pending intents (action required) ───────────────────── */}
      <CockpitCard
        id="intents"
        title="Order generated — awaiting approval"
        badge={pending.length || undefined}
        tone={pending.length > 0 ? "warn" : "default"}
        defaultOpen
        actions={pending.length > 0 ? (
          <button
            onClick={approveAll}
            style={miniButton("ok")}
            title="Approve every pending intent"
          >
            approve all
          </button>
        ) : null}
      >
        {pending.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No intents waiting. The next strategy run will populate this list.
          </span>
        ) : (
          <OrdersTable
            rows={pending}
            acting={acting}
            onApprove={(id) => act(id, "approve")}
            onReject={(id) => act(id, "reject")}
            onCancel={(id) => act(id, "cancel")}
            allowApprove
          />
        )}
      </CockpitCard>

      {/* ── Submitted (in flight at broker) ─────────────────────── */}
      <CockpitCard
        id="submitted"
        title="Order placed — awaiting fill"
        badge={submitted.length || undefined}
      >
        {submitted.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Nothing in flight.</span>
        ) : (
          <OrdersTable
            rows={submitted}
            acting={acting}
            onApprove={() => {}}
            onReject={() => {}}
            onCancel={(id) => act(id, "cancel")}
          />
        )}
      </CockpitCard>

      {/* ── Recent fills ────────────────────────────────────────── */}
      <CockpitCard
        id="fills"
        title="Trade executed (recent)"
        badge={recent.length || undefined}
        tone={recent.length > 0 ? "ok" : "default"}
      >
        {recent.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No fills yet.</span>
        ) : (
          <OrdersTable rows={recent} acting={null} onApprove={() => {}} onReject={() => {}} onCancel={() => {}} />
        )}
      </CockpitCard>

      {/* ── Activity feed (unified signal / order / fill timeline) */}
      <CockpitCard
        id="activity"
        title="Activity feed"
        badge={activityEvents.length || undefined}
        defaultOpen
        fullWidth
      >
        {activityEvents.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No activity yet. Signal fires, OMS state changes, and fills land
            here as one chronological timeline.
          </span>
        ) : (
          <ActivityList events={activityEvents} />
        )}
      </CockpitCard>

      {/* ── Trade cards (signal → order → fill → P&L chain) ──────── */}
      <TradeCardsPanel
        orders={orders}
        positions={positions}
        latestSessions={latestSessions}
      />

      {/* ── Strategy charts (Ichimoku cloud per symbol) ────────── */}
      <StrategyChartsCard latestSessions={latestSessions} />

      {/* ── Order lifecycle Gantt ──────────────────────────────── */}
      <CockpitCard
        id="lifecycle"
        title="Order lifecycle (Gantt)"
        badge={orders.length || undefined}
        defaultOpen={false}
        fullWidth
      >
        {orders.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No orders yet. Fires from a strategy run or the Test placement
            panel will appear here as horizontal bars from enqueue to terminal,
            colour-coded by state.
          </span>
        ) : (
          <PlotlyChart figure={buildOrderLifecycleFigure(orders)} />
        )}
      </CockpitCard>

      {/* ── Strategy signals (validate without broker) ──────────── */}
      {latestSessions.length > 0 && (
        <CockpitCard
          id="signals"
          title="Strategy signals — latest run per strategy"
          badge={latestSessions.reduce((n, s) =>
            n + s.decisions.filter((d) => d.action.startsWith("fire-")).length, 0) || undefined}
          tone="ok"
          fullWidth
        >
          {latestSessions.map((s) => {
            const fired = s.decisions.filter((d) => d.action.startsWith("fire-"));
            const skipped = s.decisions.filter((d) => d.action.startsWith("skip-"));
            return (
              <div key={s.strategy} style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{s.strategy}</span>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    completed {s.completedAtUtc ? new Date(s.completedAtUtc).toLocaleTimeString() : "—"}
                    {" · "}{fired.length} fire · {skipped.length} skip
                  </span>
                  <Link
                    to={`/paper-live/session/${encodeURIComponent(s.requestId)}`}
                    style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}
                  >
                    session detail →
                  </Link>
                </div>
                {fired.length === 0 ? (
                  <NoFiresDiagnostic
                    barsSeen={s.barsSeen}
                    skipped={skipped}
                  />
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                    <thead>
                      <tr style={{ color: "var(--text-dim)" }}>
                        <th style={posTh}>Bar UTC</th>
                        <th style={posTh}>Symbol</th>
                        <th style={posTh}>Action</th>
                        <th style={posTh}>Reason</th>
                        <th style={posTh}>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fired.slice(0, 10).map((d, i) => (
                        <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                          <td style={{ ...posTd, fontFamily: "monospace", color: "var(--text-muted)" }}>
                            {d.barTs ? d.barTs.slice(11, 19) : "—"}
                          </td>
                          <td style={posTd}>{d.symbol}</td>
                          <td style={{ ...posTd, color: "#1fc16b", fontFamily: "monospace" }}>{d.action}</td>
                          <td style={posTd}>{d.reason}</td>
                          <td style={{ ...posTd, fontFamily: "monospace", fontSize: 10, color: "var(--text-muted)" }}>
                            {Object.keys(d.detail).length ? JSON.stringify(d.detail) : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
            Showing top 10 fire-* decisions per strategy. Full timeline (incl. skip-* with reasons) in Session Detail.
          </div>
        </CockpitCard>
      )}

      {/* ── Positions ────────────────────────────────────────────── */}
      <CockpitCard
        id="positions"
        title="Overall position"
        badge={positions?.enabled ? positions.positionCount || undefined : undefined}
      >
        {!positions ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading…</span>
        ) : !positions.enabled ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            T212 {account} not connected.
          </span>
        ) : positions.positionCount === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No open positions in T212 {account}.
          </span>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)" }}>
                <th style={posTh}>Ticker</th>
                <th style={{ ...posTh, textAlign: "right" }}>Qty</th>
                <th style={{ ...posTh, textAlign: "right" }}>Avg cost</th>
                <th style={{ ...posTh, textAlign: "right" }}>Now</th>
                <th style={{ ...posTh, textAlign: "right" }}>P&L %</th>
                <th style={{ ...posTh, textAlign: "right" }}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {positions.positions.map((p) => (
                <tr key={p.ticker} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={posTd}>{p.ticker}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{p.quantity}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{p.averagePricePaid?.toFixed(4) ?? "—"}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{p.currentPrice?.toFixed(4) ?? "—"}</td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace", color: (p.unrealisedPct ?? 0) >= 0 ? "#1fc16b" : "#ef4444" }}>
                    {p.unrealisedPct != null ? `${p.unrealisedPct >= 0 ? "+" : ""}${p.unrealisedPct.toFixed(2)}%` : "—"}
                  </td>
                  <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace", color: (p.unrealisedAbs ?? 0) >= 0 ? "#1fc16b" : "#ef4444" }}>
                    {p.unrealisedAbs != null ? `${p.unrealisedAbs >= 0 ? "+" : ""}${p.unrealisedAbs.toFixed(2)}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
          Detailed view: <Link to="/portfolio" style={{ color: "var(--text-muted)" }}>Portfolio →</Link>
          {" · "}Per-order drill-in: <Link to="/oms" style={{ color: "var(--text-muted)" }}>OMS →</Link>
        </div>
      </CockpitCard>
      </div>
    </div>
  );
}

/**
 * SystemHealthRow — six compact pills showing whether the underlying
 * chain the trader needs (API, Postgres, Mac daemon, T212, Finnhub,
 * Yahoo) is healthy at this moment. Visible at the very top of the
 * cockpit because the memory-rule "build trust before breadth" needs
 * a single-glance "is the engine alive?" surface before the trader
 * commits to a session.
 *
 * Pulls from /health/details + /health/integrations (both public).
 * Polls every 60s. Each pill carries a tooltip explaining the
 * verdict + how it's computed. Clicking the pill links to /health
 * (the existing dedicated page) for the full breakdown.
 */
function SystemHealthRow() {
  type Pill = { label: string; status: "ok" | "warn" | "down"; detail: string };
  const [details, setDetails] = useState<Record<string, unknown> | null>(null);
  const [integrations, setIntegrations] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const d = await fetch(new URL("/health/details", config.apiBaseUrl).toString());
        if (d.ok && live) setDetails(await d.json());
      } catch { /* keep last good */ }
      try {
        const i = await fetch(new URL("/health/integrations", config.apiBaseUrl).toString());
        if (i.ok && live) setIntegrations(await i.json());
      } catch { /* best-effort */ }
    };
    void tick();
    const id = setInterval(() => void tick(), 60_000);
    return () => { live = false; clearInterval(id); };
  }, []);

  const pills: Pill[] = [];

  // API
  const apiUp = Number((details?.api as Record<string, unknown> | undefined)?.uptimeSeconds ?? 0);
  pills.push({
    label: "API",
    status: details ? "ok" : "down",
    detail: details
      ? `Up ${fmtUptime(apiUp)} · commit ${String((details.deploy as Record<string, unknown> | undefined)?.backendCommit ?? "").slice(0, 7)}`
      : "API unreachable — frontend can't fetch /health/details",
  });

  // Postgres
  const pg = (details?.deploy as Record<string, unknown> | undefined)?.postgres as
    Record<string, unknown> | undefined;
  pills.push({
    label: "Postgres",
    status: pg?.connected ? "ok" : "down",
    detail: pg?.connected
      ? `Connected · ${pg.latencyMs ?? "?"}ms last probe`
      : `Disconnected: ${pg?.error ?? "unknown"}`,
  });

  // Mac worker (Python daemon liveness)
  const worker = details?.worker as Record<string, unknown> | undefined;
  const liveness = (worker?.liveness as string) ?? "down";
  const sinceLast = Number(worker?.sinceLastPingSeconds ?? -1);
  pills.push({
    label: "Mac daemon",
    status: liveness === "alive" ? "ok" : liveness === "late" ? "warn" : "down",
    detail: worker
      ? `${liveness}${sinceLast >= 0 ? ` · last ping ${fmtUptime(sinceLast)} ago` : ""}${worker.host ? ` · host ${worker.host}` : ""}${worker.isProcessing ? " · processing" : ""}`
      : "Mac worker hasn't pinged yet",
  });

  // T212 (from integrations probe)
  const providers = (integrations?.providers as Array<Record<string, unknown>> | undefined) ?? [];
  const t212 = providers.find((p) => p.provider === "trading212");
  if (t212) {
    pills.push({
      label: "T212",
      status: t212.status === "ok" ? "ok" : t212.status === "disabled" ? "warn" : "down",
      detail: `${t212.label} (${t212.mode ?? "live"}) · ${t212.detail}`,
    });
  }

  // Yahoo (data freshness)
  const yahoo = providers.find((p) => p.provider === "yahoo");
  if (yahoo) {
    pills.push({
      label: "Yahoo data",
      status: yahoo.status === "ok" ? "ok" : "warn",
      detail: `${yahoo.label} · ${yahoo.detail}`,
    });
  }

  return (
    <div
      style={{
        display: "flex", gap: 6, flexWrap: "wrap",
        marginBottom: 10, fontSize: 11,
      }}
    >
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
        alignSelf: "center", marginRight: 2,
      }}>
        Health
      </span>
      {pills.map((p, i) => (
        <Link
          key={i}
          to="/health"
          title={p.detail}
          style={{
            display: "inline-flex", gap: 5, alignItems: "center",
            padding: "2px 9px", borderRadius: 999, fontSize: 11,
            border: `1px solid ${
              p.status === "ok" ? "rgba(31,193,107,0.30)" :
              p.status === "warn" ? "rgba(245,158,11,0.30)" :
              "rgba(239,68,68,0.30)"
            }`,
            background:
              p.status === "ok" ? "rgba(31,193,107,0.06)" :
              p.status === "warn" ? "rgba(245,158,11,0.06)" :
              "rgba(239,68,68,0.06)",
            color: p.status === "ok" ? "#1fc16b" : p.status === "warn" ? "#f59e0b" : "#ef4444",
            textDecoration: "none", letterSpacing: "0.02em",
          }}
        >
          <span style={{ fontSize: 10 }}>
            {p.status === "ok" ? "✓" : p.status === "warn" ? "⚠" : "✗"}
          </span>
          {p.label}
        </Link>
      ))}
    </div>
  );
}

function fmtUptime(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  return `${Math.floor(sec / 86400)}d`;
}

/**
 * TradeCardsPanel — one "card per fired signal" that joins the
 * Strategy → OMS Order → Fill → P&L chain visually so the trader
 * doesn't have to cross-reference three tables. For each fire-*
 * decision in the latest session we look up the OMS order with the
 * matching strategy_id + symbol + side (best-effort heuristic; the
 * order's ClientOrderId is a hash that includes bar_ts so we'd need
 * the daemon's hash code to match exactly — for the cockpit a
 * symbol+strategy+side match is enough to surface the link).
 *
 * Once the order's symbol matches a T212 position, we also pull the
 * unrealised P&L for that symbol. Empty cards (no order yet) show
 * the chain as far as it's gone — gives the trader visible feedback
 * the moment a signal fires.
 */
function TradeCardsPanel({
  orders, positions, latestSessions,
}: {
  orders: OmsOrderRow[];
  positions: T212PosResp | null;
  latestSessions: LatestSession[];
}) {
  type Card = {
    key: string;
    strategy: string;
    symbol: string;
    side: string;
    decision?: DecisionEntry;
    order?: OmsOrderRow;
    pnl?: { abs: number; pct: number; currency: string | null } | null;
  };
  const cards: Card[] = [];
  for (const s of latestSessions) {
    for (const d of s.decisions) {
      if (!d.action.startsWith("fire-")) continue;
      const sideFromDetail = (d.detail.side as string | undefined) ?? "";
      const side = sideFromDetail || (d.action.includes("entry") ? "BUY" : "SELL");
      // Match an order from the same strategy + symbol + side.
      // OMS strips _US_EQ etc; the decision symbol is the raw ticker —
      // accept a startsWith match either way.
      const order = orders.find((o) =>
        o.strategyId === s.strategy &&
        o.side === side &&
        (o.symbol === d.symbol ||
         o.symbol.startsWith(d.symbol + "_") ||
         d.symbol.startsWith(o.symbol + "_")),
      );
      const pos = positions?.positions.find(
        (p) => p.ticker === d.symbol ||
               p.yahooSymbol === d.symbol ||
               p.ticker.startsWith(d.symbol + "_"),
      );
      cards.push({
        key: `${s.strategy}.${d.symbol}.${d.barTs ?? ""}`,
        strategy: s.strategy,
        symbol: d.symbol,
        side,
        decision: d,
        order,
        pnl: pos && pos.unrealisedAbs != null && pos.unrealisedPct != null
          ? { abs: pos.unrealisedAbs, pct: pos.unrealisedPct, currency: pos.currency }
          : null,
      });
    }
  }
  // Newest first; cap to 12 cards so the panel stays scannable.
  cards.sort((a, b) =>
    (b.decision?.barTs ?? "").localeCompare(a.decision?.barTs ?? ""),
  );
  const visible = cards.slice(0, 12);

  return (
    <CockpitCard
      id="trade-cards"
      title="Trades — signal → order → fill → P&L"
      badge={visible.length || undefined}
      defaultOpen={visible.length > 0}
      fullWidth
    >
      {visible.length === 0 ? (
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No fire-* signals from the latest session. When a strategy fires,
          this panel shows each signal joined to its OMS order + fill price
          + unrealised P&L from the matching T212 position.
        </span>
      ) : (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 10,
        }}>
          {visible.map((c) => (
            <TradeCard key={c.key} card={c} />
          ))}
        </div>
      )}
    </CockpitCard>
  );
}

function TradeCard({ card }: { card: {
  strategy: string;
  symbol: string;
  side: string;
  decision?: DecisionEntry;
  order?: OmsOrderRow;
  pnl?: { abs: number; pct: number; currency: string | null } | null;
} }) {
  const sideColor = card.side === "BUY" ? "#1fc16b" : "#ef4444";
  const orderStateColor =
    card.order?.state === "FILLED" ? "#1fc16b" :
    card.order?.state === "REJECTED" ? "#ef4444" :
    card.order?.state === "CANCELLED" ? "var(--text-muted)" :
    card.order ? "#4f8cff" : "var(--text-muted)";
  const pnlColor = card.pnl && card.pnl.abs >= 0 ? "#1fc16b" : "#ef4444";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: 10,
        fontSize: 12,
        background: "var(--bg-hover, rgba(255,255,255,0.02))",
      }}
    >
      {/* Header — symbol + side */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ fontFamily: "monospace", fontWeight: 700, fontSize: 14 }}>
          {card.symbol}
        </span>
        <span style={{
          fontSize: 10, padding: "1px 6px",
          borderRadius: 999, color: sideColor,
          background: card.side === "BUY" ? "rgba(31,193,107,0.10)" : "rgba(239,68,68,0.10)",
          fontWeight: 700, letterSpacing: "0.04em",
        }}>
          {card.side}
        </span>
        <span style={{
          fontSize: 10, color: "var(--text-muted)", fontFamily: "monospace", marginLeft: "auto",
        }}>
          {card.strategy}
        </span>
      </div>

      {/* Chain steps. Each step lights up when reached. */}
      <ChainStep
        label="Signal"
        ok={!!card.decision}
        detail={card.decision
          ? `${card.decision.action} · ${card.decision.reason || "(no reason)"}`
          : "no decision logged"}
        ts={card.decision?.barTs ?? null}
      />
      <ChainStep
        label="Order"
        ok={!!card.order}
        detail={card.order
          ? `qty=${card.order.qty} · ${card.order.state}`
          : "no matching OMS order yet"}
        tone={orderStateColor}
        ts={card.order?.createdAtUtc ?? null}
        href={card.order ? "/oms" : undefined}
      />
      <ChainStep
        label="Fill"
        ok={card.order?.state === "FILLED" || (card.order?.filledQty ?? 0) > 0}
        detail={
          card.order?.avgFillPrice != null
            ? `${card.order.filledQty} @ ${card.order.avgFillPrice.toFixed(4)}`
            : card.order?.state === "REJECTED"
              ? `rejected${card.order.cancelledReason ? `: ${card.order.cancelledReason}` : ""}`
              : "awaiting fill"
        }
        tone={card.order?.state === "REJECTED" ? "#ef4444" : undefined}
        ts={card.order?.state === "FILLED" ? card.order?.lastStateChangeAtUtc ?? null : null}
      />
      <ChainStep
        label="P&L"
        ok={card.pnl != null}
        detail={card.pnl
          ? `${card.pnl.abs >= 0 ? "+" : ""}${card.pnl.currency ?? ""} ${card.pnl.abs.toFixed(2)} (${card.pnl.pct >= 0 ? "+" : ""}${card.pnl.pct.toFixed(2)}%)`
          : "no position open"}
        tone={card.pnl ? pnlColor : undefined}
      />
    </div>
  );
}

function ChainStep({
  label, ok, detail, tone, ts, href,
}: {
  label: string;
  ok: boolean;
  detail: string;
  tone?: string;
  ts?: string | null;
  href?: string;
}) {
  const dotColor = ok ? (tone ?? "#1fc16b") : "var(--text-muted)";
  const body = (
    <div style={{
      display: "grid",
      gridTemplateColumns: "12px 60px 1fr auto",
      gap: 6, alignItems: "baseline",
      padding: "3px 0",
      fontSize: 11,
      color: ok ? "var(--text)" : "var(--text-muted)",
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: 999,
        background: dotColor, alignSelf: "center",
        opacity: ok ? 1 : 0.4,
      }} />
      <span style={{ color: "var(--text-dim)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </span>
      <span style={{
        color: tone ?? "inherit",
        fontFamily: detail.length < 40 ? "monospace" : "inherit",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {detail}
      </span>
      <span style={{ color: "var(--text-muted)", fontSize: 9, fontFamily: "monospace" }}>
        {ts ? new Date(ts).toLocaleTimeString([], { hour12: false }) : ""}
      </span>
    </div>
  );
  if (href) {
    return (
      <Link to={href} style={{ textDecoration: "none", color: "inherit", display: "block" }}>
        {body}
      </Link>
    );
  }
  return body;
}

/**
 * StrategyChartsCard — embed any Plotly figure the strategy emitted
 * via recent_charts() directly on the cockpit. Today that's the
 * per-symbol Ichimoku cloud + fill markers from ichimoku_equity.
 * Defaults to closed so the heavy plotly.js bundle only lazy-loads
 * when the trader explicitly opens it.
 */
function StrategyChartsCard({ latestSessions }: { latestSessions: LatestSession[] }) {
  // Flatten charts across all latest sessions. Sort by strategy
  // then by chart name for deterministic display.
  const entries: Array<{ key: string; title: string; strategy: string; figure: unknown }> = [];
  for (const s of latestSessions) {
    for (const [name, fig] of Object.entries(s.charts ?? {})) {
      entries.push({
        key: `${s.strategy}.${name}`,
        title: name,
        strategy: s.strategy,
        figure: fig,
      });
    }
  }
  entries.sort((a, b) => a.key.localeCompare(b.key));

  return (
    <CockpitCard
      id="charts"
      title="Strategy charts (live signal viz)"
      badge={entries.length || undefined}
      defaultOpen={false}
      fullWidth
    >
      {entries.length === 0 ? (
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No charts attached to the latest session yet. Strategies that
          implement recent_charts() (today: ichimoku_equity → cloud chart
          per symbol) populate this on the next completed run.
        </span>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {entries.map((e) => (
            <div key={e.key}>
              <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 4 }}>
                {e.strategy} · {e.title}
              </div>
              <PlotlyChart figure={e.figure as Record<string, unknown>} />
            </div>
          ))}
        </div>
      )}
    </CockpitCard>
  );
}

/**
 * TodayOutcome — English summary at the top of the cockpit so the
 * trader reads the day's story before scanning panels. Three lines
 * max: what fired, what filled / failed, who carried / dragged P&L.
 *
 * All computed client-side from already-fetched cockpit state — no
 * new endpoint. Hidden when nothing's happened today (no fires, no
 * fills, no positions) so a quiet day isn't padded with placeholders.
 */
function TodayOutcome({
  orders, positions, latestSessions,
}: {
  orders: OmsOrderRow[];
  positions: T212PosResp | null;
  latestSessions: LatestSession[];
}) {
  const today = new Date().toISOString().slice(0, 10);

  // Fires today across all strategies' latest sessions.
  let firesToday = 0;
  const firingStrategies = new Set<string>();
  for (const s of latestSessions) {
    if ((s.completedAtUtc ?? "").slice(0, 10) !== today) continue;
    for (const d of s.decisions) {
      if (d.action.startsWith("fire-")) {
        firesToday++;
        firingStrategies.add(s.strategy);
      }
    }
  }

  // Orders that moved into a terminal state today.
  const todayOrders = orders.filter(
    (o) => o.lastStateChangeAtUtc.slice(0, 10) === today,
  );
  const fillsToday = todayOrders.filter((o) => o.state === "FILLED");
  const rejectsToday = todayOrders.filter(
    (o) => o.state === "REJECTED" || o.state === "CANCELLED",
  );

  // Symbol P&L contribution from positions (carry / drag).
  const sortedPos = positions?.positions ?
    [...positions.positions].filter((p) => p.unrealisedAbs != null)
      .sort((a, b) => (b.unrealisedAbs ?? 0) - (a.unrealisedAbs ?? 0)) : [];
  const carrier = sortedPos[0];
  const dragger = sortedPos[sortedPos.length - 1];
  const totalPnl = sortedPos.reduce((n, p) => n + (p.unrealisedAbs ?? 0), 0);

  const nothingHappened =
    firesToday === 0 && fillsToday.length === 0 &&
    rejectsToday.length === 0 && sortedPos.length === 0;
  if (nothingHappened) return null;

  const ccy = positions?.positions[0]?.currency ?? "";
  const pnlColor = totalPnl >= 0 ? "#1fc16b" : "#ef4444";

  return (
    <div
      style={{
        padding: "10px 14px",
        marginBottom: 12,
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "rgba(168,85,247,0.04)",
        fontSize: 12,
        lineHeight: 1.6,
        color: "var(--text)",
      }}
    >
      <div style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
        marginBottom: 4,
      }}>
        Today
      </div>
      <div>
        {firesToday > 0 ? (
          <>
            <strong style={{ color: "#a855f7" }}>{firesToday} signal{firesToday === 1 ? "" : "s"}</strong>{" "}
            fired from{" "}
            <strong>{Array.from(firingStrategies).join(", ")}</strong>
            {fillsToday.length > 0 && (
              <>
                {" · "}
                <strong style={{ color: "#1fc16b" }}>{fillsToday.length} filled</strong>
              </>
            )}
            {rejectsToday.length > 0 && (
              <>
                {" · "}
                <strong style={{ color: "#ef4444" }}>{rejectsToday.length} rejected</strong>
              </>
            )}
          </>
        ) : fillsToday.length > 0 || rejectsToday.length > 0 ? (
          <>
            No new signals fired today.{" "}
            {fillsToday.length > 0 && (
              <>
                <strong style={{ color: "#1fc16b" }}>{fillsToday.length} fill{fillsToday.length === 1 ? "" : "s"}</strong>{" "}
                cleared (earlier intent).{" "}
              </>
            )}
            {rejectsToday.length > 0 && (
              <strong style={{ color: "#ef4444" }}>
                {rejectsToday.length} rejected — check the histogram on /oms.
              </strong>
            )}
          </>
        ) : (
          <>Strategies ran but emitted no signals. No order activity yet.</>
        )}
      </div>
      {sortedPos.length > 0 && (
        <div>
          Unrealised P&L:{" "}
          <strong style={{ color: pnlColor, fontFamily: "monospace" }}>
            {totalPnl >= 0 ? "+" : ""}{ccy} {totalPnl.toFixed(2)}
          </strong>
          {carrier && (carrier.unrealisedAbs ?? 0) > 0 && (
            <>
              {" · "}biggest carry:{" "}
              <strong>{carrier.ticker}</strong>{" "}
              <span style={{ color: "#1fc16b", fontFamily: "monospace" }}>
                +{(carrier.unrealisedAbs ?? 0).toFixed(2)}
              </span>
            </>
          )}
          {dragger && dragger !== carrier && (dragger.unrealisedAbs ?? 0) < 0 && (
            <>
              {" · "}biggest drag:{" "}
              <strong>{dragger.ticker}</strong>{" "}
              <span style={{ color: "#ef4444", fontFamily: "monospace" }}>
                {(dragger.unrealisedAbs ?? 0).toFixed(2)}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * ActivityList — render the chronological event feed. One row per
 * event, colour-coded by kind, with click-through to the relevant
 * detail page (OMS row or session detail). Kept compact so it can
 * coexist with the three existing order panels rather than replacing
 * them — both views serve different scan patterns (timeline vs.
 * bucketed by state).
 */
function ActivityList({ events }: { events: ActivityEvent[] }) {
  return (
    <div
      style={{
        display: "flex", flexDirection: "column", gap: 4,
        maxHeight: 360, overflowY: "auto",
        paddingRight: 4,
      }}
    >
      {events.map((e, i) => {
        const tone = activityTone(e.kind);
        const ts = e.time ? new Date(e.time) : null;
        const body = (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "70px 18px 1fr",
              gap: 8,
              padding: "4px 6px",
              borderRadius: 4,
              fontSize: 12,
              alignItems: "baseline",
              borderLeft: `3px solid ${tone.fg}`,
              background: "rgba(255,255,255,0.015)",
            }}
            title={`${e.kind} · ${e.strategyId ?? ""}`}
          >
            <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-muted)" }}>
              {ts ? ts.toLocaleTimeString([], { hour12: false }) : "—"}
            </span>
            <span style={{ color: tone.fg, textAlign: "center" }}>{tone.icon}</span>
            <span style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <span style={{ color: tone.fg, fontWeight: 600, fontFamily: "monospace" }}>
                {e.kind.replace(/_/g, " ")}
              </span>
              <span style={{ fontFamily: "monospace" }}>{e.label}</span>
              {e.detail && (
                <span style={{ color: "var(--text-dim)", fontSize: 11 }}>
                  {e.detail}
                </span>
              )}
              {e.strategyId && (
                <span style={{ color: "var(--text-muted)", fontSize: 10, marginLeft: "auto" }}>
                  {e.strategyId}
                </span>
              )}
            </span>
          </div>
        );
        if (e.href) {
          return (
            <Link key={i} to={e.href} style={{ textDecoration: "none", color: "inherit" }}>
              {body}
            </Link>
          );
        }
        return <div key={i}>{body}</div>;
      })}
    </div>
  );
}

/**
 * KpiStrip — single-glance status bar pinned at the top of /trader.
 * No new data fetches; reuses what the cockpit already polls. Every
 * KPI is derived from props so the strip stays in sync with the
 * panels below it.
 *
 * KPIs surfaced:
 *   - Cash (free) — the operator's effective buying power.
 *   - Open orders — anything not in a terminal state.
 *   - Fills today — filled orders whose lastStateChange falls on UTC today.
 *   - Today's P&L — sum of unrealised P&L across T212 positions.
 *   - Warnings — passthrough count from the existing warnings panel.
 *
 * Why "today" = UTC: the OMS + T212 timestamps are UTC; mixing local
 * tz would mis-bucket fills near the user's midnight. Trader's wall-
 * clock day matters less than the broker's session boundary.
 */
function KpiStrip({
  cash, orders, positions, warningCount,
}: {
  cash: T212Cash | null;
  orders: OmsOrderRow[];
  positions: T212PosResp | null;
  warningCount: number;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const isOpen = (s: string) =>
    ["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(s);
  const openOrders = orders.filter((o) => isOpen(o.state)).length;
  const fillsToday = orders.filter(
    (o) => o.state === "FILLED" && o.lastStateChangeAtUtc.slice(0, 10) === today,
  ).length;

  // Today's P&L — sum of unrealised across positions if T212 is on.
  // Falls back to "—" when the broker integration is disabled so we
  // never imply a number we don't actually have.
  const pnlSrc = positions?.enabled && positions.positions.length > 0
    ? positions.positions.reduce((n, p) => n + (p.unrealisedAbs ?? 0), 0)
    : null;
  const ccy = cash?.currency ?? positions?.positions[0]?.currency ?? "";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
        gap: 8,
        padding: "10px 14px",
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--bg-hover, rgba(255,255,255,0.03))",
        marginBottom: 12,
      }}
    >
      <KpiCell
        label="Cash (free)"
        value={cash?.free != null
          ? `${ccy} ${cash.free.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
          : "—"}
        sub={cash?.enabled === false ? "T212 disabled" : undefined}
        hint="Cash available for new orders on the selected T212 account (Invest product). Refreshed every cockpit poll."
      />
      <KpiCell
        label="Open orders"
        value={String(openOrders)}
        tone={openOrders > 0 ? "info" : undefined}
        hint="OMS orders in flight — PENDING_APPROVAL / SUBMITTED / WORKING / PARTIALLY_FILLED. Excludes terminal states."
      />
      <KpiCell
        label="Fills today"
        value={String(fillsToday)}
        tone={fillsToday > 0 ? "ok" : undefined}
        hint="Count of OMS orders whose state changed to FILLED today (UTC). Resets at 00:00 UTC."
      />
      <KpiCell
        label="Today's P&L"
        value={pnlSrc == null
          ? "—"
          : `${pnlSrc >= 0 ? "+" : ""}${ccy} ${pnlSrc.toFixed(2)}`}
        tone={pnlSrc == null ? undefined : pnlSrc >= 0 ? "ok" : "down"}
        hint="Sum of unrealised P&L across current T212 positions. Excludes realised P&L from positions already closed today."
      />
      <KpiCell
        label="Warnings"
        value={String(warningCount)}
        tone={warningCount > 0 ? "warn" : undefined}
        hint="Count of issues flagged in the Warnings panel below: T212 / OMS fetch errors, rejected orders, integration failures."
      />
    </div>
  );
}

function KpiCell({
  label, value, sub, tone, hint,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "ok" | "warn" | "down" | "info";
  hint?: string;
}) {
  const fg =
    tone === "ok" ? "#1fc16b" :
    tone === "warn" ? "#f59e0b" :
    tone === "down" ? "#ef4444" :
    tone === "info" ? "#4f8cff" :
    "var(--text)";
  return (
    <div>
      <div style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        {label}
        {hint && <InlineHint text={hint} />}
      </div>
      <div style={{
        fontSize: 18, fontWeight: 700, fontFamily: "monospace",
        color: fg, marginTop: 2,
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

function OrdersTable({
  rows, acting, onApprove, onReject, onCancel, allowApprove,
}: {
  rows: OmsOrderRow[];
  acting: string | null;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onCancel: (id: string) => void;
  allowApprove?: boolean;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
      <thead>
        <tr style={{ color: "var(--text-dim)" }}>
          <th style={posTh}>Time</th>
          <th style={posTh}>Strategy</th>
          <th style={posTh}>Symbol</th>
          <th style={posTh}>Side</th>
          <th style={{ ...posTh, textAlign: "right" }}>Qty</th>
          <th style={posTh}>State</th>
          <th style={posTh}>Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => {
          const busy = acting?.startsWith(o.id);
          return (
            <tr key={o.id} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ ...posTd, fontFamily: "monospace", color: "var(--text-muted)" }}>
                {o.createdAtUtc.slice(11, 19)}
              </td>
              <td style={posTd}>{o.strategyId ?? "—"}</td>
              <td style={posTd}>{o.symbol}</td>
              <td style={{ ...posTd, color: o.side === "BUY" ? "#1fc16b" : "#ef4444" }}>{o.side}</td>
              <td style={{ ...posTd, textAlign: "right", fontFamily: "monospace" }}>{o.qty}</td>
              <td style={posTd}>
                <StatePill state={o.state} />
                {(o.state === "REJECTED" || o.state === "CANCELLED") && o.cancelledReason && (
                  <span
                    title={o.cancelledReason}
                    style={{
                      display: "inline-block", marginLeft: 6,
                      fontSize: 10, color: o.state === "REJECTED" ? "#ef4444" : "var(--text-muted)",
                      maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
                      whiteSpace: "nowrap", verticalAlign: "middle",
                    }}
                  >
                    {o.cancelledReason}
                  </span>
                )}
              </td>
              <td style={{ ...posTd, whiteSpace: "nowrap" }}>
                {allowApprove && o.state === "PENDING_APPROVAL" && (
                  <>
                    <button onClick={() => onApprove(o.id)} disabled={busy} style={miniButton("ok")}>approve</button>{" "}
                    <button onClick={() => onReject(o.id)} disabled={busy} style={miniButton("down")}>reject</button>{" "}
                  </>
                )}
                {["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"].includes(o.state) && (
                  <button onClick={() => onCancel(o.id)} disabled={busy} style={miniButton("muted")}>cancel</button>
                )}
                <Link
                  to="/oms"
                  style={{ ...miniButton("muted"), textDecoration: "none", marginLeft: 4 }}
                >
                  →
                </Link>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/**
 * StatePill — color-coded OMS state badge so the trader can scan
 * "what state is this order in" at a glance instead of reading text.
 * Green = filled. Blue = in-flight. Amber = needs human. Red = bad.
 * Muted gray = terminal benign (cancelled, expired).
 */
function StatePill({ state }: { state: string }) {
  const tone =
    state === "FILLED" ? { bg: "rgba(31,193,107,0.15)", fg: "#1fc16b" } :
    state === "SUBMITTED" || state === "WORKING" || state === "PARTIALLY_FILLED"
      ? { bg: "rgba(79,140,255,0.15)", fg: "#4f8cff" } :
    state === "PENDING_APPROVAL" ? { bg: "rgba(245,158,11,0.15)", fg: "#f59e0b" } :
    state === "REJECTED" ? { bg: "rgba(239,68,68,0.15)", fg: "#ef4444" } :
    { bg: "rgba(255,255,255,0.06)", fg: "var(--text-dim)" };
  return (
    <span style={{
      fontSize: 10, padding: "1px 6px", borderRadius: 999,
      background: tone.bg, color: tone.fg, fontFamily: "monospace",
    }}>
      {state}
    </span>
  );
}

function Stat({
  label, value, big, tone,
}: {
  label: string;
  value: string;
  big?: boolean;
  tone?: "ok" | "down";
}) {
  const fg = tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--text)";
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.04em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: big ? 20 : 14, fontWeight: big ? 700 : 500, color: fg, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}

/**
 * NoFiresDiagnostic — inline explainer shown in the cockpit Strategy
 * signals widget when a session completed but emitted zero fire-*
 * decisions. Without this the operator sees "No fire-* decisions"
 * and has no actionable insight. We surface the top skip reasons
 * (or warmup/data hints when even skips are empty) so the trader
 * can decide whether to widen lookback, switch symbols, or accept
 * that the day genuinely had no trade.
 */
function NoFiresDiagnostic({
  barsSeen,
  skipped,
}: {
  barsSeen: number;
  skipped: DecisionEntry[];
}) {
  // No data at all → broken feed / wrong symbol mapping.
  if (barsSeen === 0 && skipped.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "#f59e0b" }}>
        ⚠ Strategy saw 0 bars + logged 0 decisions. Likely: source
        feed misconfigured or symbols rejected. Open Session Detail
        to inspect the run.
      </div>
    );
  }
  // Bars came in but on_bar never ran (warmup not reached).
  if (skipped.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "#f59e0b" }}>
        ⚠ Saw {barsSeen} bars but logged 0 decisions — warmup likely
        not reached. Try a larger lookback or older session date.
      </div>
    );
  }
  // Aggregate top skip reasons.
  const reasonCounts = new Map<string, number>();
  for (const d of skipped) reasonCounts.set(d.reason, (reasonCounts.get(d.reason) ?? 0) + 1);
  const top = Array.from(reasonCounts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
  return (
    <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
      No fire-* decisions — strategy chose not to trade. Top skip reasons:
      <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
        {top.map(([reason, count]) => (
          <li key={reason}>
            <span style={{ fontFamily: "monospace" }}>{reason}</span>
            {" "}
            <span style={{ color: "var(--text-muted)" }}>×{count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * TestPlacementPanel — operator smoke test for the OMS → T212 demo
 * chain. Bypasses the strategy / Mac daemon entirely: submits an
 * OrderIntent directly to /api/oms/orders and auto-approves so the
 * .NET OmsService → Trading212DemoClient → T212 demo path runs end-
 * to-end. Useful for verifying the broker wiring before triggering a
 * real strategy session, and for sanity checks after a redeploy.
 *
 * Defaults: BUY 1 AAPL (small enough to not move T212's demo cash
 * meaningfully; symbol T212 always has). Operator can override.
 *
 * After approve, the fill poller picks up T212's fill within ~30s
 * and transitions OMS to FILLED — visible in cockpit "Order placed"
 * → "Trade executed".
 */
function TestPlacementPanel({ onPlaced }: { onPlaced: () => void }) {
  const [symbol, setSymbol] = useState("AAPL");
  const [qty, setQty] = useState(1);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const fire = async () => {
    setSubmitting(true);
    setFeedback(null);
    try {
      // Generate a uuid for ClientOrderId — browser crypto API gives
      // us this without an extra library. OMS dedupes on it so a
      // double-click doesn't double-place.
      const clientOrderId = crypto.randomUUID();
      const enqueued = await api.omsEnqueue({
        ClientOrderId: clientOrderId,
        Broker: "T212_DEMO",
        Symbol: symbol.toUpperCase(),
        Side: side,
        Qty: qty,
        OrderType: "MKT",
        StrategyId: "manual_test_cockpit",
        PlacedBy: "HUMAN",
        TimeInForce: "DAY",
      });
      // Auto-approve so it actually places at T212.
      await api.omsApprove(enqueued.id);
      setFeedback(
        `✓ Enqueued + approved ${side} ${qty} ${symbol.toUpperCase()} — watch "Order placed" / "Trade executed" panels.`,
      );
      onPlaced();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.5 }}>
        Bypasses the strategy + Mac daemon — creates an OMS intent
        directly + auto-approves so the .NET OmsService → T212 demo
        chain runs end-to-end. Use after a redeploy to verify nothing
        broke before triggering a real strategy run.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <FieldGroup label="Symbol">
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            style={triggerInput}
          />
        </FieldGroup>
        <FieldGroup label="Side">
          <div style={{ display: "flex", gap: 4 }}>
            {(["BUY", "SELL"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                style={{
                  ...triggerInput,
                  width: 56,
                  cursor: "pointer",
                  color: side === s
                    ? s === "BUY" ? "#1fc16b" : "#ef4444"
                    : "var(--text-dim)",
                  borderColor: side === s
                    ? s === "BUY" ? "#1fc16b" : "#ef4444"
                    : "var(--border)",
                  fontWeight: side === s ? 600 : 400,
                  textAlign: "center",
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </FieldGroup>
        <FieldGroup label="Qty">
          <input
            type="number"
            min={1}
            max={1000}
            value={qty}
            onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))}
            style={{ ...triggerInput, width: 80 }}
          />
        </FieldGroup>
        <button
          onClick={fire}
          disabled={submitting}
          style={{
            padding: "6px 14px", fontSize: 12, fontWeight: 600,
            background: submitting ? "var(--text-muted)" : "#4f8cff",
            color: "white", border: "none", borderRadius: 4,
            cursor: submitting ? "wait" : "pointer",
          }}
        >
          {submitting ? "Placing…" : `Fire ${side} ${qty} ${symbol.toUpperCase()}`}
        </button>
      </div>
      {feedback && (
        <div style={{
          marginTop: 8, fontSize: 11,
          color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
        }}>
          {feedback}
        </div>
      )}
    </div>
  );
}

/**
 * TriggerPanel — compact form to fire a strategy session without
 * navigating to /strategies. Loads the strategy catalog once,
 * renders the strategies as pills (no dropdowns per memory rule),
 * click expands an inline form with symbol + date + lookback +
 * Run. Defaults pre-filled from strategy.default_lookback_days.
 */
function TriggerPanel({ onTriggered }: { onTriggered: () => void }) {
  type Strat = Awaited<ReturnType<typeof api.paperStrategies>>["strategies"][number];
  type Universe = Awaited<ReturnType<typeof api.universes>>["universes"][number];
  const [strategies, setStrategies] = useState<Strat[]>([]);
  const [universes, setUniverses] = useState<Universe[]>([]);
  const [selected, setSelected] = useState<Strat | null>(null);
  // Symbols editable as a comma-separated string so trader can paste
  // / hand-curate after picking a universe. Run() splits + cleans.
  const [symbolsText, setSymbolsText] = useState("");
  const [pickedUniverse, setPickedUniverse] = useState<string | null>(null);
  const [loadingUniverse, setLoadingUniverse] = useState(false);
  const todayIso = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayIso);
  const [lookback, setLookback] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.paperStrategies()
      .then((r) => { if (!cancelled) setStrategies(r.strategies); })
      .catch((e) => { if (!cancelled) setFeedback(`Strategy catalog failed: ${e}`); });
    // Universe catalog — optional (older API images won't have the
    // endpoint). Silent on failure so the trigger form still works.
    api.universes()
      .then((r) => { if (!cancelled) setUniverses(r.universes); })
      .catch(() => { /* universe pipeline not yet ingested */ });
    return () => { cancelled = true; };
  }, []);

  const pick = (s: Strat) => {
    setSelected(s);
    setLookback(s.default_lookback_days ?? 0);
    if (s.name === "ichimoku_fx_mr") setSymbolsText("");
    else if (!symbolsText) setSymbolsText("AAPL,MSFT,NVDA,TSLA");
  };

  // Pick a universe → fetch its symbols (effective only — applies
  // include/exclude overrides server-side) and replace the symbols
  // textbox. Trader can edit afterwards (sometimes you want to scan
  // a subset).
  const pickUniverse = async (name: string) => {
    setLoadingUniverse(true);
    setPickedUniverse(name);
    try {
      const u = await api.universe(name);
      const tickers = u.symbols.filter((s) => s.effective).map((s) => s.ticker);
      setSymbolsText(tickers.join(","));
      setFeedback(`Loaded ${tickers.length} symbols from ${name}`);
    } catch (e) {
      setFeedback(`Universe load failed: ${e}`);
    } finally {
      setLoadingUniverse(false);
    }
  };

  const run = async () => {
    if (!selected) return;
    const isFx = selected.name === "ichimoku_fx_mr";
    const symbols = isFx ? [] :
      symbolsText.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (!isFx && symbols.length === 0) {
      setFeedback("Enter at least one symbol before triggering");
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      await api.runIntraday({
        strategy: selected.name,
        symbols,
        session_date: date,
        lookback_days: lookback ?? 0,
        params: selected.default_params,
      });
      setFeedback(`✓ Queued ${selected.name} on ${symbols.length || "G10"} symbols for ${date}`);
      onTriggered();
    } catch (e) {
      setFeedback(`Failed: ${e}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      {strategies.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Loading strategies…
        </div>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
            {strategies.map((s) => {
              const isSelected = selected?.name === s.name;
              const tone = s.source === "trader-quant" ? "#1fc16b"
                : s.source === "alpha-engine" ? "#4f8cff"
                : "var(--text-dim)";
              return (
                <button
                  key={s.name}
                  onClick={() => pick(s)}
                  style={{
                    padding: "4px 11px", fontSize: 11, borderRadius: 999,
                    border: `1px solid ${isSelected ? tone : "var(--border)"}`,
                    background: isSelected ? `${tone}1a` : "transparent",
                    color: isSelected ? tone : "var(--text-dim)",
                    cursor: "pointer", fontFamily: "monospace",
                  }}
                  title={s.summary}
                >
                  {s.name}
                </button>
              );
            })}
          </div>
          {/* Universe picker — only render when there's any ingested
              + when a non-FX strategy is selected. Pills, not a
              dropdown, per memory rule. */}
          {selected && selected.name !== "ichimoku_fx_mr" && universes.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10, alignItems: "baseline" }}>
              <span style={{
                fontSize: 9, color: "var(--text-muted)",
                textTransform: "uppercase", letterSpacing: "0.06em",
              }}>
                Universe
              </span>
              {universes.map((u) => {
                const isPicked = pickedUniverse === u.name;
                return (
                  <button
                    key={u.name}
                    onClick={() => void pickUniverse(u.name)}
                    disabled={loadingUniverse}
                    title={`${u.symbolCount} symbols · fetched ${new Date(u.fetchedAtUtc).toLocaleString()}${
                      u.excludedOverrides ? ` · ${u.excludedOverrides} excluded by you` : ""
                    }`}
                    style={{
                      padding: "3px 9px", fontSize: 10, borderRadius: 999,
                      border: `1px solid ${isPicked ? "#a855f7" : "var(--border)"}`,
                      background: isPicked ? "rgba(168,85,247,0.10)" : "transparent",
                      color: isPicked ? "#a855f7" : "var(--text-dim)",
                      cursor: loadingUniverse ? "wait" : "pointer",
                      fontFamily: "monospace", letterSpacing: "0.02em",
                    }}
                  >
                    {u.name}
                    <span style={{ marginLeft: 4, opacity: 0.7 }}>
                      {u.symbolCount - u.excludedOverrides}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {selected && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
              <FieldGroup label={`Symbols${symbolsText ? ` (${symbolsText.split(",").filter(Boolean).length})` : ""}`}>
                <textarea
                  placeholder={selected.name === "ichimoku_fx_mr" ? "G10 (auto)" : "AAPL,MSFT,NVDA — or pick a Universe pill above"}
                  value={symbolsText}
                  onChange={(e) => setSymbolsText(e.target.value)}
                  disabled={selected.name === "ichimoku_fx_mr"}
                  rows={2}
                  style={{ ...triggerInput, width: 280, fontFamily: "monospace", resize: "vertical" }}
                />
              </FieldGroup>
              <FieldGroup label="Session date">
                <input
                  type="date"
                  value={date}
                  max={todayIso}
                  onChange={(e) => setDate(e.target.value)}
                  style={triggerInput}
                />
              </FieldGroup>
              <FieldGroup label="Lookback (days)">
                <input
                  type="number"
                  min={0}
                  max={365}
                  value={lookback ?? 0}
                  onChange={(e) => setLookback(Number(e.target.value))}
                  style={{ ...triggerInput, width: 70 }}
                />
              </FieldGroup>
              <button
                onClick={run}
                disabled={submitting}
                style={{
                  padding: "6px 14px", fontSize: 12, fontWeight: 600,
                  background: submitting ? "var(--text-muted)" : "#1fc16b",
                  color: "white", border: "none", borderRadius: 4,
                  cursor: submitting ? "wait" : "pointer",
                }}
              >
                {submitting ? "Queueing…" : "Run"}
              </button>
            </div>
          )}
          {feedback && (
            <div style={{
              marginTop: 8, fontSize: 11,
              color: feedback.startsWith("✓") ? "#1fc16b" : "var(--down)",
            }}>
              {feedback}
            </div>
          )}
          {selected && (
            <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
              {selected.summary}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 9, color: "var(--text-muted)", letterSpacing: "0.04em", textTransform: "uppercase" }}>
        {label}
      </span>
      {children}
    </div>
  );
}

const triggerInput: React.CSSProperties = {
  padding: "5px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text)",
};

function fmtMoney(n: number | null | undefined, ccy?: string | null): string {
  if (n == null) return "—";
  const c = ccy || "";
  return `${c} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const posTh: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", fontSize: 10, fontWeight: 700,
  letterSpacing: "0.04em", textTransform: "uppercase",
};
const posTd: React.CSSProperties = { padding: "4px 8px" };

function miniButton(tone: "ok" | "down" | "muted"): React.CSSProperties {
  const fg = tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--text-dim)";
  const border = tone === "ok" ? "#1fc16b" : tone === "down" ? "#ef4444" : "var(--border)";
  return {
    fontSize: 10, padding: "2px 7px",
    border: `1px solid ${border}`, borderRadius: 3,
    background: "transparent", color: fg, cursor: "pointer",
    fontWeight: 500,
  };
}
