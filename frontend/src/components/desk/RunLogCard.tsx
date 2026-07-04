/**
 * RunLogCard — the CENTRAL run-log surfaced on the cockpit. Every process on every
 * machine (Mac daemons, harvest, audits, the API) writes a row; this shows the recent
 * slice with FAILURES/partials highlighted, so an operational issue on any machine is
 * loud in one place instead of buried in per-machine /tmp logs
 * (feedback_central_observability_fail_loud). A 24h health rollup badges the header.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

type Row = Awaited<ReturnType<typeof api.runLog>>["rows"][number];
type State = "loading" | "ok" | "error";

const STATUS_COLOR: Record<string, string> = {
  ok: "#3fb950", fail: "#f85149", partial: "#d29922", stale: "#d29922", warn: "#d29922",
};

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function RunLogCard() {
  const [state, setState] = useState<State>("loading");
  const [rows, setRows] = useState<Row[]>([]);
  const [health, setHealth] = useState<Record<string, number>>({});
  const [procs, setProcs] = useState<Awaited<ReturnType<typeof api.runLog>>["processes"]>([]);
  const [failOnly, setFailOnly] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.runLog(60, failOnly ? "fail" : undefined);
      setRows(d.rows); setHealth(d.health24h ?? {}); setProcs(d.processes ?? []); setState("ok"); setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e)); setState("error");
    }
  }, [failOnly]);
  useEffect(() => { void load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  const fails = health.fail ?? 0;
  const partials = (health.partial ?? 0) + (health.warn ?? 0) + (health.stale ?? 0);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: "1px solid #1b2233" }}>
        <span style={{ fontWeight: 600 }}>Run log — all processes</span>
        <span style={{ fontSize: 9.5, color: fails ? "#f85149" : "#3fb950" }}>
          24h: {health.ok ?? 0} ok{fails ? ` · ${fails} FAIL` : ""}{partials ? ` · ${partials} warn` : ""}
        </span>
        <button onClick={() => setFailOnly((v) => !v)}
          style={{ marginLeft: "auto", fontSize: 9, padding: "1px 7px", borderRadius: 5, cursor: "pointer",
            border: "1px solid #1b2233", background: failOnly ? "rgba(248,81,73,0.15)" : "rgba(255,255,255,0.03)",
            color: failOnly ? "#f85149" : "var(--text-dim)" }}>
          {failOnly ? "showing fails" : "fails only"}
        </button>
        <button onClick={() => void load()} title="refresh"
          style={{ fontSize: 10, padding: "1px 7px", borderRadius: 5, border: "1px solid #1b2233", background: "rgba(255,255,255,0.03)", color: "var(--text-dim)", cursor: "pointer" }}>↻</button>
      </div>

      {procs.some((p) => p.stale) && (
        <div style={{ padding: "5px 10px", fontSize: 10.5, color: "#f85149", background: "rgba(248,81,73,0.08)", borderBottom: "1px solid #5a2222" }}>
          ⚠ <b>Dead / stale process{procs.filter((p) => p.stale).length > 1 ? "es" : ""}:</b>{" "}
          {procs.filter((p) => p.stale).map((p) => `${p.process} (${p.lastRunUtc ? `${Math.round(p.ageHours ?? 0)}h ago` : "never run"})`).join(", ")}
          {" "}— expected to run on schedule but hasn't. (This is the check that would've caught the 5-week-dead decision uploader.)
        </div>
      )}
      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Loading…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Run-log unavailable: {err}</div>}
      {state === "ok" && rows.length === 0 && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>No runs recorded yet.</div>}

      {state === "ok" && rows.length > 0 && (
        <div style={{ maxHeight: 320, overflow: "auto" }}>
          {rows.map((r) => (
            <div key={r.id} style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, padding: "3px 10px", borderTop: "1px solid #11161f", alignItems: "baseline", fontSize: 10.5 }}>
              <span style={{ fontWeight: 700, color: STATUS_COLOR[r.status] ?? "var(--text)", minWidth: 46, fontSize: 9.5, textTransform: "uppercase" }}>{r.status}</span>
              <span style={{ color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                <b style={{ color: "var(--text)", fontFamily: "monospace", fontSize: 10 }}>{r.process}</b>
                {r.broker ? <span style={{ color: "var(--text-muted)" }}> ·{r.broker}</span> : null}
                {r.symbol ? <span style={{ color: "var(--text-muted)" }}> {r.symbol}</span> : null}
                {" "}
                {r.error ? <span style={{ color: "#f85149" }}>{r.error}</span> : <span>{r.summary}</span>}
              </span>
              <span style={{ color: "var(--text-muted)", fontSize: 9 }} title={r.created_at_utc}>{ago(r.created_at_utc)}</span>
            </div>
          ))}
        </div>
      )}
      <div style={{ padding: "4px 10px 7px", fontSize: 9, color: "var(--text-muted)" }}>
        Every process/machine writes here — a silent failure (stale container, dead secret, swallowed fetch) shows up loud. Fail rows carry the reason.
      </div>
    </div>
  );
}
