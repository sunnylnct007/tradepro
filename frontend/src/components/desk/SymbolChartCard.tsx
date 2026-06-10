/**
 * SymbolChartCard — wraps CandleIchimokuChart with timeframe pills.
 *
 * One job: render a symbol's Ichimoku candlestick chart with 1M/3M/6M/1Y/5Y
 * pill switcher. Reuses the existing CandleIchimokuChart (already has all the
 * Ichimoku logic, lookback padding, cloud overlay, crosshair readout).
 *
 * Props:
 *   symbol   — Yahoo-resolvable chart symbol (e.g. "AAPL", "EC")
 *   height   — optional chart canvas height, default 260 (rail context)
 */
import { useState } from "react";
import { CandleIchimokuChart } from "./CandleIchimokuChart";

const TIMEFRAMES = ["1M", "3M", "6M", "1Y", "5Y"] as const;
type TF = (typeof TIMEFRAMES)[number];

export function SymbolChartCard({
  symbol,
  height = 300,
  entryPrice,
}: {
  symbol: string;
  height?: number;
  /** Held position's avg entry price → dashed "Entry" line on the chart. */
  entryPrice?: number | null;
}) {
  const [tf, setTf] = useState<TF>("3M");

  return (
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: "rgba(255,255,255,0.015)",
        padding: 10,
        // Stretch to fill the rail's width (width:100% + minWidth:0 lets the
        // flex/grid parent constrain it). minWidth:0 prevents chart overflow
        // when the rail is narrower than the chart's natural width.
        width: "100%",
        minWidth: 0,
        boxSizing: "border-box",
      }}
    >
      {/* Timeframe pills — no dropdown, state always visible. */}
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {TIMEFRAMES.map((t) => {
          const active = tf === t;
          return (
            <button
              key={t}
              onClick={() => setTf(t)}
              style={{
                background: active ? "var(--accent, #4f8cff)" : "transparent",
                color:      active ? "#0a0e17" : "var(--text-muted)",
                border: `1px solid ${active ? "var(--accent, #4f8cff)" : "#1b2233"}`,
                borderRadius: 4,
                padding: "2px 8px",
                fontSize: 10,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {t}
            </button>
          );
        })}
      </div>

      <CandleIchimokuChart symbol={symbol} timeframe={tf} height={height} entryPrice={entryPrice} />
    </div>
  );
}
