/**
 * SystemHealthRow — five pills at the very top of /trader showing
 * whether the chain the trader needs (API, Postgres, Mac daemon,
 * T212, Yahoo data) is healthy at this moment.
 *
 * "Build trust before breadth" — the trader's mental cost of a
 * trading decision goes up sharply if they can't see whether the
 * engine is even alive. One row of green/amber/red badges answers
 * that question without forcing a click.
 *
 * Polls /health/details + /health/integrations every 60s; survives
 * endpoint blips by keeping the last-good state visible.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { config } from "../../config";

type PillStatus = "ok" | "warn" | "down";
type Pill = { label: string; status: PillStatus; detail: string };

export function SystemHealthRow() {
  const [details, setDetails] = useState<Record<string, unknown> | null>(null);
  const [integrations, setIntegrations] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const d = await fetch(new URL("/health/details", config.apiBaseUrl).toString());
        if (d.ok && live) setDetails(await d.json());
      } catch { /* keep last good */ }
      try {
        const i = await fetch(new URL("/health/integrations", config.apiBaseUrl).toString());
        if (i.ok && live) setIntegrations(await i.json());
      } catch { /* best-effort */ }
    };
    void tick();
    const id = setInterval(() => void tick(), 60_000);
    return () => { live = false; clearInterval(id); };
  }, []);

  const pills = buildPills(details, integrations);
  return (
    <div
      style={{
        display: "flex", gap: 6, flexWrap: "wrap",
        marginBottom: 10, fontSize: 11,
      }}
    >
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
        alignSelf: "center", marginRight: 2,
      }}>
        Health
      </span>
      {pills.map((p, i) => (<PillBadge key={i} pill={p} />))}
    </div>
  );
}

function buildPills(
  details: Record<string, unknown> | null,
  integrations: Record<string, unknown> | null,
): Pill[] {
  const pills: Pill[] = [];

  const api = details?.api as Record<string, unknown> | undefined;
  const apiUp = Number(api?.uptimeSeconds ?? 0);
  const deploy = details?.deploy as Record<string, unknown> | undefined;
  const commit = String(deploy?.backendCommit ?? "").slice(0, 7);
  pills.push({
    label: "API",
    status: details ? "ok" : "down",
    detail: details
      ? `Up ${fmtUptime(apiUp)} · commit ${commit}`
      : "API unreachable — frontend can't fetch /health/details",
  });

  const pg = deploy?.postgres as Record<string, unknown> | undefined;
  pills.push({
    label: "Postgres",
    status: pg?.connected ? "ok" : "down",
    detail: pg?.connected
      ? `Connected · ${pg.latencyMs ?? "?"}ms last probe`
      : `Disconnected: ${pg?.error ?? "unknown"}`,
  });

  const worker = details?.worker as Record<string, unknown> | undefined;
  const liveness = (worker?.liveness as string) ?? "down";
  const sinceLast = Number(worker?.sinceLastPingSeconds ?? -1);
  pills.push({
    label: "Mac daemon",
    status: liveness === "alive" ? "ok" : liveness === "late" ? "warn" : "down",
    detail: worker
      ? [
          liveness,
          sinceLast >= 0 ? `last ping ${fmtUptime(sinceLast)} ago` : null,
          worker.host ? `host ${worker.host}` : null,
          worker.isProcessing ? "processing" : null,
        ].filter(Boolean).join(" · ")
      : "Mac worker hasn't pinged yet",
  });

  const providers = (integrations?.providers as Array<Record<string, unknown>> | undefined) ?? [];
  const t212 = providers.find((p) => p.provider === "trading212");
  if (t212) {
    pills.push({
      label: "T212",
      status: t212.status === "ok" ? "ok" : t212.status === "disabled" ? "warn" : "down",
      detail: `${t212.label} (${t212.mode ?? "live"}) · ${t212.detail}`,
    });
  }
  const yahoo = providers.find((p) => p.provider === "yahoo");
  if (yahoo) {
    pills.push({
      label: "Yahoo data",
      status: yahoo.status === "ok" ? "ok" : "warn",
      detail: `${yahoo.label} · ${yahoo.detail}`,
    });
  }
  return pills;
}

function PillBadge({ pill }: { pill: Pill }) {
  const tone = PILL_TONES[pill.status];
  return (
    <Link
      to="/health"
      title={pill.detail}
      style={{
        display: "inline-flex", gap: 5, alignItems: "center",
        padding: "2px 9px", borderRadius: 999, fontSize: 11,
        border: `1px solid ${tone.border}`,
        background: tone.bg,
        color: tone.fg,
        textDecoration: "none", letterSpacing: "0.02em",
      }}
    >
      <span style={{ fontSize: 10 }}>
        {pill.status === "ok" ? "✓" : pill.status === "warn" ? "⚠" : "✗"}
      </span>
      {pill.label}
    </Link>
  );
}

const PILL_TONES: Record<PillStatus, { fg: string; bg: string; border: string }> = {
  ok:   { fg: "#1fc16b", bg: "rgba(31,193,107,0.06)",  border: "rgba(31,193,107,0.30)" },
  warn: { fg: "#f59e0b", bg: "rgba(245,158,11,0.06)",  border: "rgba(245,158,11,0.30)" },
  down: { fg: "#ef4444", bg: "rgba(239,68,68,0.06)",   border: "rgba(239,68,68,0.30)" },
};

function fmtUptime(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  return `${Math.floor(sec / 86400)}d`;
}
