import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../components/CockpitCard";
import { InlineHint } from "../components/InlineHint";
import { PlotlyChart } from "../components/PlotlyChart";
import { SystemHealthRow } from "../components/cockpit/SystemHealthRow";
import { TriggerPanel } from "../components/cockpit/TriggerPanel";
import { TradeCardsPanel } from "../components/cockpit/TradeCardsPanel";
import { TestPlacementPanel } from "../components/cockpit/TestPlacementPanel";
import { useHiddenWidgets, type WidgetMeta } from "../components/cockpit/useHiddenWidgets";
import { HiddenWidgetsBar } from "../components/cockpit/HiddenWidgetsBar";
import { api, OmsOrderRow } from "../api/client";
import { config } from "../config";
import { buildOrderLifecycleFigure } from "../viz/orderLifecycle";
import { buildActivityFeed, activityTone, type ActivityEvent } from "../viz/activityFeed";
import type { T212Cash, DecisionEntry, LatestSession, T212PosResp } from "../types/cockpit";

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

  // Widget visibility — per-trader localStorage persistence. Catalog
  // is declared once + the cockpit renders each CockpitCard wrapped
  // in `isVisible(id) ? ... : null`. Adding a new widget = add an
  // entry to WIDGETS + wrap its render. Lifecycle is symmetric:
  // click × on the card → moves to HiddenWidgetsBar; click the pill
  // → restored.
  const WIDGETS: WidgetMeta[] = [
    { id: "warnings",    title: "Warnings" },
    { id: "trigger",     title: "Trigger session" },
    { id: "testorder",   title: "Test placement" },
    { id: "cash",        title: "Cash" },
    { id: "intents",     title: "Order generated" },
    { id: "submitted",   title: "Order placed" },
    { id: "fills",       title: "Trade executed" },
    { id: "activity",    title: "Activity feed" },
    { id: "trade-cards", title: "Trade cards" },
    { id: "charts",      title: "Strategy charts" },
    { id: "lifecycle",   title: "Order lifecycle (Gantt)" },
    { id: "signals",     title: "Strategy signals" },
    { id: "positions",   title: "Overall position" },
  ];
  const widgets = useHiddenWidgets("cockpit.hidden");
  const v = (id: string) => !widgets.isHidden(id);

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
      {/* Hidden widgets toolbar — pills to restore anything the
          trader has × out of the panel grid. */}
      <HiddenWidgetsBar
        widgets={WIDGETS}
        hidden={widgets.hidden}
        onShow={widgets.show}
        onShowAll={widgets.showAll}
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))",
          gap: 12,
          alignItems: "start",
        }}
      >
      {/* ── Warnings (only visible when any) ────────────────────── */}
      {v("warnings") && warnings.length > 0 && (
        <CockpitCard
          id="warnings"
          title="Warnings"
          badge={warnings.length}
          tone="warn"
          onHide={() => widgets.hide("warnings")}
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
      {v("trigger") && (
      <CockpitCard id="trigger" title="Trigger session" defaultOpen={false}
        onHide={() => widgets.hide("trigger")}
      >
        <TriggerPanel onTriggered={() => { void loadOrders(); void loadSessions(); }} />
      </CockpitCard>
      )}

      {/* ── Manual test placement (skip the strategy, smoke the chain) */}
      {v("testorder") && (
      <CockpitCard id="testorder" title="Test placement (manual OMS → T212 demo)" defaultOpen={false}
        onHide={() => widgets.hide("testorder")}
      >
        <TestPlacementPanel onPlaced={() => void loadOrders()} />
      </CockpitCard>
      )}

      {/* ── Cash ─────────────────────────────────────────────────── */}
      {v("cash") && (
      <CockpitCard
        id="cash"
        title="Cash"
        badge={cash?.free != null ? `${cash.currency ?? ""} ${cash.free.toLocaleString()}` : undefined}
        tone="ok"
        onHide={() => widgets.hide("cash")}
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
      )}

      {/* ── Pending intents (action required) ───────────────────── */}
      {v("intents") && (
      <CockpitCard
        id="intents"
        title="Order generated — awaiting approval"
        badge={pending.length || undefined}
        tone={pending.length > 0 ? "warn" : "default"}
        defaultOpen
        onHide={() => widgets.hide("intents")}
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
      )}

      {/* ── Submitted (in flight at broker) ─────────────────────── */}
      {v("submitted") && (
      <CockpitCard
        id="submitted"
        title="Order placed — awaiting fill"
        badge={submitted.length || undefined}
        onHide={() => widgets.hide("submitted")}
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
      )}

      {/* ── Recent fills ────────────────────────────────────────── */}
      {v("fills") && (
      <CockpitCard
        id="fills"
        title="Trade executed (recent)"
        badge={recent.length || undefined}
        tone={recent.length > 0 ? "ok" : "default"}
        onHide={() => widgets.hide("fills")}
      >
        {recent.length === 0 ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No fills yet.</span>
        ) : (
          <OrdersTable rows={recent} acting={null} onApprove={() => {}} onReject={() => {}} onCancel={() => {}} />
        )}
      </CockpitCard>
      )}

      {/* ── Activity feed (unified signal / order / fill timeline) */}
      {v("activity") && (
      <CockpitCard
        id="activity"
        title="Activity feed"
        badge={activityEvents.length || undefined}
        defaultOpen
        fullWidth
        onHide={() => widgets.hide("activity")}
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
      )}

      {/* ── Trade cards (signal → order → fill → P&L chain) ──────── */}
      {v("trade-cards") && (
        <TradeCardsPanel
          orders={orders}
          positions={positions}
          latestSessions={latestSessions}
          onHide={() => widgets.hide("trade-cards")}
        />
      )}

      {/* ── Strategy charts (Ichimoku cloud per symbol) ────────── */}
      {v("charts") && (
        <StrategyChartsCard
          latestSessions={latestSessions}
          onHide={() => widgets.hide("charts")}
        />
      )}

      {/* ── Order lifecycle Gantt ──────────────────────────────── */}
      {v("lifecycle") && (
      <CockpitCard
        id="lifecycle"
        title="Order lifecycle (Gantt)"
        badge={orders.length || undefined}
        defaultOpen={false}
        fullWidth
        onHide={() => widgets.hide("lifecycle")}
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
      )}

      {/* ── Strategy signals (validate without broker) ──────────── */}
      {v("signals") && latestSessions.length > 0 && (
        <CockpitCard
          id="signals"
          title="Strategy signals — latest run per strategy"
          badge={latestSessions.reduce((n, s) =>
            n + s.decisions.filter((d) => d.action.startsWith("fire-")).length, 0) || undefined}
          tone="ok"
          fullWidth
          onHide={() => widgets.hide("signals")}
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
      {v("positions") && (
      <CockpitCard
        id="positions"
        title="Overall position"
        badge={positions?.enabled ? positions.positionCount || undefined : undefined}
        onHide={() => widgets.hide("positions")}
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
      )}
      </div>
    </div>
  );
}


/**
 * StrategyChartsCard — embed any Plotly figure the strategy emitted
 * via recent_charts() directly on the cockpit. Today that's the
 * per-symbol Ichimoku cloud + fill markers from ichimoku_equity.
 * Defaults to closed so the heavy plotly.js bundle only lazy-loads
 * when the trader explicitly opens it.
 */
function StrategyChartsCard({
  latestSessions, onHide,
}: {
  latestSessions: LatestSession[];
  onHide?: () => void;
}) {
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
      onHide={onHide}
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
