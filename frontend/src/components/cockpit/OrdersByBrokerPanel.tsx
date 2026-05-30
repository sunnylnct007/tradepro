/**
 * OrdersByBrokerPanel — today's order flow, COMPLETE but SEGREGATED BY
 * BROKER. Complements the state-bucketed panels (generated / placed /
 * executed) which, by design, only show open + filled orders — so a
 * strategy firing a storm of CANCELLED/REJECTED orders (e.g. FX while
 * the market is closed: "ig_rejected: MARKET_CLOSED_WITH_EDITS") was
 * previously INVISIBLE in the cockpit. This panel shows every state per
 * broker so that flow can't hide.
 *
 * Per broker: a state-count chip row (so you see "12 filled · 142
 * cancelled" at a glance) + the most recent orders incl. terminal ones
 * with their reason. Pure presentation off the OMS orders the cockpit
 * already polls — no new fetch.
 */
import { Link } from "react-router-dom";
import type { OmsOrderRow } from "../../api/client";
import { StatePill } from "./OrdersTable";
import { brokerLabel, prettySymbol } from "../../util/brokerSymbols";

const STATE_COLOUR: Record<string, string> = {
  FILLED: "#1fc16b",
  PARTIALLY_FILLED: "#4f8cff",
  SUBMITTED: "#4f8cff",
  WORKING: "#4f8cff",
  PENDING_APPROVAL: "#f59e0b",
  REJECTED: "#ef4444",
  CANCELLED: "var(--text-muted)",
  EXPIRED: "var(--text-muted)",
};

export function OrdersByBrokerPanel({ orders, perBroker = 12 }: {
  orders: OmsOrderRow[];
  perBroker?: number;
}) {
  // Today (UTC) only — yesterday's flow isn't actionable here.
  const today = new Date().toISOString().slice(0, 10);
  const todays = orders.filter((o) => o.lastStateChangeAtUtc.slice(0, 10) === today);

  // Group by broker, most-active broker first.
  const groups = new Map<string, OmsOrderRow[]>();
  for (const o of todays) {
    const k = o.broker || "unknown";
    (groups.get(k) ?? groups.set(k, []).get(k)!).push(o);
  }
  const brokers = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);

  if (todays.length === 0) {
    return <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No orders today.</span>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {brokers.map(([broker, rows]) => {
        const counts = new Map<string, number>();
        for (const o of rows) counts.set(o.state, (counts.get(o.state) ?? 0) + 1);
        const recent = [...rows]
          .sort((a, b) => b.lastStateChangeAtUtc.localeCompare(a.lastStateChangeAtUtc))
          .slice(0, perBroker);
        return (
          <div key={broker}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text)" }}>{brokerLabel(broker)}</span>
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{rows.length} today</span>
              {/* State-count chips — the rejected/cancelled storm is now visible. */}
              {[...counts.entries()]
                .sort((a, b) => b[1] - a[1])
                .map(([state, n]) => (
                  <span key={state} style={{
                    fontSize: 10, fontFamily: "monospace", padding: "1px 7px", borderRadius: 999,
                    color: STATE_COLOUR[state] ?? "var(--text-dim)",
                    border: `1px solid ${STATE_COLOUR[state] ?? "var(--border)"}`,
                  }}>
                    {n} {state.toLowerCase().replace("_", " ")}
                  </span>
                ))}
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ color: "var(--text-dim)" }}>
                  <th style={th}>Time</th>
                  <th style={th}>Strategy</th>
                  <th style={th}>Symbol</th>
                  <th style={th}>Side</th>
                  <th style={{ ...th, textAlign: "right" }}>Qty</th>
                  <th style={th}>State / reason</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((o) => (
                  <tr key={o.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ ...td, fontFamily: "monospace", color: "var(--text-muted)" }}>
                      {o.lastStateChangeAtUtc.slice(11, 19)}
                    </td>
                    <td style={td}>{o.strategyId ?? "—"}</td>
                    <td style={td} title={o.symbol}>{prettySymbol(o.symbol)}</td>
                    <td style={{ ...td, color: o.side === "BUY" ? "#1fc16b" : "#ef4444" }}>{o.side}</td>
                    <td style={{ ...td, textAlign: "right", fontFamily: "monospace" }}>{o.qty}</td>
                    <td style={td}>
                      <StatePill state={o.state} />
                      {(o.state === "REJECTED" || o.state === "CANCELLED") && o.cancelledReason && (
                        <span title={o.cancelledReason} style={{
                          marginLeft: 6, fontSize: 10,
                          color: o.state === "REJECTED" ? "#ef4444" : "var(--text-muted)",
                          display: "inline-block", maxWidth: 260, overflow: "hidden",
                          textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "middle",
                        }}>
                          {o.cancelledReason}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > recent.length && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                Showing {recent.length} of {rows.length} — full history in{" "}
                <Link to="/oms" style={{ color: "var(--text-muted)" }}>OMS →</Link>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const th: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", fontSize: 10, fontWeight: 700,
  letterSpacing: "0.04em", textTransform: "uppercase",
};
const td: React.CSSProperties = { padding: "4px 8px" };
