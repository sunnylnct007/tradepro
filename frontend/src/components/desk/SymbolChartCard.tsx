/**
 * SymbolChartCard — wraps CandleIchimokuChart with resolution + range pills.
 *
 * One job: render a symbol's candlestick chart at the resolution the trader
 * picks (1-min / 5-min / daily, from chartResolutions — the ONE table the Data
 * screen also uses) over a range that resolution offers, with an expand toggle
 * for the rail. The chart itself (lightweight-charts) carries zoom, pan,
 * crosshair readout, indicator toggles and its own provenance caption.
 *
 * 21 Nov 2026, owner: "i do not see 5 min candle selection on graph anymore"
 * — the rail only ever offered daily ranges. Now it offers every resolution the
 * golden store holds; a name the intraday lanes do not harvest shows the
 * chart's "No data" state at 1m/5m rather than a daily series in disguise.
 *
 * The choice is remembered per browser (localStorage) so the chart comes back
 * the way it was left; a storage failure simply means defaults.
 */
import { useEffect, useState, type CSSProperties } from "react";
import { CandleIchimokuChart } from "./CandleIchimokuChart";
import { RES_OPTS, TF_FOR_RES, rangeFor } from "./chartResolutions";

const KEY_RES = "tradepro.chart.res";
const KEY_TF = "tradepro.chart.tf";

function remembered(key: string, fallback: string): string {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function remember(key: string, v: string): void {
  try { localStorage.setItem(key, v); } catch { /* per-viewer convenience only */ }
}

export function SymbolChartCard({
  symbol,
  height = 300,
  entryPrice,
  strategyId,
  entryDate,
  fills,
}: {
  symbol: string;
  height?: number;
  /** Held position's avg entry price → dashed "Entry" line on the chart. */
  entryPrice?: number | null;
  /** Position open date (ISO) → shown on the Entry line label for context. */
  entryDate?: string | null;
  /** Filled trades → buy/sell markers on the chart (entry/exit review). */
  fills?: { side: "BUY" | "SELL"; price: number | null; atUtc: string }[];
  /** The strategy that owns this position — entry timing is graded against
   *  ITS rule, not a retired strategy's cloud-cross. */
  strategyId?: string | null;
}) {
  const [res, setRes] = useState<string>(() => {
    const r = remembered(KEY_RES, "1d");
    return r in TF_FOR_RES ? r : "1d";
  });
  const [tf, setTf] = useState<string>(() => rangeFor(res, remembered(KEY_TF, "3M")));
  // FULL-SCREEN VIEW (owner, 21 Nov 2026: "where we can expand, zoom in etc").
  // The rail is ~400px wide; a chart you can actually work on needs the
  // viewport. Expanded renders the same card in a fixed overlay; Esc closes it.
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [expanded]);
  const [vh, setVh] = useState(() => (typeof window === "undefined" ? 800 : window.innerHeight));
  useEffect(() => {
    const onResize = () => setVh(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const pickRes = (r: string) => {
    setRes(r); remember(KEY_RES, r);
    const t = rangeFor(r, tf);
    setTf(t); remember(KEY_TF, t);
  };
  const pickTf = (t: string) => { setTf(t); remember(KEY_TF, t); };

  const pill = (active: boolean): CSSProperties => ({
    background: active ? "var(--accent, #4f8cff)" : "transparent",
    color: active ? "#0a0e17" : "var(--text-muted)",
    border: `1px solid ${active ? "var(--accent, #4f8cff)" : "#1b2233"}`,
    borderRadius: 4, padding: "2px 8px", fontSize: 10, fontWeight: 600, cursor: "pointer",
  });

  const card = (
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: expanded ? "var(--surface, #0a0e17)" : "rgba(255,255,255,0.015)",
        padding: 10,
        width: "100%",
        minWidth: 0,
        boxSizing: "border-box",
      }}
    >
      {/* Resolution + range pills — no dropdown, state always visible. */}
      <div style={{ display: "flex", gap: 4, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }}>
        {RES_OPTS.map((o) => (
          <button key={o.k} onClick={() => pickRes(o.k)} style={pill(res === o.k)} title={`${o.label} bars from the golden bar store`}>
            {o.label}
          </button>
        ))}
        <span style={{ width: 1, height: 14, background: "#1b2233", margin: "0 4px" }} />
        {(TF_FOR_RES[res] ?? TF_FOR_RES["1d"]).map((t) => (
          <button key={t} onClick={() => pickTf(t)} style={pill(tf === t)}>
            {t}
          </button>
        ))}
        <button
          onClick={() => setExpanded((e) => !e)}
          style={{ ...pill(expanded), marginLeft: "auto" }}
          title={expanded ? "Close the full-screen chart (Esc)" : "Full-screen chart"}
          aria-pressed={expanded}
        >
          {expanded ? "Close ✕" : "Expand ⤢"}
        </button>
      </div>

      <CandleIchimokuChart
        symbol={symbol}
        timeframe={tf}
        resolution={res}
        height={expanded ? Math.max(360, vh - 150) : height}
        entryPrice={entryPrice}
        entryDate={entryDate}
        fills={fills}
        strategyId={strategyId}
      />
    </div>
  );

  if (!expanded) return card;
  return (
    <>
      {/* Keep the rail's slot occupied so the layout does not jump. */}
      <div style={{ height: height + 48 }} />
      <div
        role="dialog"
        aria-label={`${symbol} chart, full screen`}
        onClick={(e) => { if (e.target === e.currentTarget) setExpanded(false); }}
        style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.72)",
                 display: "flex", alignItems: "stretch", justifyContent: "stretch", padding: 16 }}
      >
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>{card}</div>
      </div>
    </>
  );
}
