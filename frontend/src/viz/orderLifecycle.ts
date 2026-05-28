/**
 * orderLifecycle — build a Plotly figure spec showing every order
 * as a horizontal bar from enqueue to its last state change, colour-
 * coded by current state.
 *
 * Why frontend code instead of backend Plotly: the chart is pure
 * presentation of data already on the wire (OmsOrderRow[]). No
 * business logic happens here — just a layout transform. New
 * order-centric charts that need new aggregates should go in the
 * backend viz framework instead.
 *
 * Hover surfaces (id, symbol, side, qty, state, time spent) so the
 * trader can identify a stuck order without clicking through.
 */
import type { OmsOrderRow } from "../api/client";

const STATE_COLOR: Record<string, string> = {
  PENDING_APPROVAL: "#f59e0b",
  SUBMITTED: "#4f8cff",
  WORKING: "#4f8cff",
  PARTIALLY_FILLED: "#06A77D",
  FILLED: "#1fc16b",
  CANCELLED: "#9ca3af",
  EXPIRED: "#9ca3af",
  REJECTED: "#ef4444",
};

function fmtDuration(ms: number): string {
  if (ms < 0) ms = 0;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
  return `${Math.floor(ms / 3_600_000)}h ${Math.round((ms % 3_600_000) / 60_000)}m`;
}

/**
 * Returns a Plotly figure dict ready to hand to <PlotlyChart>.
 *
 * Each order becomes one bar drawn from createdAtUtc to
 * lastStateChangeAtUtc (or "now" for still-open orders) on a Bar
 * chart with orientation=h. We use Bar (with a `base`) rather than
 * Plotly's Timeline because timeline needs the px-timeline trace
 * type which isn't in plotly.js-basic-dist.
 */
export function buildOrderLifecycleFigure(
  orders: OmsOrderRow[],
  opts?: { now?: Date; title?: string },
): Record<string, unknown> {
  const now = opts?.now ?? new Date();
  // Group bars per state so each group becomes its own trace with a
  // single legend entry + uniform colour.
  type OrderBar = {
    order: OmsOrderRow;
    startMs: number;
    endMs: number;
    durationMs: number;
  };
  const byState = new Map<string, OrderBar[]>();
  for (const o of orders) {
    const start = new Date(o.createdAtUtc).getTime();
    const isTerminal = ["FILLED", "CANCELLED", "EXPIRED", "REJECTED"].includes(o.state);
    const end = isTerminal
      ? new Date(o.lastStateChangeAtUtc).getTime()
      : now.getTime();
    const dur = end - start;
    const arr = byState.get(o.state) ?? [];
    arr.push({ order: o, startMs: start, endMs: end, durationMs: dur });
    byState.set(o.state, arr);
  }

  // Stable y-axis: newest order at top.
  const sortedOrders = [...orders].sort(
    (a, b) => new Date(b.createdAtUtc).getTime() - new Date(a.createdAtUtc).getTime(),
  );
  const yLabel = (o: OmsOrderRow) =>
    `${o.symbol} ${o.side} ${o.qty} · ${o.id.slice(0, 6)}`;
  // Pre-compute a stable label list so all traces share the y categorical axis.
  const yLabels = sortedOrders.map(yLabel);

  const traces = Array.from(byState.entries()).map(([state, bars]) => {
    const x: number[] = [];   // duration (ms)
    const base: number[] = [];// start (ms)
    const y: string[] = [];
    const customdata: string[] = [];
    const hovertext: string[] = [];
    for (const b of bars) {
      x.push(b.durationMs);
      base.push(b.startMs);
      y.push(yLabel(b.order));
      customdata.push(b.order.id);
      hovertext.push(
        [
          `<b>${b.order.symbol}</b> ${b.order.side} ${b.order.qty}`,
          `state: ${state}`,
          `created: ${new Date(b.startMs).toLocaleTimeString()}`,
          `${["FILLED", "CANCELLED", "EXPIRED", "REJECTED"].includes(state)
            ? "completed"
            : "still open"}: ${new Date(b.endMs).toLocaleTimeString()}`,
          `duration: ${fmtDuration(b.durationMs)}`,
          b.order.cancelledReason ? `reason: ${b.order.cancelledReason}` : "",
        ].filter(Boolean).join("<br>"),
      );
    }
    return {
      type: "bar",
      orientation: "h",
      name: state,
      x, y, base,
      customdata,
      hovertemplate: "%{text}<extra></extra>",
      text: hovertext,
      marker: { color: STATE_COLOR[state] ?? "#9ca3af" },
    };
  });

  return {
    data: traces,
    layout: {
      title: opts?.title ?? "Order lifecycle",
      template: "plotly_white",
      height: Math.max(220, 24 + 22 * yLabels.length),
      margin: { l: 200, r: 20, t: 40, b: 36 },
      barmode: "stack",
      bargap: 0.2,
      xaxis: {
        type: "date",
        title: "Time (UTC)",
      },
      yaxis: {
        type: "category",
        categoryorder: "array",
        categoryarray: yLabels.slice().reverse(),
        automargin: true,
      },
      legend: {
        orientation: "h",
        y: 1.06,
        x: 1,
        xanchor: "right",
      },
      showlegend: true,
    },
  };
}
