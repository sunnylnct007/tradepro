/**
 * ConnectivityBadge — minimal system-status traffic light for the top
 * bar. One dot (green = all ok, amber = degraded, red = something down),
 * click to drop down the per-service detail. Replaces the big in-cockpit
 * Connectivity card — system health is chrome, not a trading surface, so
 * it lives in the bar and stays out of the way until something's wrong.
 */
import { useEffect, useState } from "react";
import { config } from "../config";

type Provider = {
  provider: string;
  label: string;
  status: "ok" | "degraded" | "down" | "disabled";
  detail: string;
  latencyMs: number | null;
  lastCheckedUtc: string;
  mode: string | null;
};
type Resp = { verdict: "ok" | "warn" | "needs_attention"; utc: string; providers: Provider[] };

export function ConnectivityBadge() {
  const [data, setData] = useState<Resp | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(`${config.apiBaseUrl}/health/integrations`);
        if (!r.ok) return;
        const d: Resp = await r.json();
        if (!cancelled) setData(d);
      } catch { /* keep last */ }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const downCount = data?.providers.filter((p) => p.status === "down").length ?? 0;
  const degradedCount = data?.providers.filter((p) => p.status === "degraded").length ?? 0;
  const colour = !data ? "var(--text-muted)"
    : downCount > 0 ? "var(--down)"
    : degradedCount > 0 ? "var(--neutral)" : "var(--up)";

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="System connectivity — click for detail"
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "4px 9px", borderRadius: 999, cursor: "pointer",
          border: "1px solid var(--border)", background: open ? "var(--bg-hover)" : "transparent",
          color: "var(--text-dim)", fontSize: 11,
        }}
      >
        <span style={{
          width: 9, height: 9, borderRadius: "50%", background: colour,
          boxShadow: downCount > 0 ? `0 0 6px ${colour}` : "none",
        }} />
        {downCount > 0 ? `${downCount} down` : degradedCount > 0 ? `${degradedCount} degraded` : "systems ok"}
      </button>
      {open && data && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 50 }} />
          <div style={{
            position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 51,
            width: 320, maxHeight: 420, overflowY: "auto",
            background: "var(--surface-1, #0b1220)", border: "1px solid var(--border)",
            borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,0.35)", padding: 8,
          }}>
            {data.providers.map((p) => (
              <div key={p.provider} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "6px 8px" }}>
                <span style={{ width: 9, height: 9, borderRadius: "50%", marginTop: 4, flexShrink: 0, background: dotColour(p.status) }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                    <strong style={{ fontSize: 12, color: "var(--text)" }}>{p.label}</strong>
                    <span style={{ fontSize: 9, color: dotColour(p.status), textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      {p.status}{p.mode ? ` · ${p.mode}` : ""}
                    </span>
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 2, lineHeight: 1.4 }}>{p.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function dotColour(s: Provider["status"]): string {
  switch (s) {
    case "ok": return "var(--up)";
    case "degraded": return "var(--neutral)";
    case "down": return "var(--down)";
    case "disabled": return "var(--text-muted)";
  }
}
