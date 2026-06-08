/**
 * StrategyHealthPanel — compact per-strategy traffic light on the cockpit.
 *
 * Collapsed by default to ONE line (a dot per strategy + "⚠ needs attention"
 * when any is blocked/stale) so it costs almost no screen space; click to expand
 * the per-strategy detail (status, reason, last-order timing, today's fills).
 *
 * Surfaces the gap a worker-level pill can't: one strategy can silently die
 * (skip/cancel every order) while the Mac daemon still pings "alive"
 * (feedback_surface_system_health_in_ui).
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

const SEP = "#1b2233";

type Health = Awaited<ReturnType<typeof api.strategiesHealth>>["strategies"][number];

const STATUS: Record<Health["status"], { dot: string; label: string; color: string }> = {
  healthy: { dot: "🟢", label: "Trading",          color: "#3fb950" },
  idle:    { dot: "🟡", label: "Idle (no setup)",  color: "#d29922" },
  blocked: { dot: "🔴", label: "Blocked",          color: "#f85149" },
  stale:   { dot: "🔴", label: "No recent orders", color: "#f85149" },
  unknown: { dot: "⚪", label: "No orders yet",    color: "var(--text-muted)" },
};

function ago(mins: number | null): string {
  if (mins == null) return "never";
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h}h ${Math.round(mins % 60)}m ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function StrategyHealthPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [rows, setRows] = useState<Health[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(defaultOpen);

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
    const t = setInterval(load, 30_000);
    return () => { cancel = true; clearInterval(t); };
  }, []);

  const anyBad = rows?.some((r) => r.status === "blocked" || r.status === "stale");
  const anyWarn = rows?.some((r) => r.status === "idle" || r.status === "unknown");

  return (
    <div style={{
      border: `1px solid ${anyBad ? "#f85149" : "var(--border)"}`,
      borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden",
    }}>
      {/* Collapsed traffic-light bar — click to expand */}
      <div
        onClick={() => setOpen((o) => !o)}
        title="Click for per-strategy detail"
        style={{
          display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
          padding: "6px 10px", fontSize: 11.5, userSelect: "none",
        }}
      >
        <span style={{ fontWeight: 700 }}>Strategy health</span>
        {/* one dot per strategy — the traffic light */}
        <span style={{ display: "flex", gap: 6 }}>
          {rows?.map((r) => (
            <span key={r.strategy} title={`${r.label}: ${STATUS[r.status].label} — ${r.reason}`}>
              {STATUS[r.status].dot}
            </span>
          ))}
          {!rows && !err && <span style={{ color: "var(--text-muted)" }}>…</span>}
          {err && <span style={{ color: "#f85149" }}>⚠ unavailable</span>}
        </span>
        {anyBad
          ? <span style={{ color: "#f85149", fontWeight: 700 }}>⚠ needs attention</span>
          : anyWarn
            ? <span style={{ color: "#d29922" }}>some idle</span>
            : rows && <span style={{ color: "#3fb950" }}>all trading</span>}
        <span style={{ marginLeft: "auto", color: "var(--text-muted)" }}>{open ? "▲" : "▼"}</span>
      </div>

      {/* Expanded detail */}
      {open && rows && (
        <div style={{ padding: "2px 10px 8px" }}>
          {rows.map((r) => {
            const s = STATUS[r.status];
            return (
              <div key={r.strategy} style={{
                display: "grid",
                gridTemplateColumns: "minmax(150px,1.3fr) 120px 1fr auto",
                alignItems: "center", gap: 8, fontSize: 11.5,
                padding: "5px 4px", borderTop: `1px solid ${SEP}`,
              }}>
                <span style={{ fontWeight: 600 }}>{r.label}</span>
                <span style={{ color: s.color, fontWeight: 700 }}>{s.dot} {s.label}</span>
                <span style={{ color: "var(--text-dim)" }}>{r.reason}</span>
                <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                  last order {ago(r.minutesSinceOrder)} · {r.today.fills}F/{r.today.cancels}C
                  {r.today.pending ? `/${r.today.pending}P` : ""}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
