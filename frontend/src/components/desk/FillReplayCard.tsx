/**
 * FillReplayCard — what the ACTUAL fills tell us about entry quality.
 *
 * Renders the artifact from `tradepro-fill-replay`: per-universe entry-extension
 * vs outcome, win-rate, and the one-shot-vs-staggered counterfactual — computed
 * from real OMS fills, not a backtest. This is the surface that lets us SEE the
 * over-extension refutation (corr ≈ 0; staggering doesn't help) instead of
 * trusting a narrative. Track-B research foundation: make the measurement
 * loop visible.
 *
 * Read-only: the artifact is produced on the Mac (cache access) and pushed to
 * /api/ingest/fill-replay. "No replay yet" → run the CLI with --push.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

const STRATEGY_ID = "ichimoku_equity_ibkr";

type Artifact = Awaited<ReturnType<typeof api.fillReplay>>;
type State = "loading" | "ok" | "nodata" | "error";

export function FillReplayCard() {
  const [state, setState] = useState<State>("loading");
  const [data, setData] = useState<Artifact | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.fillReplay(STRATEGY_ID);
      setData(d);
      setState("ok");
      setErr(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("404")) {
        setState("nodata");
      } else {
        setErr(msg);
        setState("error");
      }
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 300_000); // 5 min — it's a daily artifact
    return () => clearInterval(t);
  }, [load]);

  const universes = data ? Object.entries(data.artifact.universes) : [];

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>ICH equity — fill replay (entry quality)</span>
        <span
          title="What is this? Click to explain"
          onClick={() => setShowHelp((h) => !h)}
          style={{ cursor: "pointer", color: "var(--text-muted)", border: "1px solid #1b2233", borderRadius: 9, width: 15, height: 15, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 9 }}
        >
          ?
        </span>
        {state === "ok" && data && (
          <span style={{ marginLeft: "auto", fontSize: 9.5, color: "var(--text-muted)" }}>
            as of {data.artifact.as_of_utc?.slice(0, 10)}
          </span>
        )}
      </div>

      {showHelp && (
        <div style={{ padding: "6px 10px", fontSize: 10, color: "var(--text-muted)", borderBottom: "1px solid #1b2233", lineHeight: 1.5 }}>
          Replays the strategy's ACTUAL filled buys and measures where each entry landed
          (% over the 200-SMA) vs its outcome. <b>corr(ext,ret)</b> near 0 means extension
          does NOT explain wins/losses. <b>One-shot vs staggered</b> tests whether spreading
          entries over 10 days would have helped. This is what refuted the over-extension
          theory — the backtest models logic; this measures what we actually did.
        </div>
      )}

      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Loading replay…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Replay unavailable: {err}</div>}
      {state === "nodata" && (
        <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No replay pushed yet — run <code style={{ color: "var(--text-dim)" }}>tradepro-fill-replay --push</code> on the worker.
        </div>
      )}

      {state === "ok" && data && (
        <div style={{ padding: "8px 10px", display: "grid", gap: 8 }}>
          {universes.map(([name, u]) => {
            const extMatters = Math.abs(u.ext_return_corr) >= 0.3;
            return (
              <div key={name} style={{ borderTop: "1px solid #1b2233", paddingTop: 7 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
                  <span style={{ fontWeight: 700, fontSize: 12 }}>{name}</span>
                  <span style={{ fontSize: 9.5, color: "var(--text-muted)" }}>n={u.n} · {u.entry_dates.length === 1 ? `one-shot ${u.entry_dates[0]}` : `${u.entry_dates.length} entry dates`}</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 8 }}>
                  <Stat label="one-shot ret" v={fmtPct(u.one_shot_return_pct)} good={u.one_shot_return_pct >= 0} />
                  <Stat label="staggered ret" v={fmtPct(u.staggered_return_pct)} good={u.staggered_return_pct >= 0} />
                  <Stat label="win rate" v={`${u.win_rate_pct.toFixed(0)}%`} good={u.win_rate_pct >= 50} />
                  <Stat label="corr(ext,ret)" v={u.ext_return_corr >= 0 ? `+${u.ext_return_corr.toFixed(2)}` : u.ext_return_corr.toFixed(2)} good={extMatters ? u.ext_return_corr < 0 : undefined} />
                </div>
                <div style={{ marginTop: 5, fontSize: 10, color: "var(--text-dim)", lineHeight: 1.4 }}>
                  {extMatters
                    ? `extension is a real signal here (corr ${u.ext_return_corr.toFixed(2)}); losers entered ${fmtExt(u.ext_losers_avg)} vs winners ${fmtExt(u.ext_winners_avg)} over 200-SMA.`
                    : `extension does NOT explain outcomes (corr ${u.ext_return_corr.toFixed(2)}); losers ${fmtExt(u.ext_losers_avg)} vs winners ${fmtExt(u.ext_winners_avg)} over 200-SMA — about the same.`}
                  {" "}Staggering: {fmtPct(u.one_shot_return_pct)} → {fmtPct(u.staggered_return_pct)} ({Math.abs(u.one_shot_return_pct - u.staggered_return_pct) < 1 ? "no help" : "would differ"}).
                </div>
              </div>
            );
          })}
          {data.artifact.missing.length > 0 && (
            <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
              {data.artifact.missing.length} symbols skipped (no cache / &lt;200 bars) — partial coverage.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, v, good }: { label: string; v: string; good?: boolean }) {
  const color = good === undefined ? "var(--text-dim)" : good ? "#3fb950" : "#f85149";
  return (
    <div>
      <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color }}>{v}</div>
    </div>
  );
}

function fmtPct(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}
function fmtExt(n: number | null): string {
  return n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(0)}%`;
}
