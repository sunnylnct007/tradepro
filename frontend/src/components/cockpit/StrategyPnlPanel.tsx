/**
 * StrategyPnlPanel — the canonical, reconcilable P&L surface.
 *
 * One panel, one principle: the atomic unit is (strategy × symbol). The same
 * symbol traded by two strategies shows as TWO lines, so you can see "AMZN made
 * money in ichimoku_equity but lost in intraday_flat". Everything rolls up from
 * there and reconciles to the headline.
 *
 * Every number self-labels TYPE · WINDOW · SOURCE, and we only show what we can
 * prove:
 *   • Open  (unrealized · now · BROKER mark)  = (broker mark − OMS avg cost) × qty
 *           — single mark source (the broker), so per-symbol lines SUM to the
 *             strategy total, which sums to the headline. No second price feed.
 *   • Realised (banked · life-to-date · IG golden) = IG closed-deal net, per
 *           (strategy, symbol). T212 has no provable realised feed → shown as
 *           "n/a", never a guess.
 *
 * Attribution source: the OMS fill ledger (oms_orders) is the ONLY thing tagged
 * with strategy_id — the broker only knows positions, not which strategy opened
 * them. So open lots come from OMS (per strategy), marked at the broker price.
 */
import { useEffect, useMemo, useState } from "react";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";
import { useSort } from "../../util/useSort";
import { SortTh } from "../SortTh";

type OmsPos = { strategyId: string; symbol: string; broker: string; quantity: number; avgPrice: number | null };
type Mark = { mark: number; currency: string };
type Line = {
  strategyId: string;
  symbol: string;
  broker: string;
  quantity: number;
  avg: number | null;
  mark: number | null;
  currency: string;
  open: number | null;       // unrealized, broker mark
  realised: number | null;   // banked, IG golden (null = not provable, e.g. T212)
};

const UP = "#1fc16b";
const DOWN = "#ef4444";
const ccySym = (c?: string | null) => (c === "GBP" ? "£" : c === "USD" ? "$" : (c ? c + " " : ""));

function money(v: number | null | undefined, c?: string | null, bold = false) {
  if (v == null) return <span style={{ color: "var(--text-muted)" }}>n/a</span>;
  return (
    <span style={{ fontFamily: "monospace", fontWeight: bold ? 700 : 500, color: v >= 0 ? UP : DOWN }}>
      {v >= 0 ? "+" : "−"}{ccySym(c)}{Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}
    </span>
  );
}

// Stable label for a strategy id.
const STRAT_LABEL: Record<string, string> = {
  ichimoku_equity: "Ichimoku Equity",
  ichimoku_fx_mr: "Ichimoku FX (MR)",
  intraday_flat: "Intraday Flat",
};
const sLabel = (id: string) => STRAT_LABEL[id] ?? id;

export function StrategyPnlPanel({ onHide }: { onHide?: () => void }) {
  const [lines, setLines] = useState<Line[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<"strategy" | "symbol">("strategy");
  const { sorted, sortKey, dir, toggle } = useSort<Line>(lines, {
    symbol: (r) => r.symbol,
    quantity: (r) => r.quantity,
    avg: (r) => r.avg,
    mark: (r) => r.mark,
    open: (r) => r.open,
    realised: (r) => r.realised,
  }, { key: "open", dir: "desc" });

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [oms, t212, ig, hist] = await Promise.all([
          api.omsPositions(),
          api.t212Positions("demo").catch(() => null),
          api.igPositions().catch(() => null),
          api.igHistory(3650).catch(() => null), // life-to-date realised
        ]);
        if (!live) return;

        // Single mark source = the BROKER. Build symbol → {mark, currency}.
        const marks: Record<string, Mark> = {};
        for (const p of t212?.positions ?? []) {
          const key = (p.yahooSymbol ?? p.ticker ?? "").toUpperCase();
          if (key && p.currentPrice != null) marks[key] = { mark: p.currentPrice, currency: p.currency ?? "USD" };
        }
        for (const p of ig?.positions ?? []) {
          const key = (p.ticker ?? "").toUpperCase();
          if (key && p.currentPrice != null) marks[key] = { mark: p.currentPrice, currency: "GBP" };
        }

        // Realised (IG golden) keyed by strategyId|symbol.
        const realisedKey: Record<string, number> = {};
        for (const r of hist?.byStrategySymbol ?? []) {
          realisedKey[`${r.strategyId}|${r.symbol.toUpperCase()}`] = r.net;
        }

        const out: Line[] = (oms.positions ?? [])
          .filter((p) => p.quantity !== 0)
          .map((p: OmsPos) => {
            const sym = p.symbol.toUpperCase();
            const m = marks[sym] ?? null;
            const open = m != null && p.avgPrice != null ? (m.mark - p.avgPrice) * p.quantity : null;
            // IG realised is golden; T212 has no provable realised feed → null.
            const isIg = p.broker.startsWith("IG");
            const realised = isIg ? (realisedKey[`${p.strategyId}|${sym}`] ?? null) : null;
            return {
              strategyId: p.strategyId, symbol: sym, broker: p.broker,
              quantity: p.quantity, avg: p.avgPrice, mark: m?.mark ?? null,
              currency: m?.currency ?? (isIg ? "GBP" : "USD"),
              open, realised,
            };
          });
        setLines(out);
        setErr(null);
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : "failed to load P&L");
      }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  // Group rollups (per native currency — never blended).
  const groups = useMemo(() => {
    const by: Record<string, Line[]> = {};
    for (const l of sorted) {
      const k = groupBy === "strategy" ? l.strategyId : l.symbol;
      (by[k] ??= []).push(l);
    }
    return Object.entries(by).map(([key, rows]) => {
      const byCcy: Record<string, { open: number; realised: number; hasOpen: boolean; hasReal: boolean }> = {};
      for (const r of rows) {
        const c = byCcy[r.currency] ??= { open: 0, realised: 0, hasOpen: false, hasReal: false };
        if (r.open != null) { c.open += r.open; c.hasOpen = true; }
        if (r.realised != null) { c.realised += r.realised; c.hasReal = true; }
      }
      return { key, rows, byCcy };
    });
  }, [sorted, groupBy]);

  const Pill = ({ v, label }: { v: "strategy" | "symbol"; label: string }) => (
    <button
      onClick={() => setGroupBy(v)}
      style={{
        fontSize: 11, padding: "3px 10px", borderRadius: 999, cursor: "pointer",
        border: `1px solid ${groupBy === v ? "#4f8cff" : "var(--border)"}`,
        background: groupBy === v ? "#4f8cff22" : "transparent",
        color: groupBy === v ? "#4f8cff" : "var(--text-muted)",
      }}
    >{label}</button>
  );

  return (
    <CockpitCard id="strategy-pnl" title="P&L by strategy × symbol" fullWidth onHide={onHide}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>group by</span>
        <Pill v="strategy" label="Strategy" />
        <Pill v="symbol" label="Symbol" />
        <span style={{ fontSize: 10.5, color: "var(--text-muted)", marginLeft: "auto" }}>
          Open = (broker mark − avg cost) × qty · Realised = IG closed deals (golden) · per native currency, not blended
        </span>
      </div>

      {err && <div style={{ fontSize: 12, color: DOWN, padding: "6px 0" }}>P&L unavailable: {err}</div>}
      {!err && lines.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "12px 0" }}>No open OMS positions to attribute.</div>
      )}

      {groups.map(({ key, rows, byCcy }) => (
        <div key={key} style={{ marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, borderBottom: "1px solid var(--border)", paddingBottom: 4 }}>
            <strong style={{ fontSize: 13 }}>{groupBy === "strategy" ? sLabel(key) : key}</strong>
            <span style={{ marginLeft: "auto", fontSize: 11, display: "flex", gap: 14 }}>
              {Object.entries(byCcy).map(([c, v]) => (
                <span key={c}>
                  <span style={{ color: "var(--text-muted)" }}>open </span>{v.hasOpen ? money(v.open, c, true) : <span style={{ color: "var(--text-muted)" }}>n/a</span>}
                  <span style={{ color: "var(--text-muted)", margin: "0 4px" }}>·</span>
                  <span style={{ color: "var(--text-muted)" }}>realised </span>{v.hasReal ? money(v.realised, c) : <span style={{ color: "var(--text-muted)" }}>n/a</span>}
                </span>
              ))}
            </span>
          </div>
          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ color: "var(--text-muted)", textAlign: "right" }}>
                <SortTh label={groupBy === "strategy" ? "Symbol" : "Strategy"} col="symbol" sortKey={sortKey} dir={dir} onSort={toggle} style={{ textAlign: "left" }} />
                <SortTh label="Qty" col="quantity" sortKey={sortKey} dir={dir} onSort={toggle} />
                <SortTh label="Avg cost" col="avg" sortKey={sortKey} dir={dir} onSort={toggle} />
                <SortTh label="Mark" col="mark" sortKey={sortKey} dir={dir} onSort={toggle} />
                <SortTh label="Open (unrealized)" col="open" sortKey={sortKey} dir={dir} onSort={toggle} />
                <SortTh label="Realised (IG)" col="realised" sortKey={sortKey} dir={dir} onSort={toggle} />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.strategyId}|${r.symbol}`} style={{ borderTop: "1px solid var(--border)", textAlign: "right" }}>
                  <td style={{ textAlign: "left", fontFamily: "monospace" }}>{groupBy === "strategy" ? r.symbol : sLabel(r.strategyId)}</td>
                  <td style={{ fontFamily: "monospace" }}>{r.quantity}</td>
                  <td style={{ fontFamily: "monospace" }}>{r.avg != null ? `${ccySym(r.currency)}${r.avg.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"}</td>
                  <td style={{ fontFamily: "monospace" }}>{r.mark != null ? `${ccySym(r.currency)}${r.mark.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : <span style={{ color: "var(--text-muted)" }}>no mark</span>}</td>
                  <td>{money(r.open, r.currency)}</td>
                  <td>{money(r.realised, r.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 4, lineHeight: 1.5 }}>
        <strong>Open</strong> = unrealized mark-to-market, now, from the broker's own price (single source — rows sum to the strategy total).
        <strong> Realised</strong> = banked, life-to-date, IG closed deals incl. fees (golden). T212 has no provable realised feed, so equity realised shows <em>n/a</em> rather than a guess.
        Attribution is per (strategy × symbol) from the OMS fill ledger — the only source tagged with which strategy opened each lot.
      </div>
    </CockpitCard>
  );
}
