/**
 * TodaySetupsCard — the dashboard scanner: "what's worth a look today?"
 *
 * The curated, risk-aware replacement for the original flat BUY-light board.
 * Reads the tradepro-today-setups artifact (universe ranked by ENTRY QUALITY)
 * and surfaces the few worth considering, each with the WHY + risk context:
 *   ⭐ CONSIDER  — LONG + pulled back near the kijun (good risk-entry)
 *   ⚠  EXTENDED  — LONG but stretched (98th-pctile) → wait for a pullback
 * It never emits a confident BUY — "LONG + here's the risk; you decide".
 * Excluded (no long signal) names are summarised, not shown.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

const UNIVERSE = "large_50";

type Artifact = Awaited<ReturnType<typeof api.todaySetups>>;
type State = "loading" | "ok" | "nodata" | "error";

const TAG: Record<string, { dot: string; color: string }> = {
  consider: { dot: "⭐", color: "#3fb950" },
  extended: { dot: "⚠", color: "#d29922" },
  hold: { dot: "·", color: "var(--text-muted)" },
};

export function TodaySetupsCard() {
  const [state, setState] = useState<State>("loading");
  const [data, setData] = useState<Artifact | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.todaySetups(UNIVERSE);
      setData(d); setState("ok"); setErr(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("404")) setState("nodata");
      else { setErr(msg); setState("error"); }
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 300_000);
    return () => clearInterval(t);
  }, [load]);

  const a = data?.artifact;
  const setups = a?.setups ?? [];
  const shown = showAll ? setups : setups.filter((s) => s.classification === "consider");

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>Today's Setups — {UNIVERSE}</span>
        {state === "ok" && a && (
          <span style={{ fontSize: 9.5, color: "var(--text-muted)" }}>
            ⭐{a.counts.consider} · ⚠{a.counts.extended} · {a.counts.excluded} excluded
          </span>
        )}
        {state === "ok" && a && (
          <span style={{ marginLeft: "auto", fontSize: 9.5, color: "var(--text-muted)" }}>as of {a.as_of_utc?.slice(0, 10)}</span>
        )}
      </div>

      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Scanning…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Scan unavailable: {err}</div>}
      {state === "nodata" && (
        <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No scan pushed yet — run <code style={{ color: "var(--text-dim)" }}>tradepro-today-setups --push</code> on the worker.
        </div>
      )}

      {state === "ok" && a && (
        <div style={{ padding: "6px 6px 8px" }}>
          {shown.length === 0 && (
            <div style={{ padding: "6px 6px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
              No ⭐ "consider" setups today — nothing pulled back to support. {a.counts.extended > 0 && `${a.counts.extended} extended (wait for pullback) — `}
              <span onClick={() => setShowAll(true)} style={{ cursor: "pointer", color: "var(--accent, #4f8cff)" }}>show all</span>
            </div>
          )}
          {shown.map((s) => {
            const t = TAG[s.classification] ?? TAG.hold;
            return (
              <div key={s.symbol} style={{ display: "grid", gridTemplateColumns: "auto 64px 1fr", gap: 8, alignItems: "baseline", padding: "4px 6px", borderTop: "1px solid #11161f" }}>
                <span style={{ color: t.color, fontWeight: 700, fontSize: 12, whiteSpace: "nowrap" }}>{t.dot} {s.symbol}</span>
                <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-dim)" }}>${s.close.toFixed(2)}</span>
                <span style={{ fontSize: 10, color: "var(--text-dim)", lineHeight: 1.4 }}>{s.why}</span>
              </div>
            );
          })}
          <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", padding: "0 6px", display: "flex", justifyContent: "space-between" }}>
            <span>{showAll ? "showing consider + extended" : "showing ⭐ consider only"} ·
              <span onClick={() => setShowAll((v) => !v)} style={{ cursor: "pointer", color: "var(--accent, #4f8cff)" }}> {showAll ? "consider only" : "show extended too"}</span>
            </span>
            <span title={a.note}>signal — not advice; you decide entry ⓘ</span>
          </div>
        </div>
      )}
    </div>
  );
}
