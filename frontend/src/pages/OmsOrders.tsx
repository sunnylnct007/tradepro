import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, OmsOrderRow, OmsOrderEventRow } from "../api/client";
import { PaperSubNav } from "../components/PaperSubNav";
import { PlotlyChart } from "../components/PlotlyChart";
import { buildRejectReasonsFigure } from "../viz/rejectReasons";

// OMS Orders page — single surface for every order the platform ever
// placed. Replaces the per-broker pending_orders queue ad-hoc UI.
// Lists, filters, and gates state transitions through /api/oms/*.
// Mode toggle (Manual / Auto) drives the cancel-on-flip contract
// implemented in InMemoryOmsModeService (Phase 1c).

type OmsState = OmsOrderRow["state"];

const ALL_STATES: OmsState[] = [
  "PENDING_APPROVAL",
  "SUBMITTED",
  "WORKING",
  "PARTIALLY_FILLED",
  "FILLED",
  "CANCELLED",
  "REJECTED",
  "EXPIRED",
];

const OPEN_STATES: OmsState[] = ["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"];

/**
 * Known broker / symbol incompatibilities surfaced inline so the
 * trader sees WHY a row is doomed before reading the cancelled_reason.
 * T212 Invest API (the only T212 surface with a public REST endpoint)
 * exposes equities + ETFs only — FX lives in their CFD product
 * which has no public API. An EURUSD order via T212_DEMO is correctly
 * routed but the API will 404 with "entity-not-found".
 */
const FX_PAIRS = new Set([
  "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD",
  "USDCAD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
]);
function isFxOnT212Invest(symbol: string, broker: string): boolean {
  if (!broker.startsWith("T212")) return false;
  const upper = symbol.toUpperCase();
  // Match bare ("EURUSD") and underscore-suffixed ("EURUSD_FX") forms.
  return FX_PAIRS.has(upper) ||
    Array.from(FX_PAIRS).some((p) => upper.startsWith(p + "_") || upper === p);
}

function stateBadge(state: OmsState): { fg: string; bg: string } {
  switch (state) {
    case "FILLED":
      return { fg: "#1fc16b", bg: "rgba(31,193,107,0.14)" };
    case "PARTIALLY_FILLED":
      return { fg: "#d97706", bg: "rgba(217,119,6,0.14)" };
    case "PENDING_APPROVAL":
      return { fg: "#d97706", bg: "rgba(217,119,6,0.14)" };
    case "SUBMITTED":
    case "WORKING":
      return { fg: "#4f8cff", bg: "rgba(79,140,255,0.14)" };
    case "CANCELLED":
    case "EXPIRED":
      return { fg: "#9ca3af", bg: "rgba(107,114,128,0.14)" };
    case "REJECTED":
      return { fg: "#ef4444", bg: "rgba(239,68,68,0.14)" };
  }
}

const pillButton = (active: boolean): React.CSSProperties => ({
  padding: "4px 10px",
  fontSize: 11,
  border: "1px solid var(--border)",
  borderRadius: 999,
  background: active ? "var(--bg-hover, rgba(255,255,255,0.06))" : "transparent",
  color: active ? "var(--text)" : "var(--text-dim)",
  cursor: "pointer",
  letterSpacing: "0.04em",
});

const actionButton = (busy: boolean, kind: "ok" | "muted" | "danger" = "muted"): React.CSSProperties => ({
  padding: "4px 9px",
  fontSize: 11,
  border: `1px solid ${
    kind === "ok" ? "#1fc16b" : kind === "danger" ? "#ef4444" : "var(--border)"
  }`,
  borderRadius: 4,
  background: "transparent",
  color: busy
    ? "var(--text-muted)"
    : kind === "ok"
    ? "#1fc16b"
    : kind === "danger"
    ? "#ef4444"
    : "var(--text-dim)",
  cursor: busy ? "wait" : "pointer",
  whiteSpace: "nowrap",
});

export function OmsOrders() {
  // Honour ?strategy=X in the URL so deep-links from Session Detail
  // (resulting-OMS-order pivot) land already filtered.
  const [searchParams, setSearchParams] = useSearchParams();
  const strategyFilter = searchParams.get("strategy") ?? "";
  const setStrategyFilter = (s: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (s) next.set("strategy", s);
      else next.delete("strategy");
      return next;
    });
  };

  const [orders, setOrders] = useState<OmsOrderRow[]>([]);
  const [filterStates, setFilterStates] = useState<Set<OmsState>>(
    () => new Set(OPEN_STATES),
  );
  // Hide noise-cancellations by default: supersede + broker-not-found
  // chain produce many "real but uninteresting" cancellations that
  // bury the actual decisions. Trader can toggle them back on.
  const [hideNoiseCancellations, setHideNoiseCancellations] = useState(true);
  const [mode, setMode] = useState<"auto" | "manual" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  // Per-order audit-trail state: {orderId → audit|"loading"|"error"}.
  // Lazy-loaded on first expand so the orders list stays fast.
  // Carries the full decision chain (state events + RiskGate
  // refusals + LLM evaluations) — the "on what basis" answer.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [audits, setAudits] = useState<Record<string, Awaited<ReturnType<typeof api.omsOrderAudit>> | "loading" | "error">>({});

  const toggleEvents = async (orderId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(orderId) ? next.delete(orderId) : next.add(orderId);
      return next;
    });
    if (audits[orderId] !== undefined) return;
    setAudits((a) => ({ ...a, [orderId]: "loading" }));
    try {
      const audit = await api.omsOrderAudit(orderId);
      setAudits((a) => ({ ...a, [orderId]: audit }));
    } catch {
      setAudits((a) => ({ ...a, [orderId]: "error" }));
    }
  };

  const loadOrders = useCallback(async () => {
    try {
      // Always fetch the full unfiltered list so the per-state pill
      // counts reflect the global total, not the count within the
      // current filter. Previously clicking FILLED then clicking it
      // again collapsed every count to 0 because the API call
      // requested only the un-deselected states and the trader lost
      // sight of how many filled orders existed.
      const { orders } = await api.omsOrders(undefined, 500);
      setOrders(orders);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const loadMode = useCallback(async () => {
    try {
      const { mode } = await api.omsMode();
      setMode((mode === "auto" ? "auto" : "manual") as "auto" | "manual");
    } catch (e) {
      // Mode endpoint may not be deployed yet on older API images —
      // surface the failure but don't block the orders view.
      console.warn("omsMode failed:", e);
      setMode("manual");
    }
  }, []);

  useEffect(() => {
    void loadOrders();
    void loadMode();
  }, [loadOrders, loadMode]);

  // Auto-refresh every 10s while there are open orders worth polling.
  useEffect(() => {
    const hasOpen = orders.some((o) => OPEN_STATES.includes(o.state));
    if (!hasOpen) return;
    const id = setInterval(() => void loadOrders(), 10_000);
    return () => clearInterval(id);
  }, [orders, loadOrders]);

  const toggleFilter = (state: OmsState) => {
    setFilterStates((prev) => {
      const next = new Set(prev);
      next.has(state) ? next.delete(state) : next.add(state);
      return next;
    });
  };

  const flipMode = async () => {
    if (!mode) return;
    const target = mode === "auto" ? "manual" : "auto";
    if (
      mode === "auto" &&
      target === "manual" &&
      !confirm(
        "Switching Auto → Manual will CANCEL every open order. Continue?",
      )
    )
      return;
    setError(null);
    try {
      await api.setOmsMode(target);
      await loadMode();
      await loadOrders();
    } catch (e) {
      setError(String(e));
    }
  };

  const act = async (
    orderId: string,
    kind: "approve" | "reject" | "cancel",
  ) => {
    setActing(orderId + ":" + kind);
    setError(null);
    try {
      if (kind === "approve") await api.omsApprove(orderId);
      else if (kind === "reject") {
        const reason = prompt("Reject reason?", "") || "rejected";
        await api.omsReject(orderId, reason);
      } else {
        const reason = prompt("Cancel reason?", "user_cancel") || "user_cancel";
        await api.omsCancel(orderId, reason);
      }
      await loadOrders();
    } catch (e) {
      setError(String(e));
    } finally {
      setActing(null);
    }
  };

  // Apply BOTH the strategy filter AND the state filter on the
  // client. The API call deliberately returns everything (see
  // loadOrders) so the state-pill counts stay accurate as the trader
  // toggles pills off — deselecting FILLED hides filled rows from
  // the table but the pill still shows "FILLED 7" so the trader can
  // re-enable it without losing sight of the data behind it.
  const NOISE_REASONS = [
    "superseded by newer order",
    "unapprovable_pending_gates_block",
    "broker_not_found_assume_terminal",
  ];
  // Position-sync (reconcile) rows are NOT real fills: the
  // /oms/positions/sync-from-broker flow writes a synthetic FILLED order
  // (placed_by=HUMAN, no strategy, actor "oms-sync", brokerFillId
  // "reconcile-…") purely to make OMS-derived positions match the broker.
  // Counting them as FILLED inflated the pill to 119 and made it look
  // like strategies traded when they didn't. Treat them as noise: out of
  // the pill counts + hidden unless "Show noise" is on. (The orders-list
  // row doesn't carry actor/brokerFillId, so we key on the stable
  // signature FILLED + HUMAN + no strategy.)
  const isReconcileFill = (o: OmsOrderRow) =>
    o.state === "FILLED" && o.placedBy === "HUMAN" && !o.strategyId;
  const isNoise = (o: OmsOrderRow) =>
    isReconcileFill(o)
    || (o.state === "CANCELLED"
        && !!o.cancelledReason
        && NOISE_REASONS.some((n) => o.cancelledReason!.includes(n)));

  // De-noised base: when "Hide noise" is on (default), drop reconcile
  // fills + noise-cancellations BEFORE computing both the pill counts and
  // the table, so the FILLED pill reflects real fills and the table
  // matches it. State-filter toggles still survive (counts come from this
  // base, not from the already state-filtered visible set).
  const denoised = useMemo(
    () => (hideNoiseCancellations ? orders.filter((o) => !isNoise(o)) : orders),
    [orders, hideNoiseCancellations],
  );

  const visibleOrders = useMemo(() => {
    return denoised.filter((o) => {
      if (strategyFilter && o.strategyId !== strategyFilter) return false;
      if (filterStates.size > 0 && !filterStates.has(o.state as OmsState)) return false;
      return true;
    });
  }, [denoised, strategyFilter, filterStates]);

  // Totals come from the de-noised, strategy-scoped set (NOT the
  // state-filtered visible set) so each pill's count survives a state
  // toggle while still excluding position-sync noise.
  const totals = useMemo(() => {
    const scope = strategyFilter
      ? denoised.filter((o) => o.strategyId === strategyFilter)
      : denoised;
    const by: Record<string, number> = {};
    for (const o of scope) by[o.state] = (by[o.state] || 0) + 1;
    return by;
  }, [denoised, strategyFilter]);

  // How many position-sync rows are currently folded away — shown on the
  // noise toggle so the count isn't silently hidden.
  const reconcileCount = useMemo(
    () => orders.filter(isReconcileFill).length,
    [orders],
  );

  return (
    <div style={{ padding: 24 }}>
      <PaperSubNav />
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 16,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>OMS — orders</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-dim)", fontSize: 13 }}>
            Every order the platform placed — strategy intents, manual
            entries, audit trail. Approve / Reject / Cancel rows in flight.
            Mode toggle flips the daemon-wide auto vs manual gate.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>OMS mode</span>
          <button
            onClick={flipMode}
            disabled={mode === null}
            style={{
              padding: "5px 14px",
              fontSize: 12,
              fontWeight: 600,
              border: `1px solid ${mode === "auto" ? "#1fc16b" : "var(--border)"}`,
              borderRadius: 999,
              background: mode === "auto" ? "rgba(31,193,107,0.12)" : "transparent",
              color: mode === "auto" ? "#1fc16b" : "var(--text-dim)",
              cursor: mode === null ? "wait" : "pointer",
              letterSpacing: "0.06em",
              textTransform: "uppercase",
            }}
            title={
              mode === "auto"
                ? "Flip to Manual (will cancel every open order)"
                : "Flip to Auto (strategies auto-approve)"
            }
          >
            {mode ?? "…"}
          </button>
        </div>
      </header>

      {error && (
        <div
          style={{
            padding: "8px 12px",
            background: "rgba(239,68,68,0.08)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 6,
            color: "#ef4444",
            fontSize: 12,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      <RejectReasonsWidget orders={orders} />

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12, alignItems: "center" }}>
        {ALL_STATES.map((s) => {
          const b = stateBadge(s);
          const active = filterStates.has(s);
          const count = totals[s] ?? 0;
          return (
            <button
              key={s}
              onClick={() => toggleFilter(s)}
              style={{
                ...pillButton(active),
                color: active ? b.fg : "var(--text-dim)",
                borderColor: active ? b.fg : "var(--border)",
                background: active ? b.bg : "transparent",
              }}
            >
              {s} <span style={{ opacity: 0.7 }}>{count}</span>
            </button>
          );
        })}
        <span style={{ marginLeft: 12 }} />
        <button
          onClick={() => setHideNoiseCancellations((v) => !v)}
          style={{
            ...pillButton(!hideNoiseCancellations),
            color: hideNoiseCancellations ? "var(--text-dim)" : "var(--text)",
          }}
          title={
            "Toggle low-signal rows: position-sync reconcile fills "
            + "(synthetic FILLED rows that adopt broker positions, not real "
            + "trades) + low-signal cancellations ('superseded by newer "
            + "order', 'unapprovable_pending_gates_block', "
            + "'broker_not_found_assume_terminal')."
          }
        >
          {hideNoiseCancellations ? "Show noise" : "Hide noise"}
          {reconcileCount > 0 && (
            <span style={{ opacity: 0.7 }}> ({reconcileCount} sync)</span>
          )}
        </button>
      </div>

      {strategyFilter && (
        <div
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "6px 12px", marginBottom: 10,
            fontSize: 12,
            border: "1px solid var(--border)",
            borderRadius: 6,
            background: "rgba(79,140,255,0.06)",
          }}
        >
          <span style={{ color: "var(--text-dim)" }}>Filter:</span>
          <span style={{ fontFamily: "monospace", color: "#4f8cff", fontWeight: 600 }}>
            strategy={strategyFilter}
          </span>
          <button
            onClick={() => setStrategyFilter("")}
            style={{
              marginLeft: "auto",
              fontSize: 10, padding: "2px 8px",
              background: "transparent", border: "1px solid var(--border)",
              borderRadius: 4, color: "var(--text-dim)", cursor: "pointer",
            }}
          >
            clear ×
          </button>
        </div>
      )}

      {visibleOrders.length === 0 ? (
        <div style={{ padding: 32, color: "var(--text-dim)", fontSize: 13 }}>
          No orders match the current filter. Try expanding the state
          filter, or trigger a strategy from Strategies / Paper to
          generate intents.
        </div>
      ) : (
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: 8,
            overflowX: "auto",
          }}
        >
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)", background: "var(--bg-hover, rgba(255,255,255,0.03))" }}>
                <Th>Time UTC</Th>
                <Th>Strategy</Th>
                <Th>Symbol</Th>
                <Th>Side</Th>
                <Th>Qty</Th>
                <Th>Filled</Th>
                <Th>Avg fill</Th>
                <Th>State</Th>
                <Th>Broker</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody>
              {visibleOrders.map((o) => {
                const b = stateBadge(o.state);
                const isOpen = OPEN_STATES.includes(o.state);
                const busy = acting?.startsWith(o.id);
                const isExpanded = expanded.has(o.id);
                const audit = audits[o.id];
                return (
                  <React.Fragment key={o.id}>
                  <tr style={{ borderTop: "1px solid var(--border)" }}>
                    <Td mono>
                      <button
                        onClick={() => toggleEvents(o.id)}
                        style={{
                          marginRight: 6,
                          padding: "0 5px",
                          fontSize: 10,
                          background: "transparent",
                          border: "1px solid var(--border)",
                          borderRadius: 3,
                          color: "var(--text-muted)",
                          cursor: "pointer",
                          minWidth: 18,
                        }}
                        title={isExpanded ? "Hide event trail" : "Show event trail"}
                      >
                        {isExpanded ? "▾" : "▸"}
                      </button>
                      {o.createdAtUtc.slice(0, 19).replace("T", " ")}
                    </Td>
                    <Td>
                      {o.strategyId ? (
                        <Link
                          to={`/paper-live?strategy=${encodeURIComponent(o.strategyId)}`}
                          style={{
                            color: "inherit", textDecoration: "none",
                            borderBottom: "1px dotted var(--text-muted)",
                          }}
                          title={`Open paper sessions filtered to ${o.strategyId}`}
                        >
                          {o.strategyId}
                        </Link>
                      ) : isReconcileFill(o) ? (
                        <span
                          style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}
                          title="Synthetic reconcile fill — adopts a broker position into OMS; not a real trade."
                        >
                          position sync
                        </span>
                      ) : (
                        "—"
                      )}
                    </Td>
                    <Td>
                      {o.symbol}
                      {isFxOnT212Invest(o.symbol, o.broker) && (
                        <span
                          title={
                            "T212's Invest API does not list FX instruments — FX is "
                            + "CFD-only and has no public API. This order will 404 at "
                            + "placement. Use broker=PAPER for simulated fills; FX live "
                            + "needs IBKR (planned)."
                          }
                          style={{
                            marginLeft: 6, fontSize: 9,
                            padding: "0 5px", borderRadius: 999,
                            background: "rgba(245,158,11,0.14)",
                            color: "#f59e0b", fontWeight: 700,
                            letterSpacing: "0.04em",
                            cursor: "help",
                          }}
                        >
                          T212 ✗ FX
                        </span>
                      )}
                    </Td>
                    <Td style={{ color: o.side === "BUY" ? "#1fc16b" : "#ef4444" }}>{o.side}</Td>
                    <Td mono>{o.qty}</Td>
                    <Td mono>{o.filledQty}</Td>
                    <Td mono>{o.avgFillPrice != null ? o.avgFillPrice.toFixed(5) : "—"}</Td>
                    <Td>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          padding: "2px 6px",
                          borderRadius: 999,
                          color: b.fg,
                          background: b.bg,
                          letterSpacing: "0.04em",
                        }}
                      >
                        {o.state}
                      </span>
                    </Td>
                    <Td>
                      {o.broker}
                      {o.broker === "PAPER" && (
                        <span
                          title={
                            "PAPER fills are SIMULATED locally by the engine. "
                            + "They do NOT touch any broker, so they don't appear in "
                            + "Portfolio. Use broker=T212_DEMO to place at the actual "
                            + "T212 demo account (note: T212 Invest API has no FX)."
                          }
                          style={{
                            marginLeft: 6, fontSize: 9,
                            padding: "1px 6px", borderRadius: 999,
                            background: "rgba(168,85,247,0.14)",
                            color: "#a855f7",
                            fontWeight: 700, letterSpacing: "0.04em",
                            cursor: "help",
                          }}
                        >
                          SIMULATED
                        </span>
                      )}
                    </Td>
                    <Td>
                      {isOpen ? (
                        <div style={{ display: "flex", gap: 4 }}>
                          {o.state === "PENDING_APPROVAL" && (
                            <>
                              <button
                                onClick={() => act(o.id, "approve")}
                                disabled={busy}
                                style={actionButton(!!busy, "ok")}
                                title="Approve → Submitted"
                              >
                                approve
                              </button>
                              <button
                                onClick={() => act(o.id, "reject")}
                                disabled={busy}
                                style={actionButton(!!busy, "danger")}
                                title="Reject → Rejected"
                              >
                                reject
                              </button>
                            </>
                          )}
                          <button
                            onClick={() => act(o.id, "cancel")}
                            disabled={busy}
                            style={actionButton(!!busy)}
                            title="Cancel → Cancelled"
                          >
                            cancel
                          </button>
                        </div>
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontSize: 10 }}>terminal</span>
                      )}
                    </Td>
                  </tr>
                  {isExpanded && (
                    <tr style={{ background: "rgba(255,255,255,0.02)" }}>
                      <td colSpan={10} style={{ padding: "10px 14px" }}>
                        <AuditChain audit={audit} />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const cellStyle: React.CSSProperties = {
  padding: "8px 12px",
  textAlign: "left",
  verticalAlign: "middle",
};

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th style={{ ...cellStyle, fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase" }}>
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
  style,
}: {
  children: React.ReactNode;
  mono?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <td
      style={{
        ...cellStyle,
        fontFamily: mono ? "monospace" : undefined,
        color: mono ? "var(--text-muted)" : "var(--text)",
        ...(style ?? {}),
      }}
    >
      {children}
    </td>
  );
}

/**
 * AuditChain — full decision audit for one order. Three sections:
 *   1. State transitions (OMS event timeline)
 *   2. Risk gate decisions (C# RiskGate refusals + allowances)
 *   3. LLM evaluations (model verdicts with reasoning)
 * Answers the operator's "on what basis was this approved/rejected"
 * question without joining tables manually.
 */
function AuditChain({
  audit,
}: {
  audit: Awaited<ReturnType<typeof api.omsOrderAudit>> | "loading" | "error" | undefined;
}) {
  if (audit === "loading" || audit === undefined) {
    return <span style={{ fontSize: 11, color: "var(--text-dim)" }}>Loading audit chain…</span>;
  }
  if (audit === "error") {
    return <span style={{ fontSize: 11, color: "#ef4444" }}>Failed to load audit.</span>;
  }
  const s = audit.summary;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontSize: 11, color: "var(--text-dim)", display: "flex", gap: 14, flexWrap: "wrap" }}>
        <span>State events: <strong>{s.nStateTransitions}</strong></span>
        <span>Risk events: <strong>{s.nRiskEvents}</strong>
          {s.riskBlocks > 0 && <> · <span style={{ color: "#ef4444" }}>{s.riskBlocks} blocks</span></>}
        </span>
        <span>LLM evaluations: <strong>{s.nLlmEvals}</strong>
          {s.llmApprovals > 0 && <> · <span style={{ color: "#1fc16b" }}>{s.llmApprovals} approve</span></>}
          {s.llmRejections > 0 && <> · <span style={{ color: "#ef4444" }}>{s.llmRejections} reject</span></>}
        </span>
      </div>

      <AuditSection title="Symbol chart (visual legitimacy check)">
        <SymbolChartSection orderSymbol={audit.order.symbol} />
      </AuditSection>

      <AuditSection title="State transitions">
        {audit.events.length === 0
          ? <Empty>No state changes recorded.</Empty>
          : <EventTimeline evs={audit.events} />}
      </AuditSection>

      <AuditSection title="Risk gate decisions">
        {audit.riskEvents.length === 0
          ? <Empty>No gate events for this order.</Empty>
          : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ color: "var(--text-dim)" }}>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Time UTC</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Gate</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Decision</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Reason</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Detail</th>
                </tr>
              </thead>
              <tbody>
                {audit.riskEvents.map((r) => (
                  <tr key={r.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace", color: "var(--text-muted)" }}>
                      {r.occurred_at_utc.slice(0, 19).replace("T", " ")}
                    </td>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace" }}>{r.gate}</td>
                    <td style={{ padding: "3px 8px", fontWeight: 600, color: r.decision === "ALLOWED" ? "#1fc16b" : "#ef4444" }}>
                      {r.decision}
                    </td>
                    <td style={{ padding: "3px 8px", color: "var(--text)" }}>{r.reason ?? "—"}</td>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace", color: "var(--text-muted)", fontSize: 10 }}>
                      {r.detail_json ? truncate(r.detail_json, 160) : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </AuditSection>

      <AuditSection title="LLM evaluations">
        {audit.llmEvals.length === 0
          ? <Empty>No LLM evaluation recorded. (LLM gate not wired or skipped this order.)</Empty>
          : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ color: "var(--text-dim)" }}>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Time UTC</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Model</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Purpose</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Decision</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Conf.</th>
                  <th style={{ textAlign: "left", padding: "3px 8px" }}>Reasoning</th>
                </tr>
              </thead>
              <tbody>
                {audit.llmEvals.map((l) => (
                  <tr key={l.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace", color: "var(--text-muted)" }}>
                      {l.occurred_at_utc.slice(0, 19).replace("T", " ")}
                    </td>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace" }}>{l.llm_model}</td>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace", color: "var(--text-muted)" }}>{l.purpose}</td>
                    <td style={{ padding: "3px 8px", fontWeight: 600, color: llmDecisionColour(l.decision) }}>{l.decision}</td>
                    <td style={{ padding: "3px 8px", fontFamily: "monospace" }}>{l.confidence != null ? l.confidence.toFixed(2) : "—"}</td>
                    <td style={{ padding: "3px 8px" }}>{l.reasoning ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </AuditSection>
    </div>
  );
}

function AuditSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{
        fontSize: 9, color: "var(--text-muted)",
        letterSpacing: "0.06em", textTransform: "uppercase",
        marginBottom: 4,
      }}>{title}</div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <span style={{ fontSize: 11, color: "var(--text-dim)" }}>{children}</span>;
}

/**
 * SymbolChartSection — for each audit panel, fetch today's paper
 * snapshots and find the chart for the order's symbol. Renders the
 * Plotly figure so the trader can visually verify the legitimacy
 * of the order (price + Ichimoku cloud + indicators + where we are
 * on the trend). Closes the "we should see the chart not just the
 * numbers" gap raised on the OMS page.
 */
function SymbolChartSection({ orderSymbol }: { orderSymbol: string }) {
  const [figure, setFigure] = React.useState<unknown | null>(null);
  const [state, setState] = React.useState<"loading" | "ok" | "none" | "error">("loading");

  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const bare = bareSymbolForChart(orderSymbol);
        const snapshots = await api.paperSnapshots();
        const today = new Date().toISOString().slice(0, 10);
        // Search today's snapshots for a chart matching this symbol.
        // Charts are keyed "ichimoku_cloud:AAPL" or similar.
        for (const snap of snapshots) {
          if (!snap.sessionLabel.endsWith(today)) continue;
          const detail = await api.paperSnapshot(snap.sessionLabel) as
            { strategies?: Array<{ charts?: Record<string, unknown> }> };
          for (const st of detail.strategies ?? []) {
            for (const [name, fig] of Object.entries(st.charts ?? {})) {
              const idx = name.indexOf(":");
              const sym = idx > 0 ? name.slice(idx + 1).toUpperCase() : name.toUpperCase();
              if (sym === bare) {
                if (!cancelled) {
                  setFigure(fig);
                  setState("ok");
                }
                return;
              }
            }
          }
        }
        if (!cancelled) setState("none");
      } catch {
        if (!cancelled) setState("error");
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [orderSymbol]);

  if (state === "loading") return <Empty>Loading chart for {orderSymbol}…</Empty>;
  if (state === "error") return <Empty>Failed to fetch chart.</Empty>;
  if (state === "none") return (
    <Empty>
      No strategy chart available for {orderSymbol} today. Either the
      strategy hasn't run yet or this symbol isn't in its universe.
    </Empty>
  );
  return (
    <div style={{ height: 280, width: "100%" }}>
      <PlotlyChart figure={figure as Parameters<typeof PlotlyChart>[0]["figure"]} />
    </div>
  );
}

function bareSymbolForChart(symbol: string): string {
  // Strip broker suffixes so we join on the bare ticker:
  //   AAPL_US_EQ → AAPL
  //   CS.D.EURUSD.MINI.IP → EURUSD
  if (symbol.startsWith("CS.D.") || symbol.startsWith("IX.D.")) {
    const parts = symbol.split(".");
    if (parts.length >= 4) return parts[2].toUpperCase();
  }
  const u = symbol.indexOf("_");
  return u > 0 ? symbol.slice(0, u).toUpperCase() : symbol.toUpperCase();
}

function llmDecisionColour(d: string): string {
  if (d === "APPROVE") return "#1fc16b";
  if (d === "REJECT") return "#ef4444";
  if (d === "ERROR") return "#f59e0b";
  return "var(--text)";
}

function EventTimeline({
  evs,
}: {
  evs: OmsOrderEventRow[];
}) {
  if (evs.length === 0) {
    return <Empty>No events recorded.</Empty>;
  }
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
      <thead>
        <tr style={{ color: "var(--text-dim)" }}>
          <th style={{ textAlign: "left", padding: "3px 8px" }}>Time UTC</th>
          <th style={{ textAlign: "left", padding: "3px 8px" }}>Event</th>
          <th style={{ textAlign: "left", padding: "3px 8px" }}>Transition</th>
          <th style={{ textAlign: "left", padding: "3px 8px" }}>Actor</th>
          <th style={{ textAlign: "left", padding: "3px 8px" }}>Detail</th>
        </tr>
      </thead>
      <tbody>
        {evs.map((e) => (
          <tr key={e.id} style={{ borderTop: "1px solid var(--border)" }}>
            <td style={{ padding: "3px 8px", fontFamily: "monospace", color: "var(--text-muted)" }}>
              {e.occurredAtUtc.slice(0, 19).replace("T", " ")}
            </td>
            <td
              style={{
                padding: "3px 8px",
                color: eventTone(e.eventType),
                fontWeight: 600,
                fontFamily: "monospace",
              }}
            >
              {e.eventType}
            </td>
            <td style={{ padding: "3px 8px", color: "var(--text-dim)" }}>
              {(e.priorState ?? "—") + " → " + e.newState}
            </td>
            <td style={{ padding: "3px 8px", color: "var(--text-muted)" }}>{e.actor}</td>
            <td style={{ padding: "3px 8px", fontFamily: "monospace", color: "var(--text-muted)", fontSize: 10 }}>
              {e.detailJson ? truncate(e.detailJson, 160) : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function eventTone(t: string): string {
  if (t === "ENQUEUED" || t === "APPROVED" || t === "FILL") return "#4f8cff";
  if (t === "REJECTED" || t === "BROKER_REJECTED" || t === "CANCEL_BROKER_FAILED") return "#ef4444";
  if (t === "CANCELLED") return "var(--text-muted)";
  return "var(--text)";
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n) + "…";
}

/**
 * RejectReasonsWidget — collapsible Plotly bar of cancelled_reason
 * counts across REJECTED + CANCELLED + EXPIRED orders. Hidden when
 * there are zero failures (don't waste vertical space).
 *
 * Renders only when there's something to show so this is invisible
 * on a clean queue. When orders fail in clusters (e.g. T212 returns
 * INSUFFICIENT_FUNDS twenty times in a row) the trader sees a single
 * dominant red bar and fixes the root cause instead of cancelling
 * twenty rows.
 */
function RejectReasonsWidget({ orders }: { orders: OmsOrderRow[] }) {
  const REJECT_STATES = new Set(["REJECTED", "CANCELLED", "EXPIRED"]);
  const failing = orders.filter((o) => REJECT_STATES.has(o.state));
  if (failing.length === 0) return null;
  return (
    <details
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 10,
        marginBottom: 12,
        background: "rgba(239,68,68,0.04)",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          color: "#ef4444",
          fontWeight: 600,
          fontSize: 12,
          userSelect: "none",
        }}
      >
        Reject / cancel reasons ({failing.length} failed orders)
      </summary>
      <div style={{ marginTop: 8 }}>
        <PlotlyChart figure={buildRejectReasonsFigure(failing)} />
      </div>
    </details>
  );
}
