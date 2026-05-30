/**
 * ConnectivityPanel — compact TRAFFIC-LIGHT strip of broker / LLM /
 * data-provider / DB liveness. One coloured dot per service in a single
 * row so it costs almost no vertical space. All-green = nothing to do;
 * click any amber/red light to expand its detail (what's wrong, latency,
 * last-checked). Sources the same /health/integrations payload the
 * standalone /health page uses.
 */
import { useEffect, useState } from "react";
import { config } from "../../config";

type Provider = {
  provider: string;
  label: string;
  status: "ok" | "degraded" | "down" | "disabled";
  detail: string;
  latencyMs: number | null;
  lastCheckedUtc: string;
  mode: string | null;
};

type Resp = {
  verdict: "ok" | "warn" | "needs_attention";
  utc: string;
  providers: Provider[];
};

export function ConnectivityPanel() {
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const resp = await fetch(`${config.apiBaseUrl}/health/integrations`);
        if (!resp.ok) throw new Error(`${resp.status}`);
        const d: Resp = await resp.json();
        if (cancelled) return;
        setData(d);
        setErr(null);
      } catch (e) {
        if (cancelled) return;
        setErr(String(e));
      }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (err) {
    return <div style={{ fontSize: 11, color: "var(--down)" }}>connectivity probe failed: {err}</div>;
  }
  if (!data) {
    return <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading connectivity…</div>;
  }

  const open = data.providers.find((p) => p.provider === expanded) ?? null;

  return (
    <div>
      {/* The traffic-light row — every service as one dot + short label. */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
        {data.providers.map((p) => {
          const colour = providerColour(p.status);
          const isOpen = expanded === p.provider;
          return (
            <button
              key={p.provider}
              type="button"
              onClick={() => setExpanded(isOpen ? null : p.provider)}
              title={`${p.label}: ${p.status}${p.mode ? ` (${p.mode})` : ""} — click for detail`}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "3px 9px", borderRadius: 999, cursor: "pointer",
                fontSize: 11, lineHeight: 1.4,
                color: "var(--text-dim)",
                background: isOpen ? `${colour}1f` : "transparent",
                border: `1px solid ${isOpen ? colour : "var(--border)"}`,
              }}
            >
              <span style={{
                width: 9, height: 9, borderRadius: "50%",
                background: colour,
                boxShadow: p.status === "down" ? `0 0 6px ${colour}` : "none",
                flexShrink: 0,
              }} />
              {p.label}
            </button>
          );
        })}
      </div>

      {/* Detail for the clicked light — only the one the trader opened. */}
      {open && (
        <div style={{
          marginTop: 8, padding: "8px 10px", borderRadius: 6,
          border: `1px solid ${providerColour(open.status)}`,
          borderLeft: `3px solid ${providerColour(open.status)}`,
          background: "rgba(0,0,0,0.10)",
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
            <strong style={{ fontSize: 12, color: "var(--text)" }}>{open.label}</strong>
            <span style={{
              fontSize: 9, color: providerColour(open.status), fontWeight: 700,
              letterSpacing: "0.06em", textTransform: "uppercase",
            }}>{open.status}{open.mode ? ` · ${open.mode}` : ""}</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.45 }}>
            {open.detail || "No detail reported."}
          </div>
          <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4 }}>
            {open.latencyMs !== null && <>latency {open.latencyMs}ms · </>}
            checked {timeAgo(open.lastCheckedUtc)}
          </div>
        </div>
      )}
    </div>
  );
}

function providerColour(s: Provider["status"]): string {
  switch (s) {
    case "ok": return "var(--up)";
    case "degraded": return "var(--neutral)";
    case "down": return "var(--down)";
    case "disabled": return "var(--text-muted)";
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
