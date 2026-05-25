import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, OmsOrderRow } from "../api/client";

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
  const [orders, setOrders] = useState<OmsOrderRow[]>([]);
  const [filterStates, setFilterStates] = useState<Set<OmsState>>(
    () => new Set(OPEN_STATES),
  );
  const [mode, setMode] = useState<"auto" | "manual" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const loadOrders = useCallback(async () => {
    try {
      const states = Array.from(filterStates);
      const { orders } = await api.omsOrders(states.length ? states : undefined, 200);
      setOrders(orders);
    } catch (e) {
      setError(String(e));
    }
  }, [filterStates]);

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

  const totals = useMemo(() => {
    const by: Record<string, number> = {};
    for (const o of orders) by[o.state] = (by[o.state] || 0) + 1;
    return by;
  }, [orders]);

  return (
    <div style={{ padding: 24 }}>
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

      {orders.length === 0 ? (
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
              {orders.map((o) => {
                const b = stateBadge(o.state);
                const isOpen = OPEN_STATES.includes(o.state);
                const busy = acting?.startsWith(o.id);
                return (
                  <tr key={o.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <Td mono>
                      {o.createdAtUtc.slice(0, 19).replace("T", " ")}
                    </Td>
                    <Td>{o.strategyId ?? "—"}</Td>
                    <Td>{o.symbol}</Td>
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
