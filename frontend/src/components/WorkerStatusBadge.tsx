import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { WorkerHealth } from "../api/types";

const POLL_MS = 15_000;

/** Compact status pill for the Strategy Engine that produces all the
 * comparator results. Colours:
 *
 *   ● green   alive (last ping ≤ 30 min)  + 'processing X' or 'idle'
 *   ● amber   late  (last ping ≤ 24h)     + 'might have missed a heartbeat'
 *   ● red     down  (last ping > 24h)     + 'check the worker container'
 *
 * When the engine is processing, the dot pulses and the label shows the
 * task / detail / phase + how long it's been running. That tells the
 * user 'a comparison is in flight, sit tight' instead of 'data is stale'.
 *
 * Polls /api/health/worker every 15s; cheap (single in-memory record). */
export function WorkerStatusBadge() {
  const [health, setHealth] = useState<WorkerHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () => {
      api.workerHealth()
        .then((h) => { if (live) { setHealth(h); setError(null); } })
        .catch((e) => { if (live) setError(String(e)); });
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, []);

  if (error && !health) {
    return (
      <Pill colour="var(--down)">
        <Dot colour="var(--down)" /> Worker unknown
      </Pill>
    );
  }
  if (!health) {
    return (
      <Pill colour="var(--text-muted)">
        <Dot colour="var(--text-muted)" /> …
      </Pill>
    );
  }

  const colour = livenessColour(health.liveness);
  const processing = health.isProcessing && health.currentTask;
  return (
    <Pill colour={colour} title={detailedTitle(health)}>
      <Dot colour={colour} pulse={processing ? true : false} />
      {processing ? (
        <span style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}>
          <strong style={{ color: colour }}>Processing</strong>
          <span style={{ color: "var(--text-dim)", fontSize: 11 }}>
            {health.currentTask?.detail ?? health.currentTask?.task}
          </span>
          {health.currentTask?.phase && (
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
              · {health.currentTask.phase}
            </span>
          )}
          {health.currentTask?.elapsedSeconds !== null && (
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
              · {health.currentTask?.elapsedSeconds}s
            </span>
          )}
        </span>
      ) : (
        <span style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}>
          <strong style={{ color: colour }}>{labelFor(health.liveness)}</strong>
          <span style={{ color: "var(--text-dim)", fontSize: 11 }}>
            {health.summary}
          </span>
        </span>
      )}
    </Pill>
  );
}

function Pill({
  children,
  colour,
  title,
}: {
  children: React.ReactNode;
  colour: string;
  title?: string;
}) {
  return (
    <div
      title={title}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 10px",
        border: `1px solid ${colour}`,
        borderRadius: 999,
        background: "rgba(255,255,255,0.02)",
        fontSize: 12,
      }}
    >
      {children}
    </div>
  );
}

function Dot({ colour, pulse }: { colour: string; pulse?: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: 4,
        background: colour,
        boxShadow: pulse ? `0 0 0 0 ${colour}` : "none",
        animation: pulse ? "tradepro-pulse 1.4s ease-out infinite" : "none",
      }}
    />
  );
}

function livenessColour(l: WorkerHealth["liveness"]): string {
  switch (l) {
    case "alive": return "var(--up)";
    case "late": return "var(--neutral)";
    case "down": return "var(--down)";
  }
}

function labelFor(l: WorkerHealth["liveness"]): string {
  switch (l) {
    case "alive": return "Engine alive";
    case "late": return "Engine late";
    case "down": return "Engine silent";
  }
}

function detailedTitle(h: WorkerHealth): string {
  const parts: string[] = [];
  if (h.host) parts.push(`Host: ${h.host}`);
  if (h.gitSha) parts.push(`Git: ${h.gitSha.slice(0, 8)}`);
  if (h.sentAtUtc) parts.push(`Last ping: ${new Date(h.sentAtUtc).toLocaleString()}`);
  if (h.uptimeSeconds !== null && h.uptimeSeconds !== undefined) {
    const hours = Math.round(h.uptimeSeconds / 3600);
    parts.push(`Uptime: ${hours}h`);
  }
  return parts.join(" · ");
}
