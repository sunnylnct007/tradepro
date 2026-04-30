import { useEffect, useState } from "react";
import { config } from "../config";

/** 'Is the system OK?' single screen.
 *
 * Polls /health/details (public, no auth) and renders a top-level
 * verdict, the API state, the Mac heartbeat, and per-universe data
 * freshness. Beats opening Compare and trying to read provenance bars
 * to figure out whether anything is broken. */

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

const POLL_MS = 30_000;

export function HealthPage() {
  const [data, setData] = useState<HealthDetailsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const url = new URL("/health/details", config.apiBaseUrl).toString();
    const tick = () => {
      fetch(url)
        .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
        .then((d) => { if (live) { setData(d); setError(null); } })
        .catch((e) => { if (live) setError(String(e)); });
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>System health</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0", maxWidth: 760 }}>
          Live read of the API, the Mac that produces results, and the data
          cache it serves. Polls every 30s. Auto-updates when something
          changes — no need to refresh.
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

          <Card title="Mac (worker)">
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
    </div>
  );
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
