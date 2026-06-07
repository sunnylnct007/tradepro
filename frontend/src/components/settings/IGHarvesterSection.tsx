/**
 * IGHarvesterSection — Phase P2.1 cockpit panel for the IG L1
 * snapshot harvester. Built FOR THE SUPPORT TEAM — every value a
 * support engineer would ask about ("is it running, when did it
 * last tick, what was the last error, how fast are quotes
 * coming in") answers in one glance.
 *
 * Layout:
 *   1. Top status banner — verdict colour chip + "last tick X
 *      seconds ago / next tick in Y seconds" + on/off + epic count.
 *      This is the chip the support team looks at FIRST.
 *   2. Per-symbol coverage grid — n_polls / n_quotes / avg / min /
 *      max spread, sorted by lowest avg first (cleanest book up).
 *      Red row = fill_rate < 50% (something's wrong for that name).
 *   3. Recent feed — last 50 snapshots, newest first. Shows the
 *      lake actually filling up. Red row = no quote (market closed
 *      or instrument halted).
 *
 * Refreshes every 15 seconds while mounted so the support team
 * sees the harvester moving without manual page reloads.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

type Status = Awaited<ReturnType<typeof api.igHarvesterStatus>>;
type Recent = Awaited<ReturnType<typeof api.igSnapshotsRecent>>;

const VERDICT_COLORS: Record<Status["verdict"], string> = {
  healthy:      "#16a34a",
  stale:        "#dc2626",
  error:        "#dc2626",
  disabled:     "#6b7280",
  never_ticked: "#ca8a04",
};
const VERDICT_LABELS: Record<Status["verdict"], string> = {
  healthy:      "HEALTHY",
  stale:        "STALE",
  error:        "ERROR",
  disabled:     "DISABLED",
  never_ticked: "NEVER TICKED",
};
const VERDICT_TOOLTIPS: Record<Status["verdict"], string> = {
  healthy:      "Last tick recent and clean. Lake is filling.",
  stale:        "Last tick older than 3 cadence intervals. Harvester may be frozen.",
  error:        "Last tick threw — see Last error below.",
  disabled:     "IG:Harvester:Enabled=false in config. Flip and restart the API to start capturing.",
  never_ticked: "Harvester process started but has not completed a tick yet.",
};

export function IGHarvesterSection() {
  return (
    <Section title="IG L1 Snapshot Harvester (Phase P2)">
      <RoadmapNote />
      <StatusPanel />
      <PerSymbolCoverage />
      <RecentFeed />
    </Section>
  );
}

function RoadmapNote() {
  return (
    <div
      style={{
        padding: "10px 14px",
        marginBottom: 14,
        background: "rgba(255,255,255,0.04)",
        borderLeft: "3px solid #1fc16b",
        borderRadius: 4,
        fontSize: 11,
        color: "var(--text-dim)",
        lineHeight: 1.55,
      }}
    >
      <strong style={{ color: "var(--text)" }}>What this is.</strong>{" "}
      The "rely less on Yahoo" data lake. The harvester polls IG
      /markets/{`{epic}`} every 5 min for the configured universe and
      writes one row per poll to <code>ig_l1_snapshots</code>. We OWN
      the time series — no provider revisions, no rate limits, no
      "what did SPY look like at 14:35 yesterday" guesswork. Backtest
      replay (Phase P5) reads from here.
    </div>
  );
}

// ─── Top status banner ─────────────────────────────────────────────

function StatusPanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await api.igHarvesterStatus();
        if (alive) {
          setStatus(r);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void load();
    const t = setInterval(load, 15_000);  // refresh every 15s
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (error) return <ErrorText>{error}</ErrorText>;
  if (!status) return <Muted>Loading harvester status…</Muted>;

  const color = VERDICT_COLORS[status.verdict];
  const lastTickLabel = status.last_tick_at_utc
    ? `${status.seconds_since_last_tick}s ago (${new Date(status.last_tick_at_utc).toLocaleTimeString()})`
    : "never";
  const nextTickLabel = status.next_tick_eta_utc && status.seconds_until_next_tick !== null
    ? `${status.seconds_until_next_tick}s (${new Date(status.next_tick_eta_utc).toLocaleTimeString()})`
    : "—";

  return (
    <Subsection title="Harvester status">
      <div style={{
        padding: "12px 16px",
        border: `1px solid ${color}55`,
        borderLeft: `4px solid ${color}`,
        background: `${color}11`,
        borderRadius: 4,
        marginBottom: 12,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span
            title={VERDICT_TOOLTIPS[status.verdict]}
            style={{
              padding: "4px 10px",
              fontSize: 11, fontWeight: 700, fontFamily: "monospace",
              borderRadius: 4,
              background: color,
              color: "white",
              letterSpacing: "0.05em",
            }}
          >
            {VERDICT_LABELS[status.verdict]}
          </span>
          <StatChip label="Enabled" value={status.enabled ? "yes" : "no"} />
          <StatChip label="Cadence" value={`${status.interval_seconds}s`} />
          <StatChip label="Configured epics" value={String(status.configured_epic_count)} />
          <StatChip label="Last tick" value={lastTickLabel} />
          <StatChip label="Next tick" value={nextTickLabel} />
          <StatChip
            label="Last tick result"
            value={`${status.last_tick_captured} captured · ${status.last_tick_failed} failed`}
          />
          <StatChip
            label="Started"
            value={new Date(status.started_at_utc).toLocaleString()}
          />
        </div>
        {status.last_error && (
          <div style={{
            marginTop: 10, padding: "6px 10px",
            background: "rgba(220, 38, 38, 0.12)",
            border: "1px solid rgba(220, 38, 38, 0.4)",
            borderRadius: 3, fontSize: 11,
            color: "#dc2626", fontFamily: "monospace",
          }}>
            <strong>Last error:</strong> {status.last_error}
          </div>
        )}
        {status.verdict === "disabled" && (
          <div style={{
            marginTop: 10, fontSize: 11, color: "var(--text-dim)",
            lineHeight: 1.5,
          }}>
            Harvester is off by config. To enable, set{" "}
            <code style={{
              background: "rgba(0,0,0,0.2)", padding: "1px 5px", borderRadius: 3,
            }}>IG:Harvester:Enabled=true</code>{" "}
            + populate{" "}
            <code style={{
              background: "rgba(0,0,0,0.2)", padding: "1px 5px", borderRadius: 3,
            }}>IG:Harvester:Epics</code>{" "}
            and restart the API.
          </div>
        )}
      </div>
    </Subsection>
  );
}

function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <span style={{
      display: "inline-flex", flexDirection: "column",
      padding: "2px 8px",
      fontSize: 10, fontFamily: "monospace",
      borderRadius: 3,
      background: "rgba(255,255,255,0.05)",
      border: "1px solid var(--border)",
    }}>
      <span style={{ color: "var(--text-muted)", fontSize: 9, textTransform: "uppercase" }}>
        {label}
      </span>
      <span style={{ color: "var(--text)", fontWeight: 600 }}>{value}</span>
    </span>
  );
}

// ─── Per-symbol coverage grid ──────────────────────────────────────

function PerSymbolCoverage() {
  const [data, setData] = useState<Recent | null>(null);
  const [windowHours, setWindowHours] = useState<number>(24);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await api.igSnapshotsRecent({ sinceHours: windowHours, limit: 50 });
        if (alive) { setData(r); setError(null); }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void load();
    const t = setInterval(load, 15_000);
    return () => { alive = false; clearInterval(t); };
  }, [windowHours]);

  return (
    <Subsection title="Per-symbol coverage">
      <div style={{
        display: "flex", gap: 10, alignItems: "center", marginBottom: 8, fontSize: 10,
      }}>
        <span style={{ color: "var(--text-muted)" }}>Window:</span>
        {[1, 6, 24, 72].map((h) => (
          <button
            key={h}
            onClick={() => setWindowHours(h)}
            style={{
              padding: "2px 8px", fontSize: 10, fontWeight: 600,
              border: `1px solid ${windowHours === h ? "#1fc16b" : "var(--border)"}`,
              borderRadius: 3,
              background: windowHours === h ? "rgba(31,193,107,0.12)" : "transparent",
              color: windowHours === h ? "#1fc16b" : "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            {h}h
          </button>
        ))}
      </div>

      {error && <ErrorText>{error}</ErrorText>}
      {!data && !error && <Muted>Loading…</Muted>}
      {data && data.per_symbol_aggregates.length === 0 && !error && (
        <div style={{
          padding: "10px 14px", border: "1px dashed var(--border)",
          borderRadius: 4, fontSize: 11, color: "var(--text-dim)",
          lineHeight: 1.55,
        }}>
          No snapshots in the last {windowHours}h. If the harvester is{" "}
          <code>HEALTHY</code> but this is empty, the universe may be
          mis-configured. If the harvester is <code>DISABLED</code>,
          flip the config flag first.
        </div>
      )}
      {data && data.per_symbol_aggregates.length > 0 && (
        <>
          <div style={{
            display: "grid",
            gridTemplateColumns: "100px 70px 70px 70px 80px 80px 80px 150px",
            gap: 8, alignItems: "center",
            paddingBottom: 4, marginBottom: 4,
            borderBottom: "1px solid var(--border)",
            fontSize: 9, color: "var(--text-muted)",
            textTransform: "uppercase", letterSpacing: "0.05em",
          }}>
            <span>Symbol</span>
            <span style={{ textAlign: "right" }}>Polls</span>
            <span style={{ textAlign: "right" }}>Quotes</span>
            <span style={{ textAlign: "right" }}>Fill %</span>
            <span style={{ textAlign: "right" }}>Avg bps</span>
            <span style={{ textAlign: "right" }}>Min bps</span>
            <span style={{ textAlign: "right" }}>Max bps</span>
            <span>Last seen</span>
          </div>
          {data.per_symbol_aggregates.map((row) => {
            const fillPct = row.n_polls > 0 ? (row.n_quotes / row.n_polls) * 100 : 0;
            const fillColor = fillPct >= 90 ? "#16a34a" : fillPct >= 50 ? "#ca8a04" : "#dc2626";
            return (
              <div
                key={row.symbol}
                style={{
                  display: "grid",
                  gridTemplateColumns: "100px 70px 70px 70px 80px 80px 80px 150px",
                  gap: 8, alignItems: "center",
                  padding: "5px 0",
                  borderTop: "1px solid var(--border)",
                  fontSize: 11, fontFamily: "monospace",
                }}
              >
                <span style={{ fontWeight: 600 }}>{row.symbol}</span>
                <span style={{ textAlign: "right" }}>{row.n_polls}</span>
                <span style={{ textAlign: "right" }}>{row.n_quotes}</span>
                <span style={{ textAlign: "right", color: fillColor, fontWeight: 600 }}>
                  {fillPct.toFixed(0)}%
                </span>
                <span style={{ textAlign: "right" }}>{fmtBps(row.avg_spread_bps)}</span>
                <span style={{ textAlign: "right" }}>{fmtBps(row.min_spread_bps)}</span>
                <span style={{ textAlign: "right" }}>{fmtBps(row.max_spread_bps)}</span>
                <span style={{ color: "var(--text-dim)", fontSize: 10 }}>
                  {row.last_seen_utc ? new Date(row.last_seen_utc).toLocaleString() : "—"}
                </span>
              </div>
            );
          })}
        </>
      )}
    </Subsection>
  );
}

// ─── Recent snapshot feed ──────────────────────────────────────────

function RecentFeed() {
  const [data, setData] = useState<Recent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await api.igSnapshotsRecent({ sinceHours: 24, limit: 50 });
        if (alive) { setData(r); setError(null); }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void load();
    const t = setInterval(load, 15_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (error) return null;  // already shown above
  if (!data || data.recent_snapshots.length === 0) return null;

  return (
    <Subsection title={`Recent snapshots (last ${data.recent_snapshots.length})`}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "150px 90px 80px 80px 80px 80px 90px 100px",
        gap: 6, alignItems: "center",
        paddingBottom: 4, marginBottom: 4,
        borderBottom: "1px solid var(--border)",
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.05em",
      }}>
        <span>Captured</span>
        <span>Symbol</span>
        <span style={{ textAlign: "right" }}>Bid</span>
        <span style={{ textAlign: "right" }}>Ask</span>
        <span style={{ textAlign: "right" }}>Mid</span>
        <span style={{ textAlign: "right" }}>Bps</span>
        <span>Status</span>
        <span>Error</span>
      </div>
      {data.recent_snapshots.map((row) => {
        const noQuote = row.bid === null || row.ask === null;
        return (
          <div
            key={row.id}
            style={{
              display: "grid",
              gridTemplateColumns: "150px 90px 80px 80px 80px 80px 90px 100px",
              gap: 6, alignItems: "center",
              padding: "4px 0",
              borderTop: "1px solid var(--border)",
              fontSize: 10, fontFamily: "monospace",
              background: noQuote ? "rgba(220, 38, 38, 0.06)" : undefined,
            }}
          >
            <span style={{ color: "var(--text-dim)" }}>
              {new Date(row.captured_at_utc).toLocaleString()}
            </span>
            <span style={{ fontWeight: 600 }}>{row.symbol}</span>
            <span style={{ textAlign: "right" }}>{fmtPrice(row.bid)}</span>
            <span style={{ textAlign: "right" }}>{fmtPrice(row.ask)}</span>
            <span style={{ textAlign: "right" }}>{fmtPrice(row.mid)}</span>
            <span style={{ textAlign: "right" }}>{fmtBps(row.spread_bps)}</span>
            <span style={{ color: "var(--text-dim)" }}>{row.market_status ?? "—"}</span>
            <span style={{ color: "#dc2626" }}>{row.error ?? ""}</span>
          </div>
        );
      })}
    </Subsection>
  );
}

// ─── Helpers ───────────────────────────────────────────────────────

function fmtBps(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(2);
}
function fmtPrice(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(5);
}

// Self-contained layout primitives (parallel to other settings panels).
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{
      border: "1px solid var(--border)", borderRadius: 8,
      padding: 14, marginBottom: 14,
      background: "var(--surface-1, rgba(255,255,255,0.02))",
    }}>
      <h3 style={{
        margin: "0 0 10px", fontSize: 13, fontWeight: 700,
        letterSpacing: "0.04em", textTransform: "uppercase",
        color: "var(--text-dim)",
      }}>{title}</h3>
      {children}
    </section>
  );
}
function Subsection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <h4 style={{
        margin: "0 0 6px", fontSize: 11, fontWeight: 700,
        color: "var(--text)", letterSpacing: "0.03em",
      }}>{title}</h4>
      {children}
    </div>
  );
}
function Muted({ children }: { children: React.ReactNode }) {
  return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>{children}</div>;
}
function ErrorText({ children }: { children: React.ReactNode }) {
  return <div style={{ color: "var(--down)", fontSize: 12, marginTop: 4 }}>{children}</div>;
}
