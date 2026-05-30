/**
 * AlertBanner — top-of-cockpit operational alerts the trader must see
 * before doing anything else. Polls /api/alerts every 30s.
 *
 * The first (and most important) producer is the paper-session
 * fail-closed guard: when a strategy can't confirm its current position
 * from the broker (the golden source), it ABORTS the run rather than
 * trade on an assumed-flat book — which would stack duplicate orders.
 * That abort is invisible in a log file, so it surfaces here as a red
 * banner. Dismissing an alert resolves it server-side; it re-opens if
 * the condition recurs.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

type Severity = "info" | "warn" | "critical";

type Alert = {
  id: string;
  source: string;
  severity: Severity;
  code: string;
  title: string;
  detail: string;
  strategyId: string | null;
  broker: string | null;
  symbols: string[];
  occurrences: number;
  firstSeenUtc: string;
  lastSeenUtc: string;
};

export function AlertBanner() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [resolving, setResolving] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const resp = await api.alerts();
      setAlerts(resp.alerts);
    } catch {
      // Best-effort: a failed alerts fetch shouldn't itself raise noise
      // in the cockpit. SystemHealthRow already covers API liveness.
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const resolve = useCallback(async (id: string) => {
    setResolving((s) => new Set(s).add(id));
    try {
      await api.resolveAlert(id);
      setAlerts((a) => a.filter((x) => x.id !== id));
    } catch {
      setResolving((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
    }
  }, []);

  if (alerts.length === 0) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
      {alerts.map((a) => (
        <AlertRow
          key={a.id}
          alert={a}
          resolving={resolving.has(a.id)}
          onResolve={() => resolve(a.id)}
        />
      ))}
    </div>
  );
}

function AlertRow({
  alert,
  resolving,
  onResolve,
}: {
  alert: Alert;
  resolving: boolean;
  onResolve: () => void;
}) {
  const colour = severityColour(alert.severity);
  const tags = [alert.strategyId, alert.broker, ...alert.symbols].filter(Boolean) as string[];
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "10px 12px",
        borderRadius: 8,
        border: `1px solid ${colour}`,
        borderLeft: `4px solid ${colour}`,
        background: `${colour}14`,
      }}
    >
      <span style={{ fontSize: 16, lineHeight: "20px" }} aria-hidden>
        {alert.severity === "critical" ? "⛔" : alert.severity === "warn" ? "⚠️" : "ℹ️"}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <strong style={{ fontSize: 13, color: "var(--text)" }}>{alert.title}</strong>
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: colour,
            }}
          >
            {alert.severity}
          </span>
          {alert.occurrences > 1 && (
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              ×{alert.occurrences}
            </span>
          )}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 3, lineHeight: 1.45 }}>
          {alert.detail}
        </div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span>{alert.source}</span>
          {tags.map((t) => (
            <span
              key={t}
              style={{
                padding: "1px 6px",
                borderRadius: 999,
                background: "rgba(0,0,0,0.18)",
                border: "1px solid var(--border)",
              }}
            >
              {t}
            </span>
          ))}
          <span>· {timeAgo(alert.lastSeenUtc)}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onResolve}
        disabled={resolving}
        style={{
          flexShrink: 0,
          fontSize: 11,
          padding: "4px 10px",
          borderRadius: 6,
          border: "1px solid var(--border)",
          background: "transparent",
          color: "var(--text-dim)",
          cursor: resolving ? "default" : "pointer",
          opacity: resolving ? 0.5 : 1,
        }}
      >
        {resolving ? "…" : "Dismiss"}
      </button>
    </div>
  );
}

function severityColour(s: Severity): string {
  switch (s) {
    case "critical": return "var(--down)";
    case "warn": return "var(--neutral)";
    case "info": return "var(--up)";
  }
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "now";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return `${h}h ago`;
}
