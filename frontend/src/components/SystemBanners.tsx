/**
 * SystemBanners — global, always-visible strip(s) that warn the
 * operator about non-normal system state. Two independent banners
 * mounted in Layout above the main content, both today-only by
 * design (transient alerts; historical events live on /risk or
 * /admin pages).
 *
 *   1. System state — when system_state.mode ≠ normal (FROZEN /
 *      PANIC). Red strip with the reason + who set it + how to
 *      resume. Operator should not be confused about why approvals
 *      are failing.
 *
 *   2. Position drift — when there are unresolved drift events.
 *      Amber strip with count + severity breakdown + link to /risk.
 *      Soft alert (vs the system_state hard alert) — broker disagrees
 *      with our internal records; review before placing trades.
 *
 * Both poll every 30s. Cheap (each endpoint is one indexed row /
 * partial-index lookup). Banners auto-dismiss when state clears.
 */
import { useCallback, useEffect, useState } from "react";
import { config } from "../config";

interface SystemState {
  mode: string;
  reason: string | null;
  setAtUtc: string | null;
  setBy: string | null;
  isTradingFrozen: boolean;
  isPanic: boolean;
}

interface DriftEvent {
  id: number;
  broker: string;
  symbol: string;
  severity: string;
  qtyDrift: number;
  detectedAtUtc: string;
}

const POLL_MS = 30_000;

export function SystemBanners() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      <SystemStateBanner />
      <PositionDriftBanner />
    </div>
  );
}

// ─── System state banner ────────────────────────────────────────────────────

function SystemStateBanner() {
  const [state, setState] = useState<SystemState | null>(null);
  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${config.apiBaseUrl}/api/system/state`);
      if (!resp.ok) return;
      const data = (await resp.json()) as SystemState;
      setState(data);
    } catch {
      // Silent — banner just won't render.
    }
  }, []);
  useEffect(() => {
    void load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);
  if (!state || !state.isTradingFrozen) return null;
  const isPanic = state.isPanic;
  return (
    <div
      role="alert"
      style={{
        padding: "10px 18px",
        background: isPanic ? "rgba(239,68,68,0.15)" : "rgba(245,158,11,0.12)",
        borderBottom: isPanic ? "2px solid #ef4444" : "2px solid #f59e0b",
        color: isPanic ? "#fecaca" : "#fde68a",
        fontSize: 13,
        display: "flex", alignItems: "center", gap: 10,
      }}
    >
      <span style={{ fontSize: 16, lineHeight: 1 }}>{isPanic ? "🛑" : "⏸"}</span>
      <span style={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {isPanic ? "PANIC" : "FROZEN"}
      </span>
      <span style={{ opacity: 0.9 }}>
        {state.reason || "(no reason recorded)"}
        {state.setBy ? <> · set by <strong>{state.setBy}</strong></> : null}
        {state.setAtUtc ? <> · {new Date(state.setAtUtc).toLocaleString()}</> : null}
      </span>
      <span style={{
        marginLeft: "auto", fontSize: 11, opacity: 0.75,
        fontFamily: "ui-monospace, Menlo, monospace",
      }}>
        {isPanic
          ? "all orders refused — POST /api/system/resume to restore"
          : "new BUYs refused — defensive SELLs still allowed — POST /api/system/resume"}
      </span>
    </div>
  );
}

// ─── Position drift banner ──────────────────────────────────────────────────

function PositionDriftBanner() {
  const [events, setEvents] = useState<DriftEvent[]>([]);
  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${config.apiBaseUrl}/api/positions/drift?unresolved=true&limit=20`);
      if (!resp.ok) return;
      const data = (await resp.json()) as { drift?: DriftEvent[] };
      setEvents(data.drift ?? []);
    } catch {
      // Silent.
    }
  }, []);
  useEffect(() => {
    void load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);
  if (events.length === 0) return null;
  const counts = events.reduce<Record<string, number>>((acc, e) => {
    acc[e.severity] = (acc[e.severity] ?? 0) + 1;
    return acc;
  }, {});
  const hasCritical = (counts.critical ?? 0) > 0;
  return (
    <div
      role="status"
      style={{
        padding: "8px 18px",
        background: hasCritical ? "rgba(239,68,68,0.10)" : "rgba(245,158,11,0.08)",
        borderBottom: hasCritical ? "1px solid #ef4444" : "1px solid rgba(245,158,11,0.45)",
        color: "var(--text)",
        fontSize: 12,
        display: "flex", alignItems: "center", gap: 10,
      }}
    >
      <span style={{ fontSize: 14, lineHeight: 1 }}>⚠</span>
      <span style={{
        fontWeight: 700, color: hasCritical ? "#ef4444" : "#f59e0b",
        textTransform: "uppercase", letterSpacing: "0.05em",
      }}>
        Position drift
      </span>
      <span style={{ color: "var(--text-dim)" }}>
        {events.length} unresolved
        {counts.critical ? <> · <strong style={{ color: "#ef4444" }}>{counts.critical} critical</strong></> : null}
        {counts.major ? <> · <strong style={{ color: "#f59e0b" }}>{counts.major} major</strong></> : null}
        {counts.minor ? <> · {counts.minor} minor</> : null}
        {" — broker holdings disagree with our records"}
      </span>
      <a
        href="/admin/data"
        style={{
          marginLeft: "auto", fontSize: 11,
          color: "var(--text-dim)", textDecoration: "underline",
        }}
      >
        review →
      </a>
    </div>
  );
}
