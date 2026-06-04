/**
 * CandleChart — TradingView-style candlestick chart (lightweight-charts) for
 * post-trade analysis. Renders the price as CANDLES with the Ichimoku cloud
 * (tenkan/kijun + senkou A/B) overlaid and a green ▲ / red ▼ at every BUY/SELL
 * fill, so a trader can SEE what price did around each trade.
 *
 * Data comes from the backend's existing ichimoku_cloud Plotly figure (decoded
 * by util/plotlyData) — no new backend. Falls back gracefully if the figure
 * can't be parsed.
 */
import { useEffect, useRef } from "react";
import {
  createChart, CandlestickSeries, LineSeries, createSeriesMarkers,
  ColorType, LineStyle, type UTCTimestamp, type IChartApi,
} from "lightweight-charts";
import { extractIchimoku } from "../util/plotlyData";

export function CandleChart({ figure, height = 420 }: { figure: unknown; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const noData = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const series = extractIchimoku(figure);
    noData.current = !series;
    if (!series) return;

    const chart: IChartApi = createChart(el, {
      height,
      width: el.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#9aa3b2",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "rgba(255,255,255,0.12)" },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.12)" },
      crosshair: { mode: 0 },
    });

    // Cloud boundaries (faint) — drawn first so candles sit on top.
    const cloudOpts = { lineWidth: 1 as const, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false };
    if (series.senkouA.length) chart.addSeries(LineSeries, { color: "rgba(6,167,125,0.5)", ...cloudOpts }).setData(series.senkouA as never);
    if (series.senkouB.length) chart.addSeries(LineSeries, { color: "rgba(230,57,70,0.5)", ...cloudOpts }).setData(series.senkouB as never);
    if (series.tenkan.length) chart.addSeries(LineSeries, { color: "#2E86AB", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "Tenkan" }).setData(series.tenkan as never);
    if (series.kijun.length) chart.addSeries(LineSeries, { color: "#A23B72", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "Kijun" }).setData(series.kijun as never);

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "#1fc16b", downColor: "#ef4444",
      borderUpColor: "#1fc16b", borderDownColor: "#ef4444",
      wickUpColor: "#1fc16b", wickDownColor: "#ef4444",
    });
    candle.setData(series.candles as never);

    // Snap each fill marker to the nearest candle time so it always lands on a bar.
    const times = series.candles.map((c) => c.time);
    const snap = (t: number) => times.reduce((best, x) => (Math.abs(x - t) < Math.abs(best - t) ? x : best), times[0] ?? t);
    if (series.markers.length && times.length) {
      const markers = series.markers
        .map((m) => ({
          time: snap(m.time) as UTCTimestamp,
          position: m.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
          color: m.side === "BUY" ? "#1fc16b" : "#ef4444",
          shape: m.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
          text: m.side,
        }))
        .sort((a, b) => (a.time as number) - (b.time as number));
      createSeriesMarkers(candle, markers);
    }

    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => { ro.disconnect(); chart.remove(); };
  }, [figure, height]);

  return (
    <div style={{ width: "100%" }}>
      <div ref={ref} style={{ width: "100%", height }} />
      {noData.current && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 4px" }}>
          No candle data for this symbol yet.
        </div>
      )}
    </div>
  );
}
