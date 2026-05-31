/**
 * OrdersTable — compact OMS-order table used by the cockpit's
 * three order panels (intents / submitted / fills). Renders only;
 * action wiring (approve / reject / cancel) is owned by the caller
 * so the panels can pick which buttons to show.
 *
 * StatePill is co-located because it's only used here. miniButton
 * stays a local helper for the same reason.
 */
import { Link } from "react-router-dom";
import type { OmsOrderRow } from "../../api/client";
import { brokerLabel, prettySymbol } from "../../util/brokerSymbols";
import { fmtWhen } from "../../util/time";

export function OrdersTable({
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
    // overflow-x so the (now wider, with Broker col) table scrolls within
    // its card on narrow/half-width layouts instead of spilling past it.
    <div style={{ overflowX: "auto", maxWidth: "100%" }}>
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 560 }}>
      <thead>
        <tr style={{ color: "var(--text-dim)" }}>
          <th style={TH}>Time</th>
          <th style={TH}>Broker</th>
          <th style={TH}>Strategy</th>
          <th style={TH}>Symbol</th>
          <th style={TH}>Side</th>
          <th style={{ ...TH, textAlign: "right" }}>Qty</th>
          <th style={TH}>State</th>
          <th style={TH}>Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => (
          <OrderRow
            key={o.id}
            order={o}
            busy={!!acting?.startsWith(o.id)}
            allowApprove={!!allowApprove}
            onApprove={onApprove}
            onReject={onReject}
            onCancel={onCancel}
          />
        ))}
      </tbody>
    </table>
    </div>
  );
}

function OrderRow({
  order, busy, allowApprove, onApprove, onReject, onCancel,
}: {
  order: OmsOrderRow;
  busy: boolean;
  allowApprove: boolean;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onCancel: (id: string) => void;
}) {
  const cancellable = ["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"]
    .includes(order.state);
  return (
    <tr style={{ borderTop: "1px solid var(--border)" }}>
      <td style={{ ...TD, fontFamily: "monospace", color: "var(--text-muted)", whiteSpace: "nowrap" }}>
        {fmtWhen(order.createdAtUtc)}
      </td>
      <td style={{ ...TD, fontSize: 11, color: "var(--text-dim)" }}>{brokerLabel(order.broker)}</td>
      <td style={TD}>{order.strategyId ?? "—"}</td>
      <td style={TD} title={order.symbol}>{prettySymbol(order.symbol)}</td>
      <td style={{ ...TD, color: order.side === "BUY" ? "#1fc16b" : "#ef4444" }}>
        {order.side}
      </td>
      <td style={{ ...TD, textAlign: "right", fontFamily: "monospace" }}>{order.qty}</td>
      <td style={TD}>
        <StatePill state={order.state} />
        {(order.state === "REJECTED" || order.state === "CANCELLED") && order.cancelledReason && (
          <span
            title={order.cancelledReason}
            style={{
              display: "inline-block", marginLeft: 6,
              fontSize: 10,
              color: order.state === "REJECTED" ? "#ef4444" : "var(--text-muted)",
              maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
              whiteSpace: "nowrap", verticalAlign: "middle",
            }}
          >
            {order.cancelledReason}
          </span>
        )}
      </td>
      <td style={{ ...TD, whiteSpace: "nowrap" }}>
        {allowApprove && order.state === "PENDING_APPROVAL" && (
          <>
            <button onClick={() => onApprove(order.id)} disabled={busy} style={miniButton("ok")}>
              approve
            </button>{" "}
            <button onClick={() => onReject(order.id)} disabled={busy} style={miniButton("down")}>
              reject
            </button>{" "}
          </>
        )}
        {cancellable && (
          <button onClick={() => onCancel(order.id)} disabled={busy} style={miniButton("muted")}>
            cancel
          </button>
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
}

/**
 * StatePill — colour-coded OMS state badge. Green=filled,
 * blue=in-flight, amber=pending, red=rejected, muted=terminal benign.
 */
export function StatePill({ state }: { state: string }) {
  const tone = TONE[state] ?? TONE.default;
  return (
    <span style={{
      fontSize: 10, padding: "1px 6px", borderRadius: 999,
      background: tone.bg, color: tone.fg, fontFamily: "monospace",
    }}>
      {state}
    </span>
  );
}

const TONE: Record<string, { fg: string; bg: string }> = {
  FILLED:           { fg: "#1fc16b",        bg: "rgba(31,193,107,0.15)" },
  SUBMITTED:        { fg: "#4f8cff",        bg: "rgba(79,140,255,0.15)" },
  WORKING:          { fg: "#4f8cff",        bg: "rgba(79,140,255,0.15)" },
  PARTIALLY_FILLED: { fg: "#4f8cff",        bg: "rgba(79,140,255,0.15)" },
  PENDING_APPROVAL: { fg: "#f59e0b",        bg: "rgba(245,158,11,0.15)" },
  REJECTED:         { fg: "#ef4444",        bg: "rgba(239,68,68,0.15)" },
  default:          { fg: "var(--text-dim)", bg: "rgba(255,255,255,0.06)" },
};

const TH: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", fontSize: 10, fontWeight: 700,
  letterSpacing: "0.04em", textTransform: "uppercase",
};
const TD: React.CSSProperties = { padding: "4px 8px" };

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
