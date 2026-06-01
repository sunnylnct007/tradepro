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
import { deskFor, executionMode, metaFor } from "../../util/strategyMeta";

const RED = "#ef4444";
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

export function StrategyReadinessPanel({ onHide }: { onHide: () => void }) {
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
      // ⚠ when the warmup gate is below what the ensemble needs — the
      // silent-zero trap (gate passes, long horizons under-compute).
      const starveRisk = need != null && warmup != null && warmup < need.need;
      const universe = (Array.isArray(s.default_params.pairs) && (s.default_params.pairs as unknown[]).length)
        || (Array.isArray(s.default_params.symbols) && (s.default_params.symbols as unknown[]).length)
        || null;
      return { name: s.name, meta, broker, mode, need, warmup, starveRisk, universe };
    });

  if (items.length === 0) return null;
  const risks = items.filter((i) => i.starveRisk).length;

  return (
    <CockpitCard id="strategy-readiness" title="Strategy readiness" badge={risks || undefined} fullWidth onHide={onHide}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
        Surfaces the assumptions behind each strategy so data-starvation /
        config gaps are visible before testing. ⚠ = warmup gate below the bars
        the longest horizon needs (silent-zero risk).
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
                <td style={{ ...td, color: "var(--text-muted)" }}>{i.meta && deskFor(i.name).trader}</td>
                <td style={td}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: i.mode === "live" ? UP : "#4f8cff" }}>
                    {i.mode === "live" ? "LIVE" : "SIGNAL"}
                  </span>
                </td>
                <td style={{ ...td, color: "var(--text-dim)" }}>{i.broker ?? "—"}</td>
                <td style={numTd}>{i.universe ?? "—"}</td>
                <td style={numTd}>{i.warmup ?? "—"}</td>
                <td style={{ ...numTd, color: i.starveRisk ? RED : "var(--text)" }}>
                  {i.need ? i.need.need : "—"}
                </td>
                <td style={td}>
                  {i.need == null ? (
                    <span style={{ color: "var(--text-muted)" }}>—</span>
                  ) : i.starveRisk ? (
                    <span style={{ color: RED, fontWeight: 700 }}
                      title={`Warmup gate ${i.warmup} < ${i.need.need} needed (${i.need.why}). The long horizons under-compute → signal collapses to 0 unless lookback supplies ${i.need.need}+ bars.`}>
                      ⚠ gate &lt; need
                    </span>
                  ) : (
                    <span style={{ color: UP, fontWeight: 700 }}>✓ ok</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        ⚠ doesn't mean broken if the daemon's <code style={{ color: "var(--text-dim)" }}>--lookback-days</code> supplies enough bars —
        it means the <em>gate alone</em> wouldn't guarantee it. Bars-available is logged at runtime (DATA-STARVED warning).
      </div>
    </CockpitCard>
  );
}

const th: React.CSSProperties = { padding: "4px 8px", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em" };
const rTh: React.CSSProperties = { ...th, textAlign: "right" };
const td: React.CSSProperties = { padding: "4px 8px" };
const numTd: React.CSSProperties = { ...td, textAlign: "right", fontFamily: "monospace" };
