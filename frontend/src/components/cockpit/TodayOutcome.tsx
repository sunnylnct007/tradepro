/**
 * TodayOutcome — English summary at the top of the cockpit so the
 * trader reads the day's story before scanning panels. Three lines
 * max: what fired, what filled / failed, who carried / dragged P&L.
 *
 * All computed client-side from already-fetched cockpit state — no
 * new endpoint. Hidden when nothing's happened today (no fires, no
 * fills, no positions) so a quiet day isn't padded with placeholders.
 */
import type { OmsOrderRow } from "../../api/client";
import type { LatestSession, T212PosResp } from "../../types/cockpit";

export function TodayOutcome({
  orders, positions, latestSessions,
}: {
  orders: OmsOrderRow[];
  positions: T212PosResp | null;
  latestSessions: LatestSession[];
}) {
  const summary = buildSummary(orders, positions, latestSessions);
  if (summary.empty) return null;
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
      <SignalLine
        firesToday={summary.firesToday}
        fillsToday={summary.fillsToday}
        rejectsToday={summary.rejectsToday}
        pendingToday={summary.pendingToday}
        firingStrategies={summary.firingStrategies}
      />
      {summary.sortedPos.length > 0 && (
        <PnlLine
          totalPnl={summary.totalPnl}
          ccy={summary.ccy}
          carrier={summary.carrier}
          dragger={summary.dragger}
        />
      )}
    </div>
  );
}

type Pos = T212PosResp["positions"][number];
type Summary = {
  empty: boolean;
  firesToday: number;
  firingStrategies: string[];
  fillsToday: number;
  rejectsToday: number;
  pendingToday: number;
  sortedPos: Pos[];
  carrier: Pos | undefined;
  dragger: Pos | undefined;
  totalPnl: number;
  ccy: string;
};

function buildSummary(
  orders: OmsOrderRow[],
  positions: T212PosResp | null,
  latestSessions: LatestSession[],
): Summary {
  const today = new Date().toISOString().slice(0, 10);

  let firesToday = 0;
  const firingSet = new Set<string>();
  for (const s of latestSessions) {
    if ((s.completedAtUtc ?? "").slice(0, 10) !== today) continue;
    for (const d of s.decisions) {
      if (d.action.startsWith("fire-")) {
        firesToday++;
        firingSet.add(s.strategy);
      }
    }
  }

  const todayOrders = orders.filter(
    (o) => o.lastStateChangeAtUtc.slice(0, 10) === today,
  );
  // Exclude administrative reconciliation rows from "fills today" —
  // those are synthetic OMS rows created by /api/admin/oms/reconcile-
  // from-t212-demo to sync OMS with existing broker positions. They
  // aren't strategy-driven fills and counting them inflates the
  // banner's signal-of-the-day picture (was saying "21 fills cleared"
  // when zero strategies fired today).
  const isStrategyDriven = (o: OmsOrderRow) =>
    o.strategyId !== "reconcile_from_broker"
    && o.strategyId !== "_monitor"
    && !o.strategyId?.startsWith("manual_")
    && o.placedBy !== "HUMAN";
  const fillsToday = todayOrders.filter(
    (o) => o.state === "FILLED" && isStrategyDriven(o),
  ).length;
  // Genuine rejections only — gate refusals + broker rejects. Exclude
  // CANCELLED because the T212 poller falsely cancels orders that aged
  // out of broker hot-cache ("broker_not_found_assume_terminal"); those
  // are not signal-quality rejections.
  const rejectsToday = todayOrders.filter(
    (o) => o.state === "REJECTED",
  ).length;
  // Pending orders ARE the day's signals — fires + intents that
  // haven't yet routed to the broker. The previous banner
  // ("No new signals fired today") ignored these and made the cockpit
  // look idle while orders were queued. "Pending" here = anything past
  // approval but not yet a terminal state.
  const pendingToday = todayOrders.filter(
    (o) => o.state === "PENDING_APPROVAL"
        || o.state === "SUBMITTED"
        || o.state === "WORKING"
        || o.state === "PARTIALLY_FILLED",
  ).length;

  const sortedPos = positions?.positions
    ? [...positions.positions]
        .filter((p) => p.unrealisedAbs != null)
        .sort((a, b) => (b.unrealisedAbs ?? 0) - (a.unrealisedAbs ?? 0))
    : [];
  const totalPnl = sortedPos.reduce((n, p) => n + (p.unrealisedAbs ?? 0), 0);

  return {
    empty: firesToday === 0 && fillsToday === 0 && rejectsToday === 0 && pendingToday === 0 && sortedPos.length === 0,
    firesToday,
    firingStrategies: Array.from(firingSet),
    fillsToday,
    rejectsToday,
    pendingToday,
    sortedPos,
    carrier: sortedPos[0],
    dragger: sortedPos[sortedPos.length - 1],
    totalPnl,
    ccy: positions?.positions[0]?.currency ?? "",
  };
}

function SignalLine({
  firesToday, fillsToday, rejectsToday, pendingToday, firingStrategies,
}: {
  firesToday: number; fillsToday: number; rejectsToday: number;
  pendingToday: number; firingStrategies: string[];
}) {
  // Signals = decision-trace fires OR pending orders. Pending orders
  // ARE today's signals — they came from strategy runs, are queued at
  // the broker boundary, and the trader/algo will act on them. The
  // banner now reflects that instead of saying "no signals" while
  // PENDING_APPROVAL rows sit in OMS.
  const signalCount = Math.max(firesToday, pendingToday);
  if (signalCount > 0) {
    return (
      <div>
        <strong style={{ color: "#a855f7" }}>
          {signalCount} signal{signalCount === 1 ? "" : "s"}
        </strong>{" "}
        {firingStrategies.length > 0 && (
          <>fired from <strong>{firingStrategies.join(", ")}</strong></>
        )}
        {pendingToday > 0 && (
          <> {" · "}<strong style={{ color: "#f59e0b" }}>{pendingToday} pending approval</strong></>
        )}
        {fillsToday > 0 && (
          <> {" · "}<strong style={{ color: "#1fc16b" }}>{fillsToday} filled</strong></>
        )}
        {rejectsToday > 0 && (
          <> {" · "}<strong style={{ color: "#ef4444" }}>{rejectsToday} rejected</strong></>
        )}
      </div>
    );
  }
  if (fillsToday > 0 || rejectsToday > 0) {
    return (
      <div>
        No new signals fired today.{" "}
        {fillsToday > 0 && (
          <>
            <strong style={{ color: "#1fc16b" }}>
              {fillsToday} fill{fillsToday === 1 ? "" : "s"}
            </strong>{" "}
            cleared (earlier intent).{" "}
          </>
        )}
        {rejectsToday > 0 && (
          <strong style={{ color: "#ef4444" }}>
            {rejectsToday} rejected — check the histogram on /oms.
          </strong>
        )}
      </div>
    );
  }
  return <div>Strategies ran but emitted no signals. No order activity yet.</div>;
}

function PnlLine({
  totalPnl, ccy, carrier, dragger,
}: {
  totalPnl: number; ccy: string; carrier: Pos | undefined; dragger: Pos | undefined;
}) {
  const pnlColor = totalPnl >= 0 ? "#1fc16b" : "#ef4444";
  return (
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
  );
}
