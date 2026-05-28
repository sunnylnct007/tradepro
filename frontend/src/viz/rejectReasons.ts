/**
 * rejectReasons — Plotly figure showing the distribution of
 * cancelled_reason values across REJECTED + CANCELLED orders.
 *
 * Reads from the same OmsOrderRow[] the /oms page already fetches —
 * no new endpoint, no new state. Surfaces the dominant failure
 * modes (INSUFFICIENT_FUNDS, INSTRUMENT_NOT_FOUND, MARKET_CLOSED,
 * BROKER_REJECTED…) so the trader can fix root causes instead of
 * cancelling one row at a time.
 */
import type { OmsOrderRow } from "../api/client";

const REJECT_STATES = new Set(["REJECTED", "CANCELLED", "EXPIRED"]);

/**
 * Aggregate (reason, count) pairs from the rejection-ish orders.
 * Empty reasons are collapsed into "(no reason)".
 */
function aggregateReasons(orders: OmsOrderRow[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const o of orders) {
    if (!REJECT_STATES.has(o.state)) continue;
    const reason = (o.cancelledReason ?? "").trim() || "(no reason)";
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
}

export function buildRejectReasonsFigure(
  orders: OmsOrderRow[],
  opts?: { title?: string },
): Record<string, unknown> {
  const rows = aggregateReasons(orders);
  // Truncate very long reason strings for the y-axis tick labels.
  const labels = rows.map(([r]) => (r.length > 60 ? r.slice(0, 57) + "…" : r));
  const counts = rows.map(([, n]) => n);

  return {
    data: [
      {
        type: "bar",
        orientation: "h",
        x: counts,
        y: labels,
        text: counts.map(String),
        textposition: "outside",
        marker: {
          color: "#ef4444",
          line: { color: "rgba(239,68,68,0.4)", width: 1 },
        },
        hovertemplate: "%{y}<br>%{x} orders<extra></extra>",
      },
    ],
    layout: {
      title: opts?.title ?? "Reject / cancel reasons",
      template: "plotly_white",
      height: Math.max(220, 60 + 26 * Math.max(rows.length, 1)),
      margin: { l: 240, r: 30, t: 40, b: 32 },
      xaxis: { title: "# orders", dtick: 1 },
      yaxis: {
        type: "category",
        automargin: true,
        categoryorder: "array",
        categoryarray: labels.slice().reverse(),
      },
      showlegend: false,
    },
  };
}
