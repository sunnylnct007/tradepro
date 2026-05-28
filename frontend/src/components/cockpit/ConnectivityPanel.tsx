/**
 * ConnectivityPanel — at-a-glance broker / LLM / data-provider /
 * DB liveness so the trader doesn't have to leave the cockpit to
 * check whether anything is broken. Sources the same
 * /health/integrations payload the standalone /health page uses.
 *
 * Each tile renders status + latency + last-checked. ok/degraded/down/
 * disabled are colour-coded (green / amber / red / grey). When a
 * provider is "down" the verdict surfaces at the top so the trader
 * knows BEFORE they trigger a session.
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
  const verdictColour =
    data.verdict === "ok" ? "var(--up)"
    : data.verdict === "warn" ? "var(--neutral)" : "var(--down)";
  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        marginBottom: 8, fontSize: 11,
      }}>
        <span style={{
          padding: "2px 8px", borderRadius: 999,
          background: `${verdictColour}22`,
          color: verdictColour,
          fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase",
        }}>
          {data.verdict.replace("_", " ")}
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          {data.providers.length} services · polled every 30s
        </span>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 8,
      }}>
        {data.providers.map((p) => <ProviderTile key={p.provider} p={p} />)}
      </div>
    </div>
  );
}

function ProviderTile({ p }: { p: Provider }) {
  const colour = providerColour(p.status);
  return (
    <div style={{
      padding: "8px 10px",
      borderLeft: `3px solid ${colour}`,
      border: "1px solid var(--border)",
      borderRadius: 6,
      background: "rgba(0,0,0,0.10)",
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "baseline", gap: 8,
      }}>
        <strong style={{ fontSize: 12, color: "var(--text)" }}>{p.label}</strong>
        <span style={{
          fontSize: 9, color: colour, fontWeight: 700,
          letterSpacing: "0.06em", textTransform: "uppercase",
        }}>{p.status}{p.mode ? ` · ${p.mode}` : ""}</span>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.4 }}>
        {p.detail}
      </div>
      <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4 }}>
        {p.latencyMs !== null && <>latency {p.latencyMs}ms · </>}
        checked {timeAgo(p.lastCheckedUtc)}
      </div>
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
