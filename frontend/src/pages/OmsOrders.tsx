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
  const [mode, setMode] = useState<"auto" | "manual" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  // Per-order event timeline state: {orderId → events[]|"loading"|"error"}.
  // Lazy-loaded on first expand so the orders list stays fast.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [events, setEvents] = useState<Record<string, OmsOrderEventRow[] | "loading" | "error">>({});

  const toggleEvents = async (orderId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(orderId) ? next.delete(orderId) : next.add(orderId);
      return next;
    });
    if (events[orderId] !== undefined) return;
    setEvents((e) => ({ ...e, [orderId]: "loading" }));
    try {
      const { events: rows } = await api.omsOrderEvents(orderId);
      setEvents((e) => ({ ...e, [orderId]: rows }));
    } catch {
      setEvents((e) => ({ ...e, [orderId]: "error" }));
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
  const visibleOrders = useMemo(() => {
    return orders.filter((o) => {
      if (strategyFilter && o.strategyId !== strategyFilter) return false;
      if (filterStates.size > 0 && !filterStates.has(o.state as OmsState)) return false;
      return true;
    });
  }, [orders, strategyFilter, filterStates]);

  // Totals are computed from the full (strategy-scoped) order set,
  // NOT from visibleOrders, so each pill's count survives a state-
  // filter toggle. Strategy filter still applies — when scoped to
  // one strategy, the pills show that strategy's state breakdown.
  const totals = useMemo(() => {
    const scope = strategyFilter
      ? orders.filter((o) => o.strategyId === strategyFilter)
      : orders;
    const by: Record<string, number> = {};
    for (const o of scope) by[o.state] = (by[o.state] || 0) + 1;
    return by;
  }, [orders, strategyFilter]);

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

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
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
                const evs = events[o.id];
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
                    <Td>{o.broker}</Td>
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
                        <EventTimeline evs={evs} />
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

function EventTimeline({
  evs,
}: {
  evs: OmsOrderEventRow[] | "loading" | "error" | undefined;
}) {
  if (evs === "loading" || evs === undefined) {
    return <span style={{ fontSize: 11, color: "var(--text-dim)" }}>Loading events…</span>;
  }
  if (evs === "error") {
    return <span style={{ fontSize: 11, color: "#ef4444" }}>Failed to load events.</span>;
  }
  if (evs.length === 0) {
    return <span style={{ fontSize: 11, color: "var(--text-dim)" }}>No events recorded.</span>;
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
