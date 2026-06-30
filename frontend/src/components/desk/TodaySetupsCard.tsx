/**
 * TodaySetupsCard — the dashboard scanner: "what's worth a look today?"
 *
 * The curated, risk-aware replacement for the original flat BUY-light board.
 * Scans BOTH universes — large_50 (core, stable) and high_beta (volatile) —
 * tags each setup with its universe + ATR so the risk is visible, and surfaces
 * the few worth considering by ENTRY QUALITY:
 *   ⭐ CONSIDER  — LONG + pulled back near the kijun (good risk-entry)
 *   ⚠  EXTENDED  — LONG but stretched (98th-pctile) → wait for a pullback
 * It never emits a confident BUY — "LONG + here's the risk; you decide".
 * High-beta setups carry far higher ATR (size down, wider stop) — flagged amber.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

const UNIVERSES: Array<{ key: string; tag: string; label: string }> = [
  { key: "large_50", tag: "core", label: "L50" },
  { key: "high_beta", tag: "high-β", label: "HB" },
];
const HIGH_ATR = 6; // %/day above which we flag a setup as a wild ride

type Setup = Awaited<ReturnType<typeof api.todaySetups>>["artifact"]["setups"][number] & { universe: string };
type State = "loading" | "ok" | "nodata" | "error";

const TAG: Record<string, { dot: string; color: string }> = {
  consider: { dot: "⭐", color: "#3fb950" },
  extended: { dot: "⚠", color: "#d29922" },
  hold: { dot: "·", color: "var(--text-muted)" },
};

export function TodaySetupsCard() {
  const [state, setState] = useState<State>("loading");
  const [setups, setSetups] = useState<Setup[]>([]);
  const [counts, setCounts] = useState<{ consider: number; extended: number; excluded: number }>({ consider: 0, extended: 0, excluded: 0 });
  const [asOf, setAsOf] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    const results = await Promise.allSettled(UNIVERSES.map((u) => api.todaySetups(u.key)));
    const merged: Setup[] = [];
    const c = { consider: 0, extended: 0, excluded: 0 };
    let anyOk = false, latest: string | null = null;
    results.forEach((r, i) => {
      if (r.status !== "fulfilled") return;
      anyOk = true;
      const a = r.value.artifact;
      if (!latest || (a.as_of_utc ?? "") > latest) latest = a.as_of_utc;
      c.consider += a.counts.consider; c.extended += a.counts.extended; c.excluded += a.counts.excluded;
      a.setups.forEach((s) => merged.push({ ...s, universe: UNIVERSES[i].key }));
    });
    if (!anyOk) {
      const first = results[0];
      const msg = first.status === "rejected" ? String(first.reason) : "";
      if (msg.includes("404")) setState("nodata"); else { setErr(msg); setState("error"); }
      return;
    }
    // Rank: consider → extended → hold; core (large_50) before high_beta within a tier;
    // then closest-to-kijun first.
    const order: Record<string, number> = { consider: 0, extended: 1, hold: 2 };
    merged.sort((a, b) =>
      (order[a.classification] - order[b.classification]) ||
      ((a.universe === "large_50" ? 0 : 1) - (b.universe === "large_50" ? 0 : 1)) ||
      ((a.dist_atr ?? 99) - (b.dist_atr ?? 99)));
    setSetups(merged); setCounts(c); setAsOf(latest); setState("ok"); setErr(null);
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 300_000);
    return () => clearInterval(t);
  }, [load]);

  const shown = (showAll ? setups : setups.filter((s) => s.classification === "consider")).slice(0, showAll ? 30 : 12);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>Today's Setups — large_50 + high-β</span>
        {state === "ok" && <span style={{ fontSize: 9.5, color: "var(--text-muted)" }}>⭐{counts.consider} · ⚠{counts.extended} · {counts.excluded} excluded</span>}
        {state === "ok" && asOf && <span style={{ marginLeft: "auto", fontSize: 9.5, color: "var(--text-muted)" }}>as of {asOf.slice(0, 10)}</span>}
      </div>

      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Scanning…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Scan unavailable: {err}</div>}
      {state === "nodata" && (
        <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          No scan pushed yet — run <code style={{ color: "var(--text-dim)" }}>tradepro-today-setups --push</code> on the worker.
        </div>
      )}

      {state === "ok" && (
        <div style={{ padding: "6px 6px 8px" }}>
          {shown.length === 0 && (
            <div style={{ padding: "6px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
              No ⭐ "consider" setups today. {counts.extended > 0 && `${counts.extended} extended — `}
              <span onClick={() => setShowAll(true)} style={{ cursor: "pointer", color: "var(--accent, #4f8cff)" }}>show all</span>
            </div>
          )}
          {shown.map((s) => {
            const t = TAG[s.classification] ?? TAG.hold;
            const hb = s.universe !== "large_50";
            const hotAtr = (s.atr_pct ?? 0) >= HIGH_ATR;
            return (
              <div key={`${s.universe}:${s.symbol}`} style={{ display: "grid", gridTemplateColumns: "auto 58px 50px 1fr", gap: 7, alignItems: "baseline", padding: "4px 6px", borderTop: "1px solid #11161f" }}>
                <span style={{ color: t.color, fontWeight: 700, fontSize: 12, whiteSpace: "nowrap" }}>
                  {t.dot} {s.symbol}
                  <span style={{ fontSize: 8, marginLeft: 4, padding: "0 3px", borderRadius: 3, color: hb ? "#d29922" : "var(--text-muted)", border: `1px solid ${hb ? "#5a4a1a" : "#1b2233"}` }}>{hb ? "HB" : "L50"}</span>
                </span>
                <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text-dim)" }}>${s.close.toFixed(2)}</span>
                <span style={{ fontSize: 10, color: hotAtr ? "#d29922" : "var(--text-muted)", fontWeight: hotAtr ? 700 : 400 }} title="ATR %/day — high = size down, wider stop">
                  {s.atr_pct == null ? "—" : `${s.atr_pct.toFixed(1)}%`}
                </span>
                <span style={{ fontSize: 10, color: "var(--text-dim)", lineHeight: 1.4 }}>{s.why}</span>
              </div>
            );
          })}
          <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", padding: "0 6px", display: "flex", justifyContent: "space-between" }}>
            <span>
              {showAll ? "consider + extended" : "⭐ consider only"} ·
              <span onClick={() => setShowAll((v) => !v)} style={{ cursor: "pointer", color: "var(--accent, #4f8cff)" }}> {showAll ? "consider only" : "show extended"}</span>
              {" · "}<span style={{ color: "#d29922" }}>HB / amber ATR = higher risk, size down</span>
            </span>
            <span title="signal — not advice; systematic signal, discretionary entry">you decide entry ⓘ</span>
          </div>
        </div>
      )}
    </div>
  );
}
