/**
 * StrategyReadinessPanel — surfaces the ASSUMPTIONS behind each strategy so
 * the silent-zero / data-starvation class of bug (FX, 2026-06-01) is VISIBLE
 * and DECIDABLE *before* testing, not discovered mid-run.
 *
 * The trap that motivated this: ichimoku_fx_mr's `warmup_bars` GATE (800)
 * was far below the bars its longest Ichimoku horizon actually needs
 * (624h ⇒ ~2578) — so it passed the gate but the ensemble collapsed to 0
 * and it never traded, silently. This panel computes bars-needed straight
 * from the catalog params and flags any strategy whose warmup gate is below
 * it (⚠ = would under-compute).
 *
 * All derived from existing endpoints (catalog params + broker map +
 * strategyMeta) — no new backend. "Bars available" is runtime-only so it's
 * not shown here; the strategy now logs DATA-STARVED loudly for that.
 */
import { useEffect, useState } from "react";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";
import { executionMode, metaFor, ownerFor } from "../../util/strategyMeta";
import type { LatestSession } from "../../types/cockpit";

const AMBER = "#f5a623";
const UP = "#1fc16b";

type CatalogStrategy = {
  name: string;
  default_params: Record<string, unknown>;
};

/** Bars the strategy's longest lookback structure needs to compute, derived
 * from its catalog params. Returns null when the shape isn't recognised. */
function barsNeeded(params: Record<string, unknown>): { need: number; why: string } | null {
  const num = (v: unknown): number | null =>
    typeof v === "number" && isFinite(v) ? v : null;
  const arr = (v: unknown): number[] =>
    Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : [];

  // FX mean-reversion ensemble: senkou_b = 4×horizon, + smooth + buffer.
  const horizons = arr(params.horizons);
  const smooths = arr(params.smooths);
  if (horizons.length) {
    const need = Math.max(...horizons) * 4 + (smooths.length ? Math.max(...smooths) : 0) + 10;
    return { need, why: `${Math.max(...horizons)}h horizon × 4 (senkou_b) + smooth` };
  }
  // Daily Ichimoku: max(tenkan,kijun,senkou_b) + displacement + 1.
  const t = num(params.tenkan), k = num(params.kijun), sb = num(params.senkou_b);
  const disp = num(params.displacement) ?? num(params.disp);
  if (t != null && k != null && sb != null) {
    const need = Math.max(t, k, sb) + (disp ?? 0) + 1;
    return { need, why: `max(tenkan,kijun,senkou_b)+displacement` };
  }
  return null;
}

export function StrategyReadinessPanel(
  { onHide, sessions }: { onHide: () => void; sessions: LatestSession[] },
) {
  const [rows, setRows] = useState<CatalogStrategy[]>([]);
  const [brokerBy, setBrokerBy] = useState<Record<string, string>>({});

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [cat, bm] = await Promise.all([api.paperStrategies(), api.strategyBrokerMap()]);
        if (!live) return;
        setRows(cat.strategies.map((s) => ({ name: s.name, default_params: s.default_params })));
        const map: Record<string, string> = {};
        for (const m of bm.mappings) map[m.strategy_id] = m.broker;
        setBrokerBy(map);
      } catch { /* best-effort */ }
    })();
    return () => { live = false; };
  }, []);

  // Empirical evidence beats theory: a desk that actually ran today and
  // computed bars + emitted decisions is demonstrably fed, whatever the
  // catalog gate says. Map strategy → today's live run (snapshot-backed).
  const today = new Date().toISOString().slice(0, 10);
  const liveByName: Record<string, LatestSession> = {};
  for (const s of sessions) {
    if (s.completedAtUtc && s.completedAtUtc.slice(0, 10) === today
      && (s.barsSeen > 0 || s.decisions.length > 0)) {
      liveByName[s.strategy] = s;
    }
  }

  // Only the plugged / known strategies are worth surfacing; skip aliases.
  const items = rows
    .filter((s) => metaFor(s.name))
    .map((s) => {
      const meta = metaFor(s.name)!;
      const broker = brokerBy[s.name] ?? null;
      const mode = executionMode(broker);
      const need = barsNeeded(s.default_params);
      const warmup = typeof s.default_params.warmup_bars === "number"
        ? (s.default_params.warmup_bars as number) : null;
      const live = liveByName[s.name] ?? null;
      // Theoretical gap: the catalog warmup gate is below what the longest
      // horizon needs. NOTE this is the *catalog default* — the live daemon
      // can (and does) override it with --warmup-bars / --lookback-days, so
      // a gap here is only a RISK, never proof of starvation.
      const gateGap = need != null && warmup != null && warmup < need.need;
      // Verdict, evidence-first:
      //   live   → ran today & computed bars/decisions → it IS fed. ✓
      //   risk   → no live run to confirm, and the gate alone wouldn't
      //            guarantee the bars → amber, worth checking before trading.
      //   ok     → gate covers the need (or no long-horizon shape).
      const status: "live" | "risk" | "ok" | "unknown" =
        live ? "live" : need == null ? "unknown" : gateGap ? "risk" : "ok";
      const universe = (Array.isArray(s.default_params.pairs) && (s.default_params.pairs as unknown[]).length)
        || (Array.isArray(s.default_params.symbols) && (s.default_params.symbols as unknown[]).length)
        || null;
      return { name: s.name, meta, broker, mode, need, warmup, gateGap, status, live, universe };
    });

  if (items.length === 0) return null;
  // The badge counts only UNCONFIRMED risks — a live desk is never a "risk"
  // even if its catalog gate looks low, which is the whole point of the fix.
  const risks = items.filter((i) => i.status === "risk").length;

  return (
    <CockpitCard id="strategy-readiness" title="Strategy readiness" badge={risks || undefined} fullWidth onHide={onHide}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
        Evidence-first readiness. <span style={{ color: UP }}>✓ live</span> = the
        desk actually ran today and computed bars — it's fed, whatever the
        catalog gate says. <span style={{ color: AMBER }}>⚠ gate&nbsp;&lt;&nbsp;need</span> =
        no confirmed run yet <em>and</em> the catalog warmup gate alone is below
        the bars the longest horizon needs — worth checking before relying on it.
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 640 }}>
          <thead>
            <tr style={{ color: "var(--text-dim)", textAlign: "left" }}>
              <th style={th}>Strategy</th><th style={th}>Owner</th><th style={th}>Exec</th>
              <th style={th}>Broker</th><th style={rTh}>Universe</th>
              <th style={rTh}>Warmup gate</th><th style={rTh}>Bars needed</th><th style={th}>Readiness</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.name} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={td}><span style={{ fontWeight: 600 }}>{i.name}</span></td>
                <td style={{ ...td, color: "var(--text-muted)" }}>{i.meta && ownerFor(i.name)}</td>
                <td style={td}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: i.mode === "live" ? UP : "#4f8cff" }}>
                    {i.mode === "live" ? "LIVE" : "SIGNAL"}
                  </span>
                </td>
                <td style={{ ...td, color: "var(--text-dim)" }}>{i.broker ?? "—"}</td>
                <td style={numTd}>{i.universe ?? "—"}</td>
                <td style={numTd}>{i.warmup ?? "—"}</td>
                <td style={{ ...numTd, color: i.status === "risk" ? AMBER : "var(--text)" }}>
                  {i.need ? i.need.need : "—"}
                </td>
                <td style={td}>
                  {i.status === "live" ? (
                    <span style={{ color: UP, fontWeight: 700 }}
                      title={`Ran today (${(i.live!.completedAtUtc ?? "").replace("T", " ").slice(0, 19)} UTC) and computed ${i.live!.barsSeen} bars / ${i.live!.decisions.length} decisions. Empirically fed — catalog gate ${i.warmup ?? "?"} is overridden by the live daemon's --lookback-days.`}>
                      ✓ live
                    </span>
                  ) : i.status === "risk" ? (
                    <span style={{ color: AMBER, fontWeight: 700 }}
                      title={`No confirmed run today. Catalog warmup gate ${i.warmup} < ${i.need!.need} needed (${i.need!.why}). If the daemon's --lookback-days supplies ${i.need!.need}+ bars it's fine — but the gate alone wouldn't guarantee it.`}>
                      ⚠ gate &lt; need
                    </span>
                  ) : i.status === "ok" ? (
                    <span style={{ color: UP, fontWeight: 700 }}>✓ ok</span>
                  ) : (
                    <span style={{ color: "var(--text-muted)" }}>—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        Warmup gate / bars-needed are <em>catalog defaults</em>; the live daemon overrides them with
        <code style={{ color: "var(--text-dim)" }}> --warmup-bars</code> / <code style={{ color: "var(--text-dim)" }}>--lookback-days</code>.
        That's why a <span style={{ color: UP }}>✓ live</span> desk is healthy even when its catalog gate looks low —
        the run itself is the proof. Bars-available is also logged at runtime (DATA-STARVED warning).
      </div>
    </CockpitCard>
  );
}

const th: React.CSSProperties = { padding: "4px 8px", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em" };
const rTh: React.CSSProperties = { ...th, textAlign: "right" };
const td: React.CSSProperties = { padding: "4px 8px" };
const numTd: React.CSSProperties = { ...td, textAlign: "right", fontFamily: "monospace" };
