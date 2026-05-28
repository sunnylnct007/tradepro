import { useEffect, useState } from "react";
import { config } from "../config";

/** 'Is the system OK?' single screen.
 *
 * Polls /health/details (public, no auth) and renders a top-level
 * verdict, the API state, the Strategy Engine heartbeat, and per-
 * universe data freshness. Beats opening Compare and trying to read
 * provenance bars to figure out whether anything is broken. */

interface HealthFreshness {
  universe: string;
  runId: string | null;
  ageHours: number;
  rowCount: number;
  rankMetric: string | null;
  tone: "fresh" | "stale" | "very_stale";
  generatedAtUtc: string;
}

interface HealthDetailsResponse {
  verdict: "ok" | "warn" | "needs_attention";
  utc: string;
  environment: string;
  gitSha: string;
  deploy?: {
    backendCommit: string;
    backendBuildTime: string;
    apiUptimeSeconds: number;
    postgres: { connected: boolean; latencyMs: number | null; error: string | null };
  };
  api: { status: string; uptimeSeconds: number };
  worker: {
    liveness: "alive" | "late" | "down";
    sinceLastPingSeconds: number | null;
    host: string | null;
    isProcessing: boolean;
    currentTask: { task: string; detail: string | null; phase: string | null } | null;
  };
  compareCache: { universes: number; freshness: HealthFreshness[] };
}

interface ProviderHealth {
  provider: string;
  label: string;
  status: "ok" | "degraded" | "down" | "disabled";
  detail: string;
  latencyMs: number | null;
  lastCheckedUtc: string;
  mode: string | null;
}

interface IntegrationsHealthResponse {
  verdict: "ok" | "warn" | "needs_attention";
  utc: string;
  providers: ProviderHealth[];
}

const POLL_MS = 30_000;

export function HealthPage() {
  const [data, setData] = useState<HealthDetailsResponse | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationsHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const detailsUrl = new URL("/health/details", config.apiBaseUrl).toString();
    const integrationsUrl = new URL("/health/integrations", config.apiBaseUrl).toString();
    const tick = () => {
      fetch(detailsUrl)
        .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
        .then((d) => { if (live) { setData(d); setError(null); } })
        .catch((e) => { if (live) setError(String(e)); });
      // Integrations probe is best-effort — Health page still renders
      // when /health/integrations is unreachable (older api versions
      // pre-shipping it).
      fetch(integrationsUrl)
        .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
        .then((d) => { if (live) setIntegrations(d); })
        .catch(() => {});
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>System health</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 880 }}>
          Live read of the API, the Strategy Engine that produces results,
          and the data cache it serves. Polls every 30s. Auto-updates when
          something changes — no need to refresh.
        </p>
      </div>

      {error && !data && (
        <div className="card" style={{ borderColor: "var(--down)", color: "var(--down)" }}>
          API unreachable: {error}
        </div>
      )}

      {data && <Verdict verdict={data.verdict} />}

      {data && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 14,
        }}>
          <Card title="API">
            <Row label="Status" value={data.api.status} colour="var(--up)" />
            <Row label="Environment" value={data.environment} />
            <Row label="Git" value={data.gitSha === "unknown" ? "—" : data.gitSha.slice(0, 8)} mono />
            <Row label="Uptime" value={fmtUptime(data.api.uptimeSeconds)} />
            <Row label="Server time" value={new Date(data.utc).toLocaleString()} />
          </Card>

          {data.deploy && (
            <Card title="Deployment">
              <Row
                label="Build commit"
                value={
                  data.deploy.backendCommit === "unknown"
                    ? "—"
                    : data.deploy.backendCommit.slice(0, 8)
                }
                mono
              />
              <Row
                label="Built at"
                value={
                  data.deploy.backendBuildTime === "unknown"
                    ? "—"
                    : new Date(data.deploy.backendBuildTime).toLocaleString()
                }
              />
              <Row
                label="Postgres"
                value={
                  data.deploy.postgres.connected
                    ? `up · ${data.deploy.postgres.latencyMs ?? "?"} ms`
                    : `DOWN — ${data.deploy.postgres.error ?? "no error returned"}`
                }
                colour={data.deploy.postgres.connected ? "var(--up)" : "var(--down)"}
              />
            </Card>
          )}

          <Card title="Strategy Engine">
            <Row
              label="Liveness"
              value={data.worker.liveness}
              colour={livenessColour(data.worker.liveness)}
            />
            <Row label="Host" value={data.worker.host ?? "—"} mono />
            <Row
              label="Last ping"
              value={data.worker.sinceLastPingSeconds === null
                ? "never"
                : fmtAgeSeconds(data.worker.sinceLastPingSeconds)}
            />
            <Row
              label="Processing"
              value={data.worker.isProcessing
                ? `${data.worker.currentTask?.task} — ${data.worker.currentTask?.detail ?? ""}`
                : "idle"}
              colour={data.worker.isProcessing ? "var(--up)" : undefined}
            />
          </Card>

          <Card title={`Compare cache (${data.compareCache.universes})`}>
            {data.compareCache.universes === 0 && (
              <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
                No comparisons pushed yet.
              </div>
            )}
            {data.compareCache.freshness.map((f) => (
              <Row
                key={f.universe}
                label={f.universe}
                value={`${f.rowCount} rows · ${f.ageHours}h`}
                colour={toneColour(f.tone)}
                mono
              />
            ))}
          </Card>
        </div>
      )}

      {integrations && <IntegrationsPanel data={integrations} />}
    </div>
  );
}

/** External-source health: Yahoo / Finnhub / Ollama / T212 with a
 * status pill, last-success age and the underlying detail. Polled
 * every 30s same as the rest of the page. Lets the user tell at a
 * glance whether today's verdicts came from healthy data. */
function IntegrationsPanel({ data }: { data: IntegrationsHealthResponse }) {
  return (
    <section className="card" style={{ padding: "14px 16px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
        <div className="stat-label">Data sources ({data.providers.length})</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Polled every 30s · degraded source may compromise today's verdicts
        </div>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
        gap: 10,
      }}>
        {data.providers.map((p) => (
          <ProviderTile key={p.provider} p={p} />
        ))}
      </div>
    </section>
  );
}

function ProviderTile({ p }: { p: ProviderHealth }) {
  const colour = providerColour(p.status);
  return (
    <div
      style={{
        padding: "10px 12px",
        border: `1px solid var(--border)`,
        borderLeft: `3px solid ${colour}`,
        borderRadius: 6,
        background: "rgba(0,0,0,0.12)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <strong style={{ fontSize: 13, color: "var(--text)" }}>{p.label}</strong>
        <span
          style={{
            fontSize: 10,
            color: colour,
            fontWeight: 700,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
          }}
        >
          {p.status}
          {p.mode ? ` · ${p.mode}` : ""}
        </span>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.4 }}>
        {p.detail}
      </div>
      {(p.latencyMs !== null || p.lastCheckedUtc) && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          {p.latencyMs !== null && <>latency {p.latencyMs}ms · </>}
          checked {timeAgo(p.lastCheckedUtc)}
        </div>
      )}
    </div>
  );
}

function providerColour(status: ProviderHealth["status"]): string {
  switch (status) {
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
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function Verdict({ verdict }: { verdict: HealthDetailsResponse["verdict"] }) {
  const colour =
    verdict === "ok" ? "var(--up)"
    : verdict === "warn" ? "var(--neutral)"
    : "var(--down)";
  const text =
    verdict === "ok" ? "All systems healthy"
    : verdict === "warn" ? "Minor issue — refresh recommended"
    : "Needs attention";
  return (
    <section
      className="card"
      style={{ borderLeft: `3px solid ${colour}`, padding: "14px 18px" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          display: "inline-block",
          width: 10, height: 10,
          borderRadius: 5,
          background: colour,
        }} />
        <strong style={{ fontSize: 16, color: "var(--text)" }}>{text}</strong>
      </div>
    </section>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card" style={{ padding: "14px 16px" }}>
      <div className="stat-label" style={{ marginBottom: 8 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {children}
      </div>
    </section>
  );
}

function Row({
  label,
  value,
  colour,
  mono,
}: {
  label: string;
  value: string;
  colour?: string;
  mono?: boolean;
}) {
  return (
    <div style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "baseline",
      gap: 12,
      fontSize: 13,
    }}>
      <span style={{ color: "var(--text-dim)" }}>{label}</span>
      <span
        className={mono ? "num" : ""}
        style={{ color: colour ?? "var(--text)", fontWeight: 600, textAlign: "right" }}
      >
        {value}
      </span>
    </div>
  );
}

function livenessColour(l: HealthDetailsResponse["worker"]["liveness"]): string {
  switch (l) {
    case "alive": return "var(--up)";
    case "late": return "var(--neutral)";
    case "down": return "var(--down)";
  }
}

function toneColour(t: HealthFreshness["tone"]): string {
  switch (t) {
    case "fresh": return "var(--up)";
    case "stale": return "var(--neutral)";
    case "very_stale": return "var(--down)";
  }
}

function fmtUptime(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h`;
  return `${Math.floor(hr / 24)}d`;
}

function fmtAgeSeconds(sec: number): string {
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}
