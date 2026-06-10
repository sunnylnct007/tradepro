/**
 * CandleIchimokuChart — IBKR-grade candlestick chart with an Ichimoku cloud
 * for the /desk Quote view.
 *
 * Why lightweight-charts (TradingView, ~45kb gz, already a dep): it gives us
 * native, GPU-accelerated candlesticks + line series + crosshair + zoom/pan +
 * responsive auto-sizing out of the box — the IBKR-desktop feel — without us
 * hand-rolling a canvas renderer. We add:
 *
 *   - Candlesticks from api.candles({interval:"1d"}) OHLC. Green up / red down.
 *   - Ichimoku Kinko Hyo computed CLIENT-SIDE from the candles (standard
 *     params 9/26/52, 26-forward cloud shift, 26-back Chikou). Tenkan + Kijun
 *     drawn as line series; Senkou A/B as thin line series; the CLOUD (the band
 *     between Senkou A and B, green when A>B else red) is painted by a thin
 *     <canvas> overlay using the chart's coordinate API — lightweight-charts
 *     can't fill between two moving series, so we project both lines to pixels
 *     and fill the polygon between them. This is pixel-accurate and follows the
 *     crosshair zoom/pan via the visible-range subscription.
 *   - Chikou span (close shifted 26 bars back) as a faint line.
 *
 * LOOKBACK PADDING (the bug we're fixing): the Ichimoku cloud needs ≥52 PRIOR
 * daily bars to exist at the LEFT edge of the visible window (Senkou B is a
 * 52-period midpoint; the cloud is then shifted 26 bars forward). The old
 * line+SMA chart fetched only the visible window, so indicators only appeared
 * near the tail. Here we fetch `window + PAD_BARS` (~80 trailing calendar-day
 * equivalents beyond 52 trading bars) of LEADING history, compute Ichimoku on
 * the FULL padded series, then clip the *display* (setVisibleRange) to the
 * selected window — so the cloud/lines span the entire visible window.
 *
 * Honesty: no data is fabricated. Indicator points that don't have enough
 * history are simply omitted (the series starts where the maths is valid);
 * empty candle responses render a "No data" state, never an invented series.
 *
 * Mobile/responsive: autoSize follows the container; a ResizeObserver also
 * repaints the cloud overlay on width changes.
 */
import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../../api/client";
import { config } from "../../config";
import type { Candle, CandleSeries } from "../../api/types";

/** Timeframe pill → visible window in calendar days. Matches QuoteView's pills. */
const WINDOW_DAYS: Record<string, number> = {
  "1M": 31,
  "3M": 93,
  "6M": 186,
  "1Y": 365,
  "5Y": 365 * 5,
};

/** Ichimoku standard parameters. */
const TENKAN = 9;
const KIJUN = 26;
const SENKOU_B = 52;
const SHIFT = 26; // forward shift for the cloud + back shift for Chikou

/** Leading-history pad (calendar days) so Ichimoku is valid at the LEFT edge of
 * the visible window. Senkou B needs 52 trading bars and the cloud is shifted
 * 26 forward → ~78 trading bars ≈ ~115 calendar days; we pad generously. */
const PAD_DAYS = 130;

type Props = {
  symbol: string;
  timeframe: string; // one of WINDOW_DAYS keys
  height?: number;
  ccy?: string | null;
  /** When the symbol is HELD, the position's average entry price — drawn as a
   *  dashed horizontal reference line ("Entry") so the trader sees cost basis
   *  vs the live price at a glance. null/0/undefined ⇒ no line (flat name). */
  entryPrice?: number | null;
  /** Filled trades for this symbol → buy (▲) / sell (▼) markers on the
   *  timeline, each labelled with its fill price, so a closed round-trip
   *  (entered here at X, exited here at Y) is visible directly on the chart. */
  fills?: { side: "BUY" | "SELL"; price: number | null; atUtc: string }[];
};

type IchiPoint = { time: UTCTimestamp; value: number };

export function CandleIchimokuChart({ symbol, timeframe, height = 360, ccy, entryPrice, fills }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Live crosshair readout (OHLC + date) shown above the chart.
  const [hover, setHover] = useState<Candle | null>(null);

  const windowDays = WINDOW_DAYS[timeframe] ?? 365;

  // ---- Fetch candles (padded with leading history) -----------------------
  useEffect(() => {
    if (!symbol) return;
    let live = true;
    setLoading(true);
    setErr(null);
    setSeries(null);
    setHover(null);
    const to = new Date();
    // Fetch the visible window PLUS leading pad so indicators span the window.
    const from = new Date(Date.now() - (windowDays + PAD_DAYS) * 24 * 3600 * 1000);
    api
      .candles({
        symbol,
        provider: config.defaultProvider,
        interval: "1d",
        from: from.toISOString().slice(0, 10),
        to: to.toISOString().slice(0, 10),
      })
      .then((s) => {
        if (live) setSeries(s);
      })
      .catch((e) => {
        if (live) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [symbol, windowDays]);

  // ---- Build / update the chart ------------------------------------------
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !series) return;
    const candles = (series.candles ?? []).filter(
      (c) =>
        Number.isFinite(c.open) &&
        Number.isFinite(c.high) &&
        Number.isFinite(c.low) &&
        Number.isFinite(c.close),
    );
    if (candles.length === 0) return;

    const chart = createChart(el, {
      autoSize: true,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#9ba1ad",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(155,161,173,0.08)" },
        horzLines: { color: "rgba(155,161,173,0.08)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(155,161,173,0.5)", width: 1, style: LineStyle.Dashed },
        horzLine: { color: "rgba(155,161,173,0.5)", width: 1, style: LineStyle.Dashed },
      },
      rightPriceScale: { borderColor: "#1b2233" },
      timeScale: { borderColor: "#1b2233", rightOffset: SHIFT, fixLeftEdge: false },
      // Zoom + pan: wheel/pinch to zoom, drag to pan, drag an axis to scale it,
      // double-click an axis to reset. Set explicitly so the chart stays
      // interactive regardless of library defaults.
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
      },
    });
    chartRef.current = chart;

    // Candlesticks.
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#1fc16b",
      downColor: "#ef4444",
      borderUpColor: "#1fc16b",
      borderDownColor: "#ef4444",
      wickUpColor: "#1fc16b",
      wickDownColor: "#ef4444",
      priceLineVisible: false,
    });
    candleSeries.setData(
      candles.map((c) => ({
        time: toTime(c.timestamp),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );

    // Entry-price reference line for a held symbol (cost basis at a glance).
    if (entryPrice != null && Number.isFinite(entryPrice) && entryPrice > 0) {
      candleSeries.createPriceLine({
        price: entryPrice,
        color: "#e0b341",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Entry",
      });
    }

    // Trade markers: buy (▲ below bar) / sell (▼ above bar) at each fill,
    // labelled with the fill price, so a closed round-trip reads off the chart
    // — "entered here at X, exited here at Y". Snapped to the daily bar via
    // toTime; sorted ascending as lightweight-charts requires.
    if (fills && fills.length) {
      const markers: SeriesMarker<Time>[] = fills
        .filter((f) => f.atUtc)
        .map((f) => ({
          time: toTime(f.atUtc),
          position: f.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
          color: f.side === "BUY" ? "#1fc16b" : "#ef4444",
          shape: f.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
          text: `${f.side === "BUY" ? "B" : "S"}${f.price != null ? " @" + f.price.toFixed(2) : ""}`,
        }))
        .sort((a, b) => (a.time as number) - (b.time as number));
      if (markers.length) createSeriesMarkers(candleSeries, markers);
    }

    // Ichimoku, computed on the FULL padded series.
    const ich = computeIchimoku(candles);

    const tenkan = lineSeries(chart, "#4f8cff", 1.5);
    tenkan.setData(ich.tenkan);
    const kijun = lineSeries(chart, "#d4793b", 1.5);
    kijun.setData(ich.kijun);
    const spanA = lineSeries(chart, "rgba(31,193,107,0.9)", 1, LineStyle.Solid);
    spanA.setData(ich.spanA);
    const spanB = lineSeries(chart, "rgba(239,68,68,0.9)", 1, LineStyle.Solid);
    spanB.setData(ich.spanB);
    const chikou = lineSeries(chart, "rgba(155,110,255,0.85)", 1, LineStyle.Solid);
    chikou.setData(ich.chikou);

    // Cloud overlay: keep refs to the two span series so we can project their
    // values to pixels each frame.
    const spanARef = spanA;
    const spanBRef = spanB;
    const spanAByTime = new Map(ich.spanA.map((p) => [p.time, p.value]));
    const spanBByTime = new Map(ich.spanB.map((p) => [p.time, p.value]));
    // Union of cloud times (sorted) — the x-grid we paint the band over.
    const cloudTimes = [...new Set([...spanAByTime.keys(), ...spanBByTime.keys()])].sort(
      (a, b) => (a as number) - (b as number),
    ) as UTCTimestamp[];

    const paintCloud = () => {
      const canvas = overlayRef.current;
      if (!canvas) return;
      const w = el.clientWidth;
      const h = el.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const ts = chart.timeScale();
      // Build pixel points for SpanA and SpanB where BOTH exist.
      type PX = { x: number; a: number; b: number };
      const pts: PX[] = [];
      for (const t of cloudTimes) {
        const av = spanAByTime.get(t);
        const bv = spanBByTime.get(t);
        if (av === undefined || bv === undefined) continue;
        const x = ts.timeToCoordinate(t);
        const ay = spanARef.priceToCoordinate(av);
        const by = spanBRef.priceToCoordinate(bv);
        if (x === null || ay === null || by === null) continue;
        pts.push({ x, a: ay, b: by });
      }
      if (pts.length < 2) return;

      // Fill contiguous segments, colour by sign of (SpanA − SpanB). Split the
      // polygon whenever the relationship flips so green/red never bleed.
      let segStart = 0;
      const sign = (p: PX) => (p.a <= p.b ? 1 : -1); // y grows downward: a above b ⇒ A>B
      for (let i = 1; i <= pts.length; i++) {
        const flip = i === pts.length || sign(pts[i]) !== sign(pts[segStart]);
        if (!flip) continue;
        const seg = pts.slice(segStart, i);
        if (seg.length >= 2) {
          const bullish = sign(pts[segStart]) === 1; // A above B (in price) ⇒ bullish cloud
          ctx.beginPath();
          ctx.moveTo(seg[0].x, seg[0].a);
          for (let k = 1; k < seg.length; k++) ctx.lineTo(seg[k].x, seg[k].a);
          for (let k = seg.length - 1; k >= 0; k--) ctx.lineTo(seg[k].x, seg[k].b);
          ctx.closePath();
          ctx.fillStyle = bullish ? "rgba(31,193,107,0.16)" : "rgba(239,68,68,0.16)";
          ctx.fill();
        }
        segStart = i;
      }
    };

    // Crosshair tooltip: surface OHLC + date for the bar under the cursor.
    const byTime = new Map(candles.map((c) => [toTime(c.timestamp) as number, c]));
    chart.subscribeCrosshairMove((p) => {
      if (!p.time) {
        setHover(null);
        return;
      }
      const c = byTime.get(p.time as number);
      setHover(c ?? null);
    });

    // Clip the DISPLAY to the selected window (indicators were computed on the
    // padded series). Anchor the visible range to the last `windowDays`.
    const lastTs = toTime(candles[candles.length - 1].timestamp) as number;
    const fromTs = (lastTs - windowDays * 24 * 3600) as number;
    chart.timeScale().setVisibleRange({ from: fromTs as Time, to: (lastTs + SHIFT * 24 * 3600) as Time });

    // Repaint the cloud on any pan/zoom + on resize.
    chart.timeScale().subscribeVisibleTimeRangeChange(paintCloud);
    const ro = new ResizeObserver(() => paintCloud());
    ro.observe(el);
    // Initial paints (a couple of RAFs so coordinates settle after layout).
    requestAnimationFrame(() => {
      paintCloud();
      requestAnimationFrame(paintCloud);
    });

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [series, height, windowDays, entryPrice, fills]);

  // ---- States ------------------------------------------------------------
  const noData = !loading && !err && (series?.candles?.length ?? 0) === 0;

  return (
    <div>
      {/* Crosshair OHLC readout (a plus over the basic chart). */}
      <div
        style={{
          minHeight: 18,
          fontSize: 11,
          fontFamily: "monospace",
          color: "var(--text-muted)",
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 4,
        }}
      >
        {hover ? (
          <>
            <span>{hover.timestamp.slice(0, 10)}</span>
            <span>O {fmt(hover.open, ccy)}</span>
            <span>H {fmt(hover.high, ccy)}</span>
            <span>L {fmt(hover.low, ccy)}</span>
            <span style={{ color: hover.close >= hover.open ? "#1fc16b" : "#ef4444" }}>
              C {fmt(hover.close, ccy)}
            </span>
            <span>Vol {Number.isFinite(hover.volume) ? hover.volume.toLocaleString() : "—"}</span>
          </>
        ) : (
          <span>Candles · Ichimoku cloud (9·26·52) — hover for OHLC</span>
        )}
      </div>

      <div style={{ position: "relative", width: "100%", height }}>
        {/* The chart mounts here; the canvas overlay paints the cloud band on
            top, pointer-events:none so it never blocks the crosshair. */}
        <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
        <canvas
          ref={overlayRef}
          style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
        />
        {(loading || err || noData) && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 12,
              color: err ? "var(--down, #ef4444)" : "var(--text-muted)",
              background: "rgba(13,17,23,0.4)",
            }}
          >
            {err
              ? `Chart unavailable: ${err}`
              : loading
              ? `Loading ${symbol} candles…`
              : `No candle data for ${symbol}.`}
          </div>
        )}
      </div>

      <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
        Daily candles (green up / red down). Ichimoku Kinko Hyo: Tenkan-sen (9,
        blue) · Kijun-sen (26, orange) · the cloud is the band between Senkou
        Span A &amp; B (green when A&gt;B, red when below) shifted 26 bars
        forward · Chikou span (close shifted 26 back, purple). Indicators are
        computed on ~{PAD_DAYS} extra leading bars so the cloud spans the whole
        window. No data is fabricated — points without enough history are
        omitted.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ichimoku maths (client-side, standard 9/26/52 with 26-bar shifts)
// ---------------------------------------------------------------------------

type Ichimoku = {
  tenkan: IchiPoint[];
  kijun: IchiPoint[];
  spanA: IchiPoint[];
  spanB: IchiPoint[];
  chikou: IchiPoint[];
};

/** Midpoint (highest-high + lowest-low) / 2 over the trailing `period` bars
 * ending at index `i`, or null when there isn't enough history. */
function midpoint(candles: Candle[], i: number, period: number): number | null {
  if (i + 1 < period) return null;
  let hi = -Infinity;
  let lo = Infinity;
  for (let k = i - period + 1; k <= i; k++) {
    if (candles[k].high > hi) hi = candles[k].high;
    if (candles[k].low < lo) lo = candles[k].low;
  }
  return (hi + lo) / 2;
}

function computeIchimoku(candles: Candle[]): Ichimoku {
  const times = candles.map((c) => toTime(c.timestamp));
  const tenkan: IchiPoint[] = [];
  const kijun: IchiPoint[] = [];
  const spanA: IchiPoint[] = [];
  const spanB: IchiPoint[] = [];
  const chikou: IchiPoint[] = [];

  // Tenkan / Kijun (plotted at the bar they're computed on).
  const tk: (number | null)[] = [];
  const kj: (number | null)[] = [];
  for (let i = 0; i < candles.length; i++) {
    const t = midpoint(candles, i, TENKAN);
    const k = midpoint(candles, i, KIJUN);
    tk.push(t);
    kj.push(k);
    if (t !== null) tenkan.push({ time: times[i], value: t });
    if (k !== null) kijun.push({ time: times[i], value: k });
  }

  // One day in seconds — we extend the time axis SHIFT bars beyond the last
  // candle so the forward-shifted cloud has somewhere to land. Weekends make
  // this approximate, but lightweight-charts tolerates calendar-day spacing on
  // a daily series and it keeps the cloud projecting ahead of price (the whole
  // point of the forward shift).
  const DAY = 24 * 3600;
  const lastSec = (times[times.length - 1] as number) ?? 0;

  for (let i = 0; i < candles.length; i++) {
    // Senkou Span A = (Tenkan + Kijun)/2, shifted SHIFT bars forward.
    if (tk[i] !== null && kj[i] !== null) {
      const v = (tk[i]! + kj[i]!) / 2;
      const fi = i + SHIFT;
      const time =
        fi < times.length ? times[fi] : ((lastSec + (fi - (times.length - 1)) * DAY) as UTCTimestamp);
      spanA.push({ time, value: v });
    }
    // Senkou Span B = 52-period midpoint, shifted SHIFT bars forward.
    const b = midpoint(candles, i, SENKOU_B);
    if (b !== null) {
      const fi = i + SHIFT;
      const time =
        fi < times.length ? times[fi] : ((lastSec + (fi - (times.length - 1)) * DAY) as UTCTimestamp);
      spanB.push({ time, value: b });
    }
    // Chikou span = close shifted SHIFT bars BACK.
    const bi = i - SHIFT;
    if (bi >= 0) {
      chikou.push({ time: times[bi], value: candles[i].close });
    }
  }

  // De-dupe + sort by time (the forward shift can collide a synthesized future
  // timestamp with a real one only at the boundary; keep the last write).
  return {
    tenkan: dedupe(tenkan),
    kijun: dedupe(kijun),
    spanA: dedupe(spanA),
    spanB: dedupe(spanB),
    chikou: dedupe(chikou),
  };
}

/** lightweight-charts requires strictly ascending, unique times. */
function dedupe(pts: IchiPoint[]): IchiPoint[] {
  const m = new Map<number, IchiPoint>();
  for (const p of pts) m.set(p.time as number, p);
  return [...m.values()].sort((a, b) => (a.time as number) - (b.time as number));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function lineSeries(
  chart: IChartApi,
  color: string,
  lineWidth: 1 | 2 | 1.5,
  style: LineStyle = LineStyle.Solid,
): ISeriesApi<"Line"> {
  return chart.addSeries(LineSeries, {
    color,
    // lightweight-charts LineWidth is an integer; round 1.5 → 2 for visibility.
    lineWidth: (lineWidth >= 1.5 ? 2 : 1) as 1 | 2,
    lineStyle: style,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
}

/** ISO date (or datetime) → UTC seconds Time for a daily series. */
function toTime(iso: string): UTCTimestamp {
  const d = iso.slice(0, 10);
  return Math.floor(new Date(`${d}T00:00:00Z`).getTime() / 1000) as UTCTimestamp;
}

function fmt(v: number | null | undefined, ccy?: string | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const prefix = ccy === "USD" ? "$" : ccy === "GBP" ? "£" : ccy === "EUR" ? "€" : "";
  return `${prefix}${v.toFixed(2)}`;
}
