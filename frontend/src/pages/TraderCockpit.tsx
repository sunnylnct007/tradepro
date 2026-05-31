import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../components/CockpitCard";
import { PlotlyChart } from "../components/PlotlyChart";
import { TriggerPanel } from "../components/cockpit/TriggerPanel";
import { TradeCardsPanel } from "../components/cockpit/TradeCardsPanel";
import { ActivityList } from "../components/cockpit/ActivityList";
import { todayHeadline } from "../components/cockpit/TodayOutcome";
import { OrdersTable } from "../components/cockpit/OrdersTable";
import { StrategyChartsCard } from "../components/cockpit/StrategyChartsCard";
import { PositionChartsCard } from "../components/cockpit/PositionChartsCard";
import { PositionsPanel } from "../components/cockpit/PositionsPanel";
import { StrategyDesks } from "../components/cockpit/StrategyDesks";
import { OrdersByBrokerPanel } from "../components/cockpit/OrdersByBrokerPanel";
import { LiveSignalFeed } from "../components/cockpit/LiveSignalFeed";
import { SymbolScanGrid } from "../components/cockpit/SymbolScanGrid";
import { useHiddenWidgets, type WidgetMeta } from "../components/cockpit/useHiddenWidgets";
import { HiddenWidgetsBar } from "../components/cockpit/HiddenWidgetsBar";
import { BrokerCashStrip } from "../components/cockpit/BrokerCashStrip";
import { AlertBanner } from "../components/cockpit/AlertBanner";
import { api, OmsOrderRow } from "../api/client";
import { config } from "../config";
import { buildOrderLifecycleFigure } from "../viz/orderLifecycle";
import { buildActivityFeed } from "../viz/activityFeed";
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
      const completed = sessions.filter((s) => s.state === "Completed");
      // Group by strategy and pick the newest per strategy.
      const byStrategy = new Map<string, LatestSession>();
      for (const s of completed) {
        const rs = (s.result_summary ?? {}) as Record<string, unknown>;
        const strategy = (rs.strategy as string) || (s.params as Record<string, unknown>)?.strategy as string;
        if (!strategy) continue;
        if (byStrategy.has(strategy)) continue;
        const decisions: DecisionEntry[] = [];
        let barsSeen = 0;
        const charts: Record<string, unknown> = {};
        // Top-level result_summary.charts (the quant-backtest CLI
        // emits here; ichimoku_equity emits per-strategy).
        const topCharts = rs.charts as Record<string, unknown> | undefined;
        if (topCharts && typeof topCharts === "object") {
          Object.assign(charts, topCharts);
        }
        // Two shapes coexist in result_summary depending on which
        // engine produced it:
        //
        //   • quant-backtest CLI: top-level `strategies: [...]`,
        //     one entry per strategy_id, with .decisions / .bars_seen
        //     / .charts attached. Walk it directly.
        //
        //   • intraday engine (/scan): per-symbol `results: [{symbol,
        //     strategies: [...], ...}]` — same strategies-list shape
        //     nested inside each symbol's result so the heatmap can
        //     show per-symbol decisions. Walk results[].strategies[].
        //
        // Either way each strategy entry has the same .decisions /
        // .bars_seen / .charts fields, so we can fold both into one
        // reducer.
        const visitStrategy = (st: Record<string, unknown>, fallbackSymbol?: string) => {
          const bs = st.bars_seen as Array<unknown> | undefined;
          if (Array.isArray(bs)) barsSeen += bs.length;
          const sc = st.charts as Record<string, unknown> | undefined;
          if (sc && typeof sc === "object") Object.assign(charts, sc);
          const ds = st.decisions as Array<Record<string, unknown>>;
          if (!Array.isArray(ds)) return;
          for (const d of ds) {
            decisions.push({
              barTs: (d.bar_ts as string) || null,
              // Decisions from the intraday engine don't always carry
              // .symbol (the strategy_id encodes it). Fall back to the
              // parent results[i].symbol so the cockpit grid groups
              // properly.
              symbol: (d.symbol as string) || fallbackSymbol || "",
              action: (d.action as string) || "",
              reason: (d.reason as string) || "",
              detail: (d.detail as Record<string, unknown>) || {},
            });
          }
        };
        const topStrategies = (rs.strategies as Array<Record<string, unknown>>) || [];
        for (const st of topStrategies) {
          visitStrategy(st);
        }
        const perSymbol = (rs.results as Array<Record<string, unknown>>) || [];
        for (const r of perSymbol) {
          const sym = (r.symbol as string) || "";
          const nested = (r.strategies as Array<Record<string, unknown>>) || [];
          for (const st of nested) {
            visitStrategy(st, sym);
          }
        }
        // Sort newest-first; cap to 30 per strategy to keep render light.
        decisions.sort((a, b) => (b.barTs ?? "").localeCompare(a.barTs ?? ""));
        byStrategy.set(strategy, {
          strategy,
          requestId: s.request_id,
          completedAtUtc: s.completed_at_utc,
          decisions: decisions.slice(0, 30),
          barsSeen,
          charts,
        });
      }
      // Merge charts from /api/paper/snapshots — those land via
      // `tradepro-paper --push` (different ingestion path) but the
      // cockpit only fetches /api/ops/sessions natively. Without
      // this, the Strategy Charts card stays empty even when the
      // strategy emitted recent_charts data. Best-effort: snapshot
      // fetch failure leaves charts blank, doesn't break the card.
      try {
        const snapshots = await api.paperSnapshots();
        const today = new Date().toISOString().slice(0, 10);
        for (const snap of snapshots) {
          if (!snap.sessionLabel.endsWith(today)) continue;
          const strategyName = snap.sessionLabel.replace(`-${today}`, "");
          const detail = await api.paperSnapshot(snap.sessionLabel) as
            { strategies?: Array<{ charts?: Record<string, unknown> }> };
          const charts: Record<string, unknown> = {};
          for (const st of detail.strategies ?? []) {
            if (st.charts && typeof st.charts === "object") {
              Object.assign(charts, st.charts);
            }
          }
          if (Object.keys(charts).length === 0) continue;
          const existing = byStrategy.get(strategyName);
          if (existing) {
            existing.charts = { ...existing.charts, ...charts };
          } else {
            byStrategy.set(strategyName, {
              strategy: strategyName,
              requestId: snap.sessionLabel,
              completedAtUtc: snap.asOfUtc,
              decisions: [],
              barsSeen: 0,
              charts,
            });
          }
        }
      } catch {
        // ignore — charts panel just stays empty.
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
  // "Recent" = today (UTC) only. Yesterday's fills are by definition
  // not actionable today; surfacing them as "recent" misleads the
  // trader (especially for PAPER-broker simulated fills that never
  // hit the broker but still show FILLED). Drill into /oms for full
  // history.
  // Reconcile/manual bookkeeping (strategy-less + HUMAN-placed) — e.g.
  // the synthetic fills "Sync OMS ← broker" writes. These are NOT trading
  // activity, so they must not show as "trades executed" / "orders today".
  const isReconcileLike = (o: OmsOrderRow) =>
    !o.strategyId && o.placedBy === "HUMAN";
  // Trading orders only — the feeds below are about strategy activity.
  const tradingOrders = orders.filter((o) => !isReconcileLike(o));

  const recentTodayUtc = new Date().toISOString().slice(0, 10);
  const recent = tradingOrders
    .filter((o) =>
      (o.state === "FILLED" || o.state === "PARTIALLY_FILLED") &&
      o.lastStateChangeAtUtc.slice(0, 10) === recentTodayUtc,
    )
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
    { id: "desks",        title: "Strategy desks" },
    { id: "warnings",     title: "Warnings" },
    { id: "broker-cash",  title: "Broker cash (multi)" },
    { id: "trigger",      title: "Trigger session" },
    { id: "orders-by-broker", title: "Orders today (by broker)" },
    { id: "intents",     title: "Order generated" },
    { id: "submitted",   title: "Order placed" },
    { id: "fills",       title: "Trade executed" },
    { id: "activity",    title: "Activity feed" },
    { id: "trade-cards",      title: "Trade cards" },
    { id: "position-charts",  title: "Position charts" },
    { id: "live-signal",      title: "Live signal feed" },
    { id: "scan-grid",   title: "Symbol scan grid" },
    { id: "charts",      title: "Strategy charts" },
    { id: "lifecycle",   title: "Order lifecycle (Gantt)" },
    { id: "signals",     title: "Strategy signals" },
    { id: "positions-equity", title: "Equity positions" },
    { id: "positions-fx",     title: "FX positions" },
  ];
  // Trader-first default. Cockpit ships with the analyst-flavoured
  // widgets hidden so the trader sees only essentials: positions,
  // pending action, today's signals. Restore any from the
  // HiddenWidgetsBar above the grid. Persists per-trader (localStorage)
  // — once they un-hide, that choice survives reloads.
  // Desks-first home: the trader sees STRATEGY DESKS + action (approvals,
  // warnings) by default; everything analyst-flavoured starts hidden and
  // is one click away on the HiddenWidgetsBar. "Strategy desks" already
  // summarises positions per strategy, so the broker→product positions
  // cards + the per-symbol charts default hidden (drill-in, not front page).
  const widgets = useHiddenWidgets("cockpit.hidden", [
    "submitted",            // covered by orders-by-broker
    "fills",                // covered by orders-by-broker
    "activity",             // verbose
    "lifecycle",            // analyst Gantt
    "signals",              // verbose decisions table
    "trade-cards",          // verbose
    "trigger",              // 1×/day at most
    "broker-cash",          // detail; desks show per-desk alloc, KPI shows cash
    "position-charts",      // per-symbol Ichimoku charts — drill-in
    "charts",               // strategy charts — drill-in
    "scan-grid",            // analyst
    "live-signal",          // analyst feed
    "positions-equity",     // superseded by Strategy desks (drill-in)
    "positions-fx",         // superseded by Strategy desks (drill-in)
  ]);
  const v = (id: string) => !widgets.isHidden(id);

  const approveAll = async () => {
    if (!pending.length) return;
    if (!confirm(`Approve all ${pending.length} pending intent${pending.length === 1 ? "" : "s"}?`)) return;
    for (const o of pending) {
      try { await api.omsApprove(o.id); } catch { /* keep going through the queue */ }
    }
    await loadOrders();
  };

  // Top-N approve — when a universe scan fires on (e.g.) 200 symbols
  // the trader doesn't want to approve all 200 manually. Pick the N
  // most-recent intents (proxy for "freshest signal") and approve only
  // those. Today's ranking is just "newest first"; once strategies
  // start emitting a numeric `confidence` we can rank by that.
  // The cap defaults to whatever's stored in app_settings_kv key
  // `top_n_signals_per_run` (default 5).
  const [topN, setTopN] = useState<number>(5);
  useEffect(() => {
    api.settingsKv()
      .then((r) => {
        const row = r.settings.find((s) => s.key === "top_n_signals_per_run");
        if (row && typeof row.value === "number") setTopN(row.value);
      })
      .catch(() => { /* settings endpoint optional */ });
  }, []);

  const approveTopN = async () => {
    if (!pending.length) return;
    const sorted = [...pending].sort(
      (a, b) => b.createdAtUtc.localeCompare(a.createdAtUtc),
    );
    const cap = Math.min(topN, sorted.length);
    const targets = sorted.slice(0, cap);
    if (!confirm(
      `Approve the ${cap} most-recent intent${cap === 1 ? "" : "s"}? ` +
      `Reject the remaining ${sorted.length - cap}.`,
    )) return;
    for (const o of targets) {
      try { await api.omsApprove(o.id); } catch { /* skip */ }
    }
    // The rest get rejected so the queue doesn't accumulate stale
    // ones the trader never decided on. Auto-reject reason is
    // explicit so the audit trail says why.
    for (const o of sorted.slice(cap)) {
      try { await api.omsReject(o.id, `auto_top${cap}_skip`); } catch { /* skip */ }
    }
    await loadOrders();
  };

  return (
    <div style={{ padding: 20, maxWidth: 1280, margin: "0 auto" }}>
      {/* Operational alerts the trader must see first — e.g. a strategy
          that aborted because it couldn't confirm its broker position. */}
      <AlertBanner />
      {/* ── Account selector strip ──────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Trader cockpit</h1>
        {/* Today's activity status inline with the title (was a separate
            TODAY card). The P&L / carry-drag now live on the desks. */}
        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
          {todayHeadline(orders, positions, latestSessions)}
        </span>
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
        {account === "live" && (
          <div style={{
            padding: "4px 10px", fontSize: 11, fontWeight: 600,
            color: "#ef4444",
            background: "rgba(239,68,68,0.10)",
            border: "1px solid rgba(239,68,68,0.45)",
            borderRadius: 999,
            letterSpacing: "0.03em",
          }}>
            ⚠ Viewing T212 LIVE — the algo trades DEMO. Switch to DEMO to see algo P&amp;L.
          </div>
        )}
      </div>

      {/* System health pills moved to /health — too IT-flavoured for
          the trader's daily cockpit. Surfaced via the "More ▾" nav
          + the warnings panel still raises here when something's
          actually wrong with a broker / daemon. */}

      {/* KPI strip removed — it was T212-only + mislabeled ("Today's P&L"
          was actually total T212 unrealised; "Open orders" counted stale
          mis-routed orders). The Strategy-desks portfolio strip is the
          accurate, multi-broker summary now. */}

      {/* TODAY card removed — its status line is now inline with the
          title (todayHeadline) and its P&L / carry-drag live on the
          Strategy desks. */}

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
          // min(420px,100%) so a phone (≪420px) gets a single full-width
          // column instead of a 420px column overflowing the viewport.
          gridTemplateColumns: "repeat(auto-fit, minmax(min(420px, 100%), 1fr))",
          gap: 12,
          alignItems: "start",
        }}
      >
      {/* ══ MAIN SECTION — STRATEGY DESKS ═════════════════════════
           The trader's first read: each strategy as a desk (broker ×
           asset class) with its P&L, positions, status, reconcile.
           Click a desk to drill into its positions. Everything else is
           secondary / hidden by default. */}
      {v("desks") && (
        <StrategyDesks
          positions={positions}
          onHide={() => widgets.hide("desks")}
        />
      )}

      {/* Detailed broker→product positions (with flatten / sync) —
           hidden by default; restore from the hidden-widgets bar to
           drill in. Superseded on the home by Strategy desks. */}
      {(v("positions-equity") || v("positions-fx")) && (
        <PositionsPanel
          positions={positions}
          posErr={posErr}
          account={account}
          showEquity={v("positions-equity")}
          showFx={v("positions-fx")}
          onHide={(id) => widgets.hide(id)}
          onSyncOms={(broker) => api.syncOmsFromBroker(broker)}
        />
      )}
      {v("position-charts") && (
        <PositionChartsCard
          positions={positions}
          latestSessions={latestSessions}
          onHide={() => widgets.hide("position-charts")}
        />
      )}

      {/* Connectivity moved to the top-bar traffic light (ConnectivityBadge)
          — it's chrome, not a trading surface. */}

      {/* ── Broker cash strip — every connected broker in one row */}
      {v("broker-cash") && (
      <CockpitCard id="broker-cash" title="Broker cash (T212 demo · IG · IBKR)" defaultOpen={true}
        onHide={() => widgets.hide("broker-cash")}
      >
        <BrokerCashStrip />
      </CockpitCard>
      )}

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
            <Link
              key={i}
              to="/oms"
              style={{
                display: "block", fontSize: 12, padding: "4px 0",
                color: w.tone === "down" ? "var(--down)" : "#d97706",
                textDecoration: "none", cursor: "pointer",
              }}
              title="Open OMS to investigate"
            >
              {w.text} →
            </Link>
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

      {/* Manual test placement lives on /admin/data — it's an IT
          smoke-test, not a trader workflow. Kept out of the cockpit
          so the trader screen stays decision-focused. */}

      {/* Standalone Cash card removed — it duplicated the multi-broker
          "Broker cash" strip (and the KPI strip's Cash stat). One money
          summary, not three. */}

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
          <>
            {pending.length > topN && (
              <button
                onClick={approveTopN}
                style={miniButton("ok")}
                title={
                  `Approve the ${topN} most-recent (top of queue) and ` +
                  `reject the rest. N is configurable in Settings ` +
                  `(top_n_signals_per_run).`
                }
              >
                approve top {topN}
              </button>
            )}
            {" "}
            <button
              onClick={approveAll}
              style={miniButton("muted")}
              title="Approve every pending intent"
            >
              approve all
            </button>
          </>
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

      {/* ── Orders today, by broker — the COMPLETE picture incl.
              cancelled/rejected (which the state panels below omit), so
              an FX rejection storm can't hide. */}
      {v("orders-by-broker") && (
      <CockpitCard
        id="orders-by-broker"
        title="Orders today — by broker"
        badge={orders.length || undefined}
        defaultOpen
        fullWidth
        onHide={() => widgets.hide("orders-by-broker")}
      >
        <OrdersByBrokerPanel orders={tradingOrders} />
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
        title="Trade executed today"
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

      {/* ── Symbol scan grid (compact card per symbol) ───────────── */}
      {v("scan-grid") && (
        <SymbolScanGrid
          latestSessions={latestSessions}
          onHide={() => widgets.hide("scan-grid")}
        />
      )}

      {/* ── Live signal feed — chronological feed showing the
              chain happening NOW: SIGNAL → ORDER → FILL. Closes the
              "haven't seen a proper signal flowing yet" gap. */}
      {v("live-signal") && (
        <CockpitCard
          id="live-signal"
          title="Live signal feed — signal → order → fill"
          defaultOpen={true}
          fullWidth
          onHide={() => widgets.hide("live-signal")}
        >
          <LiveSignalFeed />
        </CockpitCard>
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

      </div>
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
  // No data at all → most often the market was simply closed (no bars
  // published), e.g. FX on a weekend or equities outside the session.
  // A feed/mapping problem is the less-likely runner-up.
  if (barsSeen === 0 && skipped.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "#f59e0b" }}>
        ⚠ 0 bars, 0 decisions — usually the market was closed (weekend /
        outside session hours) so no data was published. If the market
        was open, the feed may be misconfigured or symbols rejected —
        open Session Detail to inspect.
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
