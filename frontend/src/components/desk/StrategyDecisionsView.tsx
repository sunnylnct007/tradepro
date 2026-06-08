/**
 * StrategyDecisionsView — the per-run DECISION LOG for each strategy, so you can
 * see WHY it traded or stayed flat ("is it making the right call?"), not just
 * that it's alive. Reads the latest paper-snapshot per strategy (the daemon
 * attaches its on_bar decisions) and shows the action breakdown + every symbol's
 * call + reason.
 *
 * This is what answers "no movement on intraday/fx": e.g. intraday_flat showing
 * 11× "scanner-drop-no-signal :: daily Ichimoku signal flat — price not above
 * cloud" = correctly selective, no qualifying long today.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

const SEP = "#1b2233";
const KNOWN = ["ichimoku_equity", "ichimoku_fx_mr", "intraday_flat"];
const LABEL: Record<string, string> = {
  ichimoku_equity: "Ichimoku Equity (T212)",
  ichimoku_fx_mr: "Ichimoku FX MR (IG)",
  intraday_flat: "Intraday Flat (IG 24h)",
};

type Decision = { symbol: string; action: string; reason: string };
type Frame = { asOf: string | null; decisions: Decision[] };

function actionColour(a: string): string {
  if (a.startsWith("fire-")) return "#3fb950";
  if (a.startsWith("alert-")) return "#d29922";
  if (a.startsWith("hold") || a.startsWith("restored")) return "#4f8cff";
  return "var(--text-muted)"; // skip-* / *-no-signal / drop
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

export function StrategyDecisionsView() {
  const [data, setData] = useState<Record<string, Frame>>({});
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const snaps = await api.paperSnapshots();
        const byStrat: Record<string, { label: string; recv: string }> = {};
        for (const s of snaps) {
          const sid = KNOWN.find((k) => s.sessionLabel.startsWith(k + "-"));
          if (!sid) continue;
          if (!byStrat[sid] || s.receivedAtUtc > byStrat[sid].recv) {
            byStrat[sid] = { label: s.sessionLabel, recv: s.receivedAtUtc };
          }
        }
        const out: Record<string, Frame> = {};
        await Promise.all(
          KNOWN.filter((k) => byStrat[k]).map(async (k) => {
            try {
              const snap = (await api.paperSnapshot(byStrat[k].label)) as Record<string, unknown>;
              const frames = (snap.strategies as Array<Record<string, unknown>>) ?? [];
              const frame = frames[0] ?? {};
              out[k] = {
                asOf: (snap.as_of_utc as string) ?? null,
                decisions: (frame.decisions as Decision[]) ?? [],
              };
            } catch { /* per-strategy best-effort */ }
          }),
        );
        if (!cancel) { setData(out); setErr(null); }
      } catch (e) {
        if (!cancel) setErr(e instanceof Error ? e.message : "failed to load");
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>Strategy decision log</h2>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          Every symbol's call on the latest run + why — so you can see if a strategy is making the
          right decision (e.g. "no qualifying long today"), not just whether it's alive.
        </div>
      </div>
      {loading && <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading decisions…</div>}
      {err && <div style={{ fontSize: 11, color: "#f85149" }}>Decisions unavailable: {err}</div>}
      {KNOWN.filter((k) => data[k]).map((k) => (
        <StrategyDecisionCard key={k} label={LABEL[k] ?? k} frame={data[k]} />
      ))}
      {!loading && !err && KNOWN.every((k) => !data[k]) && (
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>No snapshots posted yet.</div>
      )}
    </div>
  );
}

function StrategyDecisionCard({ label, frame }: { label: string; frame: Frame }) {
  const [showAll, setShowAll] = useState(false);
  // Split the synthetic _session summary row out from per-symbol decisions.
  const summary = frame.decisions.find((d) => d.symbol === "_session");
  const perSymbol = frame.decisions.filter((d) => d.symbol !== "_session");
  const counts = perSymbol.reduce<Record<string, number>>((acc, d) => {
    acc[d.action] = (acc[d.action] ?? 0) + 1;
    return acc;
  }, {});
  const fired = perSymbol.filter((d) => d.action.startsWith("fire-")).length;
  const shown = showAll ? perSymbol : perSymbol.slice(0, 12);

  return (
    <div style={{ border: `1px solid ${SEP}`, borderRadius: 8, background: "rgba(255,255,255,0.02)", padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 12.5 }}>{label}</span>
        <span style={{ fontSize: 10.5, color: fired ? "#3fb950" : "var(--text-muted)" }}>
          {fired ? `${fired} fired` : "no trades this run"}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}>
          {perSymbol.length} symbols · {ago(frame.asOf)}
        </span>
      </div>
      {summary && (
        <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>{summary.reason}</div>
      )}
      {/* Action breakdown chips */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "7px 0" }}>
        {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([action, n]) => (
          <span key={action} style={{
            fontSize: 10, padding: "1px 6px", borderRadius: 10,
            border: `1px solid ${SEP}`, color: actionColour(action),
          }}>
            {action} <strong>{n}</strong>
          </span>
        ))}
      </div>
      {/* Per-symbol decisions */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {shown.map((d, i) => (
          <div key={`${d.symbol}-${i}`} style={{
            display: "grid", gridTemplateColumns: "70px 150px 1fr", gap: 8,
            fontSize: 10.5, padding: "2px 0", borderTop: i ? `1px solid ${SEP}` : undefined,
          }}>
            <span style={{ fontWeight: 600 }}>{d.symbol}</span>
            <span style={{ color: actionColour(d.action) }}>{d.action}</span>
            <span style={{ color: "var(--text-muted)" }}>{d.reason}</span>
          </div>
        ))}
      </div>
      {perSymbol.length > 12 && (
        <button onClick={() => setShowAll((s) => !s)} style={{
          marginTop: 6, fontSize: 10, background: "none", border: `1px solid ${SEP}`,
          borderRadius: 6, color: "var(--text-muted)", padding: "2px 8px", cursor: "pointer",
        }}>
          {showAll ? "show less" : `show all ${perSymbol.length}`}
        </button>
      )}
    </div>
  );
}
