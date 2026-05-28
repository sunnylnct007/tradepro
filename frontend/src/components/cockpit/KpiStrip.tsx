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
 * tz would mis-bucket fills near the user's midnight. Trader's
 * wall-clock day matters less than the broker's session boundary.
 */
import { InlineHint } from "../InlineHint";
import type { OmsOrderRow } from "../../api/client";
import type { T212Cash, T212PosResp } from "../../types/cockpit";

export function KpiStrip({
  cash, orders, positions, warningCount,
}: {
  cash: T212Cash | null;
  orders: OmsOrderRow[];
  positions: T212PosResp | null;
  warningCount: number;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const openStates = new Set(["PENDING_APPROVAL", "SUBMITTED", "WORKING", "PARTIALLY_FILLED"]);
  const openOrders = orders.filter((o) => openStates.has(o.state)).length;
  const fillsToday = orders.filter(
    (o) => o.state === "FILLED" && o.lastStateChangeAtUtc.slice(0, 10) === today,
  ).length;

  // Today's P&L — sum of unrealised across positions if T212 is on.
  // Falls back to "—" when the broker integration is disabled so we
  // never imply a number we don't actually have.
  const pnlSrc = positions?.enabled && positions.positions.length > 0
    ? positions.positions.reduce((n, p) => n + (p.unrealisedAbs ?? 0), 0)
    : null;
  const ccy = cash?.currency ?? positions?.positions[0]?.currency ?? "";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
        gap: 8,
        padding: "10px 14px",
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--bg-hover, rgba(255,255,255,0.03))",
        marginBottom: 12,
      }}
    >
      <KpiCell
        label="Cash (free)"
        value={cash?.free != null
          ? `${ccy} ${cash.free.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
          : "—"}
        sub={cash?.enabled === false ? "T212 disabled" : undefined}
        hint="Cash available for new orders on the selected T212 account (Invest product). Refreshed every cockpit poll."
      />
      <KpiCell
        label="Open orders"
        value={String(openOrders)}
        tone={openOrders > 0 ? "info" : undefined}
        hint="OMS orders in flight — PENDING_APPROVAL / SUBMITTED / WORKING / PARTIALLY_FILLED. Excludes terminal states."
      />
      <KpiCell
        label="Fills today"
        value={String(fillsToday)}
        tone={fillsToday > 0 ? "ok" : undefined}
        hint="Count of OMS orders whose state changed to FILLED today (UTC). Resets at 00:00 UTC."
      />
      <KpiCell
        label="Today's P&L"
        value={pnlSrc == null
          ? "—"
          : `${pnlSrc >= 0 ? "+" : ""}${ccy} ${pnlSrc.toFixed(2)}`}
        tone={pnlSrc == null ? undefined : pnlSrc >= 0 ? "ok" : "down"}
        hint="Sum of unrealised P&L across current T212 positions. Excludes realised P&L from positions already closed today."
      />
      <KpiCell
        label="Warnings"
        value={String(warningCount)}
        tone={warningCount > 0 ? "warn" : undefined}
        hint="Count of issues flagged in the Warnings panel below: T212 / OMS fetch errors, rejected orders, integration failures."
      />
    </div>
  );
}

function KpiCell({
  label, value, sub, tone, hint,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "ok" | "warn" | "down" | "info";
  hint?: string;
}) {
  const fg = TONE_FG[tone ?? "default"];
  return (
    <div>
      <div style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        {label}
        {hint && <InlineHint text={hint} />}
      </div>
      <div style={{
        fontSize: 18, fontWeight: 700, fontFamily: "monospace",
        color: fg, marginTop: 2,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

const TONE_FG: Record<"default" | "ok" | "warn" | "down" | "info", string> = {
  default: "var(--text)",
  ok:      "#1fc16b",
  warn:    "#f59e0b",
  down:    "#ef4444",
  info:    "#4f8cff",
};
