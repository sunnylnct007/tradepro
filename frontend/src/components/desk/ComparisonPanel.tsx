/**
 * The symbol against its sector ETF, SPY and QQQ, rebased to 100 at the
 * window start (review item 15). Four lines at most; the symbol is the bold
 * one; direct labels at the right edge instead of a legend; fixed colours
 * across every chart (SPY grey, QQQ blue, sector amber).
 */
import { useEffect, useRef } from "react";
import { ColorType, LineSeries, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";

import type { Candle } from "../../api/types";
import { rebase } from "./chartContext";

const COLOUR = (k: string, symbol: string, etf: string) =>
  k === symbol ? "#e6edf3" : k === "SPY" ? "#9aa4b2" : k === "QQQ" ? "#4f8cff" : k === etf ? "#d29922" : "#888";

export function ComparisonPanel({ symbol, etf, candles, days, height = 150 }: {
  symbol: string; etf: string; candles: Record<string, Candle[]>; days: number; height?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const cut = new Date(Date.now() - days * 86400_000).toISOString().slice(0, 10);
    const windowed: Record<string, Candle[]> = {};
    for (const k of [symbol, etf, "QQQ", "SPY"]) {
      const rows = (candles[k] ?? []).filter((c) => c.timestamp.slice(0, 10) >= cut);
      if (rows.length >= 2 && !windowed[k]) windowed[k] = rows;
    }
    const series = rebase(windowed);
    if (!Object.keys(series).length) return;
    const chart = createChart(el, {
      height, width: el.clientWidth,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b949e", fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.05)" } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderVisible: false, timeVisible: false },
      handleScale: false, handleScroll: false, crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    for (const k of Object.keys(series)) {
      const last = series[k][series[k].length - 1].value;
      const s = chart.addSeries(LineSeries, {
        color: COLOUR(k, symbol, etf), lineWidth: k === symbol ? 3 : 1,
        title: `${k} ${last - 100 > 0 ? "+" : ""}${(last - 100).toFixed(0)}%`,
        priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false,
      });
      s.setData(series[k].map((p) => ({ time: (Date.parse(p.time + "T00:00:00Z") / 1000) as UTCTimestamp, value: p.value })));
    }
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  }, [symbol, etf, candles, days, height]);
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginBottom: 2 }}>
        rebased to 100 at the window start — {symbol} (bold) vs {etf} (amber), QQQ (blue), SPY (grey)
      </div>
      <div ref={ref} style={{ width: "100%", height }} />
    </div>
  );
}
