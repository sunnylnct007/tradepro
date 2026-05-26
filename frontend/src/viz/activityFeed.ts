/**
 * activityFeed — merge per-strategy decisions, OMS orders, and OMS
 * fills into one chronological event timeline. The cockpit's
 * ActivityFeed widget reads the trader's day as a single story:
 * strategy fired a signal → OMS created the order → operator (or
 * auto-mode) approved → broker accepted → broker filled.
 *
 * Lives in /viz/ even though it isn't a Plotly chart — it's still a
 * presentation transform over already-fetched data. No new endpoint
 * needed; consumes what TraderCockpit already polls.
 */
import type { OmsOrderRow } from "../api/client";

export type ActivityKind =
  | "signal"
  | "order_enqueued"
  | "order_approved"
  | "order_submitted"
  | "order_filled"
  | "order_rejected"
  | "order_cancelled";

export type ActivityEvent = {
  /** ISO string UTC; sortable. */
  time: string;
  kind: ActivityKind;
  /** Short headline, e.g. "fire-moo-entry AAPL". */
  label: string;
  /** Secondary line, e.g. "signal=1 cloud=above". */
  detail?: string;
  symbol?: string;
  strategyId?: string | null;
  /** Optional in-app link (e.g. "/oms" or "/paper-live/session/<id>"). */
  href?: string;
};

type DecisionRow = {
  barTs: string | null;
  symbol: string;
  action: string;
  reason: string;
  detail: Record<string, unknown>;
};

type StrategyLatest = {
  strategy: string;
  requestId: string;
  decisions: DecisionRow[];
};

/**
 * Build one chronological list from the cockpit's existing data
 * sources. Caller controls ordering (default: newest first) and
 * limit (default: 50).
 */
export function buildActivityFeed(
  orders: OmsOrderRow[],
  strategySessions: StrategyLatest[],
  opts?: { limit?: number; newestFirst?: boolean },
): ActivityEvent[] {
  const events: ActivityEvent[] = [];

  // ── Strategy signals (decisions tagged fire-*) ──────────────────
  for (const s of strategySessions) {
    for (const d of s.decisions) {
      if (!d.action.startsWith("fire-")) continue;
      events.push({
        time: d.barTs ?? "",
        kind: "signal",
        label: `${d.action} ${d.symbol}`,
        detail: d.reason || undefined,
        symbol: d.symbol,
        strategyId: s.strategy,
        href: `/paper-live/session/${encodeURIComponent(s.requestId)}`,
      });
    }
  }

  // ── OMS order lifecycle ─────────────────────────────────────────
  // We emit one event per order based on its CURRENT state. Multiple
  // events per order (enqueued → submitted → filled) would need the
  // events-per-order endpoint, which the feed avoids for now to keep
  // it derivable from the orders[] poll alone.
  for (const o of orders) {
    const baseLabel = `${o.side} ${o.qty} ${o.symbol}`;
    const headline = (kind: ActivityKind) => kind === "signal" ? "" : `${baseLabel}`;
    const stateToKind: Record<string, ActivityKind | null> = {
      PENDING_APPROVAL: "order_enqueued",
      SUBMITTED: "order_submitted",
      WORKING: "order_submitted",
      PARTIALLY_FILLED: "order_filled",
      FILLED: "order_filled",
      REJECTED: "order_rejected",
      CANCELLED: "order_cancelled",
      EXPIRED: "order_cancelled",
    };
    const kind = stateToKind[o.state];
    if (!kind) continue;
    events.push({
      time: o.lastStateChangeAtUtc || o.createdAtUtc,
      kind,
      label: headline(kind),
      detail: o.cancelledReason ||
        (o.avgFillPrice != null ? `@ ${o.avgFillPrice.toFixed(4)}` : undefined) ||
        (o.broker ? `broker=${o.broker}` : undefined),
      symbol: o.symbol,
      strategyId: o.strategyId,
      href: "/oms",
    });
  }

  const newestFirst = opts?.newestFirst ?? true;
  events.sort((a, b) => {
    const c = (b.time || "").localeCompare(a.time || "");
    return newestFirst ? c : -c;
  });
  const limit = opts?.limit ?? 50;
  return events.slice(0, limit);
}

/**
 * Visual tone per event kind so the feed reads at a glance —
 * green/blue/amber/red maps the same colours used by the
 * OMS state pills + the cockpit warning panel.
 */
export function activityTone(kind: ActivityKind): { fg: string; icon: string } {
  switch (kind) {
    case "signal":          return { fg: "#a855f7", icon: "•" };
    case "order_enqueued":  return { fg: "#f59e0b", icon: "○" };
    case "order_approved":  return { fg: "#4f8cff", icon: "✓" };
    case "order_submitted": return { fg: "#4f8cff", icon: "→" };
    case "order_filled":    return { fg: "#1fc16b", icon: "✓" };
    case "order_rejected":  return { fg: "#ef4444", icon: "✗" };
    case "order_cancelled": return { fg: "#9ca3af", icon: "—" };
  }
}
