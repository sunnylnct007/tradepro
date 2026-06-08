/**
 * StrategyHealthPanel — always-visible per-strategy healthcheck on the cockpit
 * home. Surfaces RUN TIMING (last run + how long ago) and outcome (fills /
 * cancels today) so a silently-stopped or all-cancels strategy reads RED at a
 * glance — instead of looking the same as a correctly-idle one.
 *
 * Built after a market-hours gate bug stopped ichimoku_equity trading for hours
 * with zero UI signal (feedback_surface_system_health_in_ui).
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

const SEP = "#1b2233";

type Health = Awaited<ReturnType<typeof api.strategiesHealth>>["strategies"][number];

const STATUS: Record<Health["status"], { dot: string; label: string; color: string }> = {
  healthy: { dot: "🟢", label: "Trading",  color: "#3fb950" },
  idle:    { dot: "🟡", label: "Idle",     color: "#d29922" },
  blocked: { dot: "🔴", label: "Blocked",  color: "#f85149" },
  stale:   { dot: "🔴", label: "Not running", color: "#f85149" },
  unknown: { dot: "⚪", label: "Unknown",  color: "var(--text-muted)" },
};

function ago(mins: number | null): string {
  if (mins == null) return "never";
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  const h = Math.floor(mins / 60);
  return `${h}h ${Math.round(mins % 60)}m ago`;
}

export function StrategyHealthPanel() {
  const [rows, setRows] = useState<Health[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    const load = async () => {
      try {
        const d = await api.strategiesHealth();
        if (!cancel) { setRows(d.strategies); setErr(null); }
      } catch (e) {
        if (!cancel) setErr(e instanceof Error ? e.message : "failed to load");
      }
    };
    load();
    const t = setInterval(load, 30_000); // refresh every 30s
    return () => { cancel = true; clearInterval(t); };
  }, []);

  const anyBad = rows?.some((r) => r.status === "blocked" || r.status === "stale");

  return (
    <div style={{
      border: `1px solid ${anyBad ? "#f85149" : "var(--border)"}`,
      borderRadius: 8, padding: "8px 10px", background: "rgba(255,255,255,0.02)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>Strategy health</span>
        <span style={{ fontSize: 10.5, color: "var(--text-muted)" }}>
          run timing + today's fills — RED = stopped or all-cancelled
        </span>
        {anyBad && (
          <span style={{ marginLeft: "auto", fontSize: 10.5, color: "#f85149", fontWeight: 700 }}>
            ⚠ needs attention
          </span>
        )}
      </div>

      {err && <div style={{ fontSize: 11, color: "#f85149" }}>health unavailable: {err}</div>}
      {!rows && !err && <div style={{ fontSize: 11, color: "var(--text-muted)" }}>loading…</div>}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {rows?.map((r) => {
          const s = STATUS[r.status];
          return (
            <div key={r.strategy} title={r.reason} style={{
              display: "grid",
              gridTemplateColumns: "minmax(150px,1.4fr) 88px 1fr auto",
              alignItems: "center", gap: 8, fontSize: 11.5,
              padding: "4px 6px", borderTop: `1px solid ${SEP}`,
            }}>
              <span style={{ fontWeight: 600 }}>{r.label}</span>
              <span style={{ color: s.color, fontWeight: 700 }}>{s.dot} {s.label}</span>
              <span style={{ color: "var(--text-dim)" }}>{r.reason}</span>
              <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                ran {ago(r.minutesSinceRun)} · {r.today.fills}F/{r.today.cancels}C
                {r.today.pending ? `/${r.today.pending}P` : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
