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
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
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
import { canonicalSymbol } from "../../util/brokerSymbols";
import { config } from "../../config";
import { fmtEntryDate } from "./deskFormat";
import { atrBrickSize, renkoBricks } from "./renko";
import type { Candle, CandleSeries } from "../../api/types";
import { volumeScaleBreak } from "../../lib/volumeUnits";

/** Timeframe pill → visible window in calendar days. Matches QuoteView's pills. */
const WINDOW_DAYS: Record<string, number> = {
  "1D": 1,   // intraday ranges (used with a 1m/5m resolution)
  "5D": 5,
  "1M": 31,
  "3M": 93,
  "6M": 186,
  "1Y": 365,
  "5Y": 365 * 5,
};

/** Ichimoku parameters — ALIGNED TO THE LIVE STRATEGY (ichimoku_equity uses
 * tenkan=5, kijun=32, senkou_b=50, displacement=32), NOT the textbook 9/26/52.
 * This is a VALIDATION surface: the cloud + signals shown must MATCH the signal
 * that actually traded, or we'd be validating against a different picture. */
const TENKAN = 5;
const KIJUN = 32;
const SENKOU_B = 50;
const SHIFT = 32; // displacement: forward shift for the cloud + back shift for Chikou

/** Leading-history pad (calendar days) so Ichimoku is valid at the LEFT edge of
 * the visible window. Senkou B needs 52 trading bars and the cloud is shifted
 * 26 forward → ~78 trading bars ≈ ~115 calendar days; we pad generously. */
// Widened 130 → 310 calendar days (≈210 sessions) on 22 Aug 2026: the SMA200
// overlay needs 200 SESSIONS of lead-in before its first point exists, so at
// the old pad it started mid-window and read as "the chart is broken". Now
// every indicator (cloud ~82 bars, SMA200) spans the whole visible window.
// Daily fetches are cache-served from the store, so the wider ask is free.
const PAD_DAYS = 310;

type Props = {
  symbol: string;
  timeframe: string; // one of WINDOW_DAYS keys
  /** Bar resolution. "1d" (default) fetches Yahoo daily candles; an intraday
   *  value ("1m"/"5m"/"15m"/"1h") fetches from the deep IBKR store
   *  (ibkr_price_bars) instead, so the harvested intraday data is chartable. */
  resolution?: string;
  height?: number;
  ccy?: string | null;
  /** When the symbol is HELD, the position's average entry price — drawn as a
   *  dashed horizontal reference line ("Entry") so the trader sees cost basis
   *  vs the live price at a glance. null/0/undefined ⇒ no line (flat name). */
  entryPrice?: number | null;
  /** Position open date (ISO) — appended to the "Entry" line label so the
   *  trader sees WHEN the entry was taken, not just the cost-basis price. */
  entryDate?: string | null;
  /** Filled trades for this symbol → buy (▲) / sell (▼) markers on the
   *  timeline, each labelled with its fill price, so a closed round-trip
   *  (entered here at X, exited here at Y) is visible directly on the chart. */
  fills?: { side: "BUY" | "SELL"; price: number | null; atUtc: string }[];
};

type IchiPoint = { time: UTCTimestamp; value: number };

// Approx regular-session bars per day per resolution — turns a day-window into
// a bar LIMIT for the intraday fetch (the store returns the latest N bars).
const BARS_PER_DAY: Record<string, number> = { "1m": 390, "5m": 78, "15m": 26, "1h": 7, "1d": 1 };

/** Indicator toggles — persisted so the trader's chart setup survives
 *  navigation. Defaults chosen per timeframe use: volume always, SMAs for
 *  swing (daily), VWAP for intraday; RSI opt-in. */
type IndState = { vol: boolean; sma50: boolean; sma200: boolean; vwap: boolean; rsi: boolean; ich: boolean; obv: boolean };
// ich defaults OFF (owner 22 Aug: the cloud misleads; platform studies concur
// — it visualizes the strategy's view on demand, it is not evidence).
const IND_DEFAULTS: IndState = { vol: true, sma50: true, sma200: true, vwap: true, rsi: false, ich: false, obv: false };
const IND_KEY = "tp-chart-indicators";

export function CandleIchimokuChart({ symbol, timeframe, resolution = "1d", height = 360, ccy, entryPrice, entryDate, fills }: Props) {
  const [ind, setInd] = useState<IndState>(() => {
    try { return { ...IND_DEFAULTS, ...JSON.parse(localStorage.getItem(IND_KEY) ?? "{}") }; }
    catch { return IND_DEFAULTS; }
  });
  const toggleInd = (k: keyof IndState) => setInd((p) => {
    const nx = { ...p, [k]: !p[k] };
    try { localStorage.setItem(IND_KEY, JSON.stringify(nx)); } catch { /* private mode */ }
    return nx;
  });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Live crosshair readout (OHLC + date) shown above the chart.
  const [hover, setHover] = useState<Candle | null>(null);

  // REGIME READ (22 Aug 2026). Stated as a verdict, not another squiggle,
  // because it answers a gate question: "is this even a trending market?"
  // Five of the stack's filters are trend-family and none of them tests
  // whether a trend exists — MU fired at ER 0.06 / ADX 12, WCC at 0.09 / 13.8.
  // In chop, trend-family signals are noise regardless of which way they point.
  const regime = useMemo(() => {
    const cs = series?.candles ?? [];
    if (cs.length < 40) return null;
    const er = efficiencyRatio(cs, 20);
    const adx = adxPoints(cs, 14);
    if (!er.length || !adx.length) return null;
    const e = er[er.length - 1].value, a = adx[adx.length - 1].value;
    const trending = e >= 0.30 && a >= 20;
    const weak = e < 0.20 || a < 15;
    return {
      er: e, adx: a,
      label: trending ? "TRENDING" : weak ? "CHOP" : "WEAK TREND",
      tone: trending ? "#1fc16b" : weak ? "#ef4444" : "#e0b341",
    };
  }, [series]);

  // ADR BUDGET (intraday). How much of a typical day's range this session has
  // already used. Past ~1.5x, breakout odds collapse and fade odds rise — the
  // move has already happened. On a name running ~7% ATR/day that budget IS
  // the intraday game, and nothing on the chart said where we were in it.
  const adrBudget = useMemo(() => {
    const cs = series?.candles ?? [];
    if (resolution === "1d" || cs.length < 30) return null;
    const byDay = new Map<string, { hi: number; lo: number }>();
    for (const c of cs) {
      const d = String(c.timestamp).slice(0, 10);
      const cur = byDay.get(d);
      if (!cur) byDay.set(d, { hi: c.high, lo: c.low });
      else { cur.hi = Math.max(cur.hi, c.high); cur.lo = Math.min(cur.lo, c.low); }
    }
    const days = [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
    if (days.length < 6) return null;
    const prior = days.slice(-21, -1).map(([, r]) => r.hi - r.lo).filter((x) => x > 0);
    if (prior.length < 5) return null;
    const adr = prior.reduce((a, b) => a + b, 0) / prior.length;
    const today = days[days.length - 1][1];
    return { used: (today.hi - today.lo) / adr };
  }, [series, resolution]);

  // Relative volume (bar vol ÷ trailing-20-bar average) per timestamp — the
  // breakout-vs-fakeout tell: a push through resistance at ×2.0 average is a
  // move; the same push at ×0.5 is nobody there. Computed once per series so
  // the crosshair readout costs a Map lookup per hover, not a scan.
  const rvolByTs = useMemo(() => {
    const m = new Map<string, number>();
    const cs = series?.candles ?? [];
    const vol = (c: Candle) => (Number.isFinite(c.volume) ? Number(c.volume) : 0);

    // WITHHOLD across a volume-units change rather than compute over one.
    // RVOL divides a bar by the same minute on PRIOR SESSIONS (intraday) or by
    // a trailing 20-session mean (daily) — both compare ACROSS the seam, and a
    // ratio only cancels a UNIFORM error. With inflated bars on one side it
    // reads ~0.01: a 99% volume collapse on every symbol at once, the morning
    // after a units fix that was entirely correct. An empty map renders no
    // RVOL, which reads as "not available"; a plausible wrong number does not.
    if (volumeScaleBreak(cs) > 0) return m;

    if (resolution !== "1d") {
      // INTRADAY: compare each bar to the SAME TIME OF DAY on prior sessions,
      // never to a trailing average (corrected 22 Aug 2026 — the first version
      // shipped that morning was wrong). Intraday volume is U-shaped: the
      // opening bars are always huge and midday is always thin, so a trailing
      // window that straddles the open makes every 11:00 bar look weak and
      // every 09:35 bar look explosive, regardless of what is happening. The
      // real question is "busy FOR THIS TIME OF DAY".
      const byMinute = new Map<string, number[]>();
      for (const c of cs) {
        const t = String(c.timestamp);
        const hhmm = t.length >= 16 ? t.slice(11, 16) : "";
        if (!hhmm) continue;
        (byMinute.get(hhmm) ?? byMinute.set(hhmm, []).get(hhmm)!).push(vol(c));
      }
      // Median, not mean — one earnings-day open would otherwise define
      // "normal" for that minute for weeks.
      const typical = new Map<string, number>();
      for (const [hhmm, vs] of byMinute) {
        const sorted = [...vs].sort((a, b) => a - b);
        if (sorted.length >= 5) typical.set(hhmm, sorted[Math.floor(sorted.length / 2)]);
      }
      for (const c of cs) {
        const t = String(c.timestamp);
        const base = typical.get(t.length >= 16 ? t.slice(11, 16) : "");
        if (base && base > 0) m.set(t, vol(c) / base);
      }
      return m;
    }

    // DAILY: a trailing 20-session average is the right reference.
    const win: number[] = [];
    let sum = 0;
    for (const c of cs) {
      const v = vol(c);
      if (win.length >= 20 && sum > 0) m.set(String(c.timestamp), v / (sum / win.length));
      win.push(v);
      sum += v;
      if (win.length > 20) sum -= win.shift() as number;
    }
    return m;
  }, [series, resolution]);
  // Candles ⇄ Renko. Persisted per symbol-independent preference so flipping a
  // chart doesn't reset every time the component remounts.
  // Renko crosshair readout — a BRICK, not a session, so it carries the
  // brick's own edges plus the bar it formed on.
  const [brickHover, setBrickHover] = useState<
    { open: number; close: number; up: boolean; sourceTime: string } | null>(null);
  const [renko, setRenko] = useState<boolean>(() => {
    try { return localStorage.getItem("tp.chart.renko") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("tp.chart.renko", renko ? "1" : "0"); } catch { /* private mode */ }
  }, [renko]);

  // "Did we enter late?" — latest BUY fill vs the IDEAL signal onset (first
  // 5/32/50 cloud-cross at/before it). Drives a readout under the chart.
  const [entryTiming, setEntryTiming] = useState<{
    signalDate: string;
    signalPrice: number;
    entryDate: string;
    entryPrice: number;
    barsLate: number;
    extPct: number;
  } | null>(null);

  // Drag-resizable height: the user can drag the chart's bottom edge to make it
  // taller/shorter (CSS resize:vertical). A ResizeObserver mirrors the dragged
  // height into state so frequent re-renders (crosshair hover) don't snap it back
  // to the fixed prop — the root cause of "can't resize the graph".
  const outerRef = useRef<HTMLDivElement | null>(null);
  const [boxH, setBoxH] = useState<number>(height);
  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const h = Math.round(entries[0].contentRect.height);
      if (h > 0) setBoxH((prev) => (Math.abs(prev - h) > 1 ? h : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const windowDays = WINDOW_DAYS[timeframe] ?? 365;

  // ---- Fetch candles (padded with leading history) -----------------------
  useEffect(() => {
    if (!symbol) return;
    let live = true;
    setLoading(true);
    setErr(null);
    setSeries(null);
    setHover(null);
    // Canonicalise corporate-action renames for the DATA fetch (FB→META,
    // LB→BBWI). The broker holds the position under the OLD ticker (FB), but
    // fetching candles for the literal "FB" resolves to a DIFFERENT ~$44
    // instrument — charting the wrong company against a $607 META position.
    // Display keeps the original symbol; only the fetch is canonicalised.
    const fetchSymbol = canonicalSymbol(symbol);
    const isIntraday = resolution !== "1d";
    const fetchP = isIntraday
      // Deep IBKR store (ibkr_price_bars) — the harvested intraday data. Returns
      // the latest N bars; map to the same candle shape the chart already draws.
      ? api
          .ibkrBars({
            symbol: fetchSymbol,
            resolution,
            limit: Math.min(5000, Math.max(50, (BARS_PER_DAY[resolution] ?? 390) * windowDays)),
          })
          .then((r) => {
            if (!live) return;
            setSeries({
              symbol,
              interval: resolution,
              candles: r.bars.map((b) => ({
                timestamp: b.ts,
                open: b.open, high: b.high, low: b.low, close: b.close,
                volume: b.volume ?? 0,
              })),
            } as unknown as CandleSeries);
          })
      // Daily: Yahoo candles + leading pad so the Ichimoku cloud spans the window.
      : api
          .candles({
            symbol: fetchSymbol,
            provider: config.defaultProvider,
            interval: "1d",
            from: new Date(Date.now() - (windowDays + PAD_DAYS) * 24 * 3600 * 1000).toISOString().slice(0, 10),
            to: new Date().toISOString().slice(0, 10),
          })
          .then((s) => { if (live) setSeries(s); });
    fetchP
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => {
      live = false;
    };
  }, [symbol, windowDays, resolution]);

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

    // Renko bricks, sized at ATR(14) so one brick means "about a typical day's
    // range" whether the name trades at $8 or $800.
    const brickSize = renko ? atrBrickSize(candles) : 0;
    const bricks = renko ? renkoBricks(candles, brickSize) : [];

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

    // Candlesticks — or Renko bricks, which reuse the same series type (a
    // brick is drawn as a wickless candle).
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#1fc16b",
      downColor: "#ef4444",
      borderUpColor: "#1fc16b",
      borderDownColor: "#ef4444",
      wickUpColor: renko ? "transparent" : "#1fc16b",
      wickDownColor: renko ? "transparent" : "#ef4444",
      priceLineVisible: false,
    });
    if (renko) {
      // Bricks have no wicks: open/close are the brick edges and high/low are
      // set equal to them, so nothing implies an intra-brick excursion that
      // Renko does not model.
      candleSeries.setData(
        bricks.map((b) => ({
          time: b.time as UTCTimestamp,
          open: b.open,
          high: Math.max(b.open, b.close),
          low: Math.min(b.open, b.close),
          close: b.close,
        })),
      );
    } else {
      candleSeries.setData(
        candles.map((c) => ({
          time: toTime(c.timestamp),
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        })),
      );
    }

    // ── Volume + trader overlays (22 Aug 2026) ────────────────────────────
    // Volume: histogram in a bottom band of the SAME pane (classic terminal
    // layout), bars tinted by candle direction at low alpha so candles stay
    // dominant. Own overlay price scale so it never distorts the price axis.
    // Suppressed in Renko mode — a brick spans arbitrary time, so per-brick
    // volume would be a lie.
    if (!renko && ind.vol) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: "vol",
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volSeries.setData(candles.map((c) => ({
        time: toTime(c.timestamp),
        value: Number.isFinite(c.volume) ? Number(c.volume) : 0,
        color: c.close >= c.open ? "rgba(31,193,107,0.35)" : "rgba(239,68,68,0.35)",
      })));
    }
    // SMA 50/200 — the swing-trade trend context (and where the 200-SMA
    // regime floor sits). VWAP — the intraday value anchor, session-reset;
    // hidden on daily bars where it has no meaning.
    if (!renko && ind.sma50) {
      const s = lineSeries(chart, "#e0b341", 1);
      s.setData(smaPoints(candles, 50));
    }
    if (!renko && ind.sma200) {
      const s = lineSeries(chart, "#b78cff", 1);
      s.setData(smaPoints(candles, 200));
    }
    if (!renko && ind.vwap && resolution !== "1d") {
      const s = lineSeries(chart, "#4f8cff", 2);
      s.setData(vwapPoints(candles));
      // Session-anchored +/-1 and +/-2 sigma bands. Mean-reversion entries
      // live at the outer band, trend continuation holds inside the inner
      // one. Faint on purpose — they FRAME price, they are not signals.
      const bands = vwapBands(candles);
      const specs: Array<[{ time: UTCTimestamp; value: number }[], string]> = [
        [bands.upper1, "rgba(79,140,255,0.45)"], [bands.lower1, "rgba(79,140,255,0.45)"],
        [bands.upper2, "rgba(79,140,255,0.22)"], [bands.lower2, "rgba(79,140,255,0.22)"],
      ];
      for (const [pts, colour] of specs) {
        const b = lineSeries(chart, colour, 1, LineStyle.Dotted);
        b.setData(pts);
      }
    }
    // OBV on its own hidden scale — its magnitude has nothing to do with
    // price, so it shares the pane but never the axis. Read the SHAPE against
    // price: new price high without a new OBV high = the move is unfunded.
    if (!renko && ind.obv) {
      const o = chart.addSeries(LineSeries, {
        color: "#b78cff", lineWidth: 2, priceScaleId: "obv",
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      chart.priceScale("obv").applyOptions({ scaleMargins: { top: 0.05, bottom: 0.55 } });
      o.setData(obvPoints(candles));
    }

    // RSI(14) in its OWN pane (own 0-100 scale — never overlaid on price).
    if (!renko && ind.rsi) {
      const rsiSeries = chart.addSeries(LineSeries, {
        color: "#e0b341", lineWidth: 2,
        priceLineVisible: false, lastValueVisible: true,
        crosshairMarkerVisible: true,
      }, 1);
      rsiSeries.setData(rsiPoints(candles, 14));
      for (const lvl of [30, 70]) {
        rsiSeries.createPriceLine({
          price: lvl, color: "rgba(155,161,173,0.35)", lineWidth: 1,
          lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "",
        });
      }
      // Keep the RSI pane a band, not half the chart. Best-effort: the pane
      // API varies across 5.x minors.
      try {
        (chart.panes()[1] as unknown as { setHeight?: (h: number) => void })
          ?.setHeight?.(Math.max(70, Math.round(height * 0.2)));
      } catch { /* default pane sizing is acceptable */ }
    }

    // ── Support / Resistance levels (pivot-based) ─────────────────────────
    // Swing highs above price = resistance (red dotted); swing lows below =
    // support (green dotted). Drawn as horizontal reference lines so the chart's
    // structure — where price has repeatedly turned — reads at a glance. Nearest
    // few each side; near-equal pivots are clustered into one level.
    //
    // MEASURED 15 Aug 2026 (SR_LEVEL_STUDY_GATES_V1.md, 76,260 touch events):
    // these levels carry NO predictive edge — a randomly-placed line at a
    // comparable distance did marginally BETTER over the next 5 bars. They are
    // a drawing aid for reading structure, NOT evidence. Do not build a signal
    // on them without re-running that study on the timeframe in question.
    //
    // Suppressed in Renko mode: the bricks' x-axis is a synthetic sequence, and
    // the pivots are computed from time-ordered candles, so drawing them
    // together would imply a correspondence that does not exist.
    if (!renko) {
      const last = candles[candles.length - 1].close;
      const { highs, lows } = pivotLevels(candles);
      // Nearest FOUR each side (was 3) so the multi-level structure shows, not a
      // single ceiling/floor. Multi-touch levels are labelled "R×3"/"S×2" so the
      // strong lines (price turned there repeatedly) read at a glance.
      const resistances = highs.filter((v) => v.level > last).sort((a, b) => a.level - b.level).slice(0, 4);
      const supports = lows.filter((v) => v.level < last).sort((a, b) => b.level - a.level).slice(0, 4);
      resistances.forEach((r) =>
        candleSeries.createPriceLine({
          price: r.level, color: "rgba(239,68,68,0.5)", lineWidth: 1,
          lineStyle: LineStyle.Dotted, axisLabelVisible: true,
          title: r.touches > 1 ? `R×${r.touches}` : "R",
        }));
      supports.forEach((s) =>
        candleSeries.createPriceLine({
          price: s.level, color: "rgba(31,193,107,0.5)", lineWidth: 1,
          lineStyle: LineStyle.Dotted, axisLabelVisible: true,
          title: s.touches > 1 ? `S×${s.touches}` : "S",
        }));
    }

    // Entry-price reference line for a held symbol (cost basis at a glance).
    if (entryPrice != null && Number.isFinite(entryPrice) && entryPrice > 0) {
      candleSeries.createPriceLine({
        price: entryPrice,
        color: "#e0b341",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        // Append the open date when known, so the label reads e.g.
        // "Entry · 2 Jun" — price AND when the entry was taken.
        title: entryDate ? `Entry · ${fmtEntryDate(entryDate, true)}` : "Entry",
      });
    }

    // Trade markers: buy (▲ below bar) / sell (▼ above bar) at each fill,
    // labelled with the fill price. Built into a shared array so the IDEAL-entry
    // signal marker (below) can be added before we apply them once.
    const markers: SeriesMarker<Time>[] = (fills ?? [])
      .filter((f) => f.atUtc)
      .map((f) => ({
        time: toTime(f.atUtc),
        position: f.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
        color: f.side === "BUY" ? "#1fc16b" : "#ef4444",
        shape: f.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
        // price===0 on a fill is never a real execution (see
        // isUnconfirmedFill in SymbolValidationCard.tsx) — a reconciler-
        // manufactured settle, not a $0 trade. Show the side with no price
        // rather than a misleading "@0.00".
        text: `${f.side === "BUY" ? "B" : "S"}${f.price != null && f.price > 0 ? " @" + f.price.toFixed(2) : ""}`,
      }));

    // Ichimoku, computed on the FULL padded series.
    const ich = computeIchimoku(candles);

    // IDEAL ENTRY (signal onset): the strategy's raw long rule turning true —
    // close > cloud_high AND tenkan > kijun at 5/32/50. cloud_high[i] =
    // max(spanA,spanB) LANDING on bar i (the displaced cloud). We pair the LATEST
    // actual BUY with the onset at/before it → "signal fired here, you entered N
    // bars later, X% more extended" = the did-we-chase-the-top check.
    {
      const spanAAt = new Map(ich.spanA.map((p) => [p.time as number, p.value]));
      const spanBAt = new Map(ich.spanB.map((p) => [p.time as number, p.value]));
      const tkAt = new Map(ich.tenkan.map((p) => [p.time as number, p.value]));
      const kjAt = new Map(ich.kijun.map((p) => [p.time as number, p.value]));
      const onsets: { t: number; price: number; idx: number }[] = [];
      let wasLong = false;
      candles.forEach((c, idx) => {
        const ti = toTime(c.timestamp) as number;
        const sa = spanAAt.get(ti);
        const sb = spanBAt.get(ti);
        const tk = tkAt.get(ti);
        const kj = kjAt.get(ti);
        if (sa === undefined || sb === undefined || tk === undefined || kj === undefined) return;
        const isLong = c.close > Math.max(sa, sb) && tk > kj;
        if (isLong && !wasLong) onsets.push({ t: ti, price: c.close, idx });
        wasLong = isLong;
      });
      // f.price===0 is an unconfirmed reconciler-settled fill, not a real
      // entry price — excluded here too, so entry-timing math (extPct below)
      // can't compute a bogus "-100% vs signal" off a $0 execution.
      const buyFills = (fills ?? []).filter((f) => f.side === "BUY" && f.atUtc && f.price != null && f.price > 0);
      // ACTUAL ENTRY: prefer an OMS BUY fill; else fall back to the broker-reported
      // POSITION entry (open date + avg price). Seeded positions (most held names)
      // have no OMS fills but DO have a broker entry — so "entered late" still works
      // for them, not just OMS-traded names.
      const actualEntry: { atUtc: string; price: number } | null = buyFills.length
        ? (() => {
            const b = buyFills.reduce((a, b) => ((toTime(a.atUtc) as number) > (toTime(b.atUtc) as number) ? a : b));
            return { atUtc: b.atUtc, price: b.price! };
          })()
        : entryDate && entryPrice != null && entryPrice > 0
          ? { atUtc: entryDate, price: entryPrice }
          : null;
      if (actualEntry && onsets.length) {
        const buyTs = toTime(actualEntry.atUtc) as number;
        const prior = onsets.filter((o) => o.t <= buyTs);
        const sig = prior.length ? prior[prior.length - 1] : onsets[0];
        const buyIdx = candles.findIndex((c) => (toTime(c.timestamp) as number) >= buyTs);
        const barsLate = buyIdx >= 0 ? Math.max(0, buyIdx - sig.idx) : 0;
        const extPct = sig.price > 0 ? (actualEntry.price / sig.price - 1) * 100 : 0;
        markers.push({
          time: sig.t as UTCTimestamp,
          position: "aboveBar" as const,
          color: "#4f8cff",
          shape: "circle" as const,
          text: "signal",
        });
        setEntryTiming({
          signalDate: new Date(sig.t * 1000).toISOString().slice(0, 10),
          signalPrice: sig.price,
          entryDate: actualEntry.atUtc.slice(0, 10),
          entryPrice: actualEntry.price,
          barsLate,
          extPct,
        });
      } else {
        setEntryTiming(null);
      }
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    // Markers are keyed to REAL bar times; Renko's axis is a synthetic brick
    // sequence, so a fill would land on an arbitrary brick. Omitted rather
    // than drawn in the wrong place.
    if (markers.length && !renko) createSeriesMarkers(candleSeries, markers);

    // Ichimoku is a TIME-series construct — tenkan/kijun average over N BARS
    // and the cloud is displaced N bars forward. Renko bricks are not bars:
    // one session can emit five or none. Drawing the cloud over bricks would
    // produce a picture that looks like the strategy's signal and is not it —
    // and this chart exists specifically to VALIDATE that signal. Skipped.
    if (renko) {
      // WINDOW THE SAME WAY THE CANDLE VIEW DOES (fixed 16 Aug 2026).
      // This used to call fitContent(), which shows EVERY brick — including
      // those built from the 130-day Ichimoku lookback pad. So "3M" on the
      // candle chart showed Jun-Sep while "3M" on Renko reached back to
      // February, and the two views could not be compared as if they covered
      // the same period. The pad must still FEED the brick builder (Renko is
      // path-dependent — you cannot start a brick sequence mid-series and get
      // the same bricks), so bricks are built from the full series and only
      // the DISPLAY is clipped. Same split the candle path already uses.
      const lastBrickTs = bricks.length
        ? (bricks[bricks.length - 1].time as number)
        : (toTime(candles[candles.length - 1].timestamp) as number);
      const fromTsR = lastBrickTs - windowDays * 24 * 3600;
      const firstBrickTs = bricks.length ? (bricks[0].time as number) : fromTsR;
      if (bricks.length && firstBrickTs >= fromTsR) {
        // Fewer bricks than the window — a genuinely quiet stretch. Showing
        // them all is right; there is nothing to clip.
        chart.timeScale().fitContent();
      } else {
        chart.timeScale().setVisibleRange({
          from: fromTsR as Time, to: (lastBrickTs + 24 * 3600) as Time,
        });
      }

      // Hover readout. Without this the crosshair handler further down never
      // ran in Renko mode, so the header silently showed the last CANDLE's
      // OHLC no matter where you pointed. A brick is not a session, so report
      // the brick's own open/close and the bar it formed on rather than
      // pretending a session's OHLC belongs to it.
      chart.subscribeCrosshairMove((pt) => {
        if (!pt?.time) { setBrickHover(null); return; }
        const t = pt.time as number;
        const b = bricks.find((x) => (x.time as number) === t);
        setBrickHover(b ? {
          open: b.open, close: b.close, up: b.up,
          sourceTime: new Date(b.sourceTime * 1000).toISOString().slice(0, 10),
        } : null);
      });

      // No cloud overlay in this mode, so no ResizeObserver to tear down —
      // just the chart itself. (`ro` below is declared after this point.)
      return () => { chart.remove(); chartRef.current = null; };
    }

    // Ichimoku overlay is now a TOGGLE, default OFF (owner, 22 Aug 2026:
    // "the ich cloud is always misleading" — and the platform's own studies
    // agree: S/R lines zero edge, the TK construct cuts winners, cloud-based
    // entry filters cost 43% of profit). It exists to visualize what the
    // ichimoku_equity strategy SEES, on demand — it is not evidence.
    if (ind.ich) {
      const tenkan = lineSeries(chart, "#4f8cff", 1.5);
      tenkan.setData(ich.tenkan);
      const kijun = lineSeries(chart, "#d4793b", 1.5);
      kijun.setData(ich.kijun);
    }
    const spanA = ind.ich ? lineSeries(chart, "rgba(31,193,107,0.9)", 1, LineStyle.Solid) : null;
    spanA?.setData(ich.spanA);
    const spanB = ind.ich ? lineSeries(chart, "rgba(239,68,68,0.9)", 1, LineStyle.Solid) : null;
    spanB?.setData(ich.spanB);
    if (ind.ich) {
      const chikou = lineSeries(chart, "rgba(155,110,255,0.85)", 1, LineStyle.Solid);
      chikou.setData(ich.chikou);
    }

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
      // Overlay off → make sure a previously painted cloud is CLEARED, not
      // left as a stale ghost under the fresh chart.
      if (!ind.ich || !spanARef || !spanBRef) {
        canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
        return;
      }
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
  }, [series, height, windowDays, entryPrice, entryDate, fills, renko, ind, resolution]);

  // ---- States ------------------------------------------------------------
  const noData = !loading && !err && (series?.candles?.length ?? 0) === 0;

  // Fail-loud staleness: if the LAST bar isn't current, SAY it on the chart —
  // never let stale candles read as live (the GOOGL case: daily 2 sessions
  // behind through a -7% gap, drawn as if it were the market now).
  //
  // MEASURED IN SESSIONS, NOT CALENDAR DAYS (fixed 17 Aug 2026). The old check
  // used a 3-calendar-day tolerance, so on Monday — with Friday's close being
  // the LATEST SETTLED DAILY BAR THAT CAN EXIST — it fired "STALE DATA … 3 days
  // old. Do not trade off this chart." That is a false alarm on the single most
  // consequential message this component prints, and it is the FOURTH instance
  // of the same underlying mistake this weekend (readiness lanes, the
  // dead-process check, the bar-cache warnings, now this): a freshness test that
  // does not know the market is shut at weekends will cry wolf every Monday, and
  // an alarm that cries wolf is worse than no alarm.
  const _lastCandle = series?.candles?.[(series?.candles?.length ?? 0) - 1];
  const _lastMs = _lastCandle ? Date.parse(String(_lastCandle.timestamp).replace(" ", "T")) : NaN;
  const _ageDays = Number.isFinite(_lastMs) ? (Date.now() - _lastMs) / 86_400_000 : null;
  // Count only weekdays between the last bar and now — a Sat/Sun gap is not
  // staleness, it is the calendar. (US holidays are not modelled; a holiday
  // Monday will read one session behind, which is the safe direction.)
  const _sessionsBehind = (() => {
    if (!Number.isFinite(_lastMs)) return null;
    let n = 0;
    const cur = new Date(_lastMs);
    cur.setUTCHours(0, 0, 0, 0);
    const today = new Date();
    today.setUTCHours(0, 0, 0, 0);
    while (cur < today) {
      cur.setUTCDate(cur.getUTCDate() + 1);
      const d = cur.getUTCDay();
      if (d !== 0 && d !== 6) n += 1;
    }
    return n;
  })();
  // Daily: one session behind is NORMAL — today's bar does not settle until the
  // close. Two or more means a genuinely missed harvest. Intraday must be
  // same-day.
  const isStale = resolution === "1d"
    ? (_sessionsBehind !== null && _sessionsBehind >= 2)
    : (_ageDays !== null && _ageDays > 1);
  const lastBarDate = _lastCandle ? String(_lastCandle.timestamp).slice(0, 10) : "";

  return (
    <div>
      {/* Candles ⇄ Renko. Pills, not a dropdown — the active mode has to be
          visible without opening anything, because Renko and candles are
          different enough that mistaking one for the other matters. */}
      <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 4 }}>
        {([["Candles", false], ["Renko", true]] as const).map(([label, val]) => (
          <button
            key={label}
            onClick={() => setRenko(val)}
            title={val
              ? "Renko: a brick is drawn only when price travels one ATR(14), so time and small moves are DISCARDED. The x-axis is a brick sequence, not a clock. Good for seeing trend structure; it is a way of looking at price, not evidence about it — a Renko chart of pure noise still looks like orderly trends. Ichimoku, trade markers and S/R lines are hidden in this mode because they are time-series constructs and would be drawn in the wrong place."
              : "Standard candlesticks on real bar times, with the Ichimoku cloud at the STRATEGY's 5·32·50 parameters."}
            style={{
              fontSize: 11, padding: "2px 10px", borderRadius: 999, cursor: "pointer",
              border: `1px solid ${renko === val ? "var(--accent, #4f8cff)" : "var(--border)"}`,
              background: renko === val ? "color-mix(in srgb, var(--accent, #4f8cff) 18%, transparent)" : "transparent",
              color: renko === val ? "var(--text)" : "var(--text-muted)",
              fontWeight: renko === val ? 700 : 400,
            }}
          >
            {label}
          </button>
        ))}
        {renko && (
          <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>
            brick = ATR(14) · time discarded · no Ichimoku/markers
          </span>
        )}
        {/* Indicator toggles — doubles as the legend: each pill wears its
            series' color when active, so identity is never color-alone
            guesswork on the plot. Hidden in Renko (time-series constructs). */}
        {!renko && (
          <span style={{ display: "flex", gap: 4, marginLeft: "auto", flexWrap: "wrap" }}>
            {([
              ["vol", "Vol", "#9ba1ad", "Volume — bottom band, tinted by candle direction", true],
              ["sma50", "SMA50", "#e0b341", "50-bar simple moving average — swing trend", true],
              ["sma200", "SMA200", "#b78cff", "200-bar SMA — the regime floor the backtests gate on", true],
              ["vwap", "VWAP", "#4f8cff", "Session-anchored VWAP — the intraday value line (intraday resolutions only)", resolution !== "1d"],
              ["rsi", "RSI", "#e0b341", "RSI(14) in its own pane with 30/70 bands", true],
              ["obv", "OBV", "#b78cff", "On-Balance Volume — cumulative signed volume. Compare its SHAPE to price: a new price high without a new OBV high means the move is not funded (MU rallied +30.8% while OBV sat at 71% of its peak).", true],
              ["ich", "Cloud", "#1fc16b", "Ichimoku overlay at the STRATEGY's 5·32·50 params — shows what ichimoku_equity sees. Off by default: the platform's own studies found no standalone edge in these lines, and the displaced cloud lags fast moves badly.", true],
            ] as const).filter(([, , , , show]) => show).map(([key, label, color, tip]) => (
              <button
                key={key}
                onClick={() => toggleInd(key)}
                title={tip}
                style={{
                  fontSize: 10, padding: "1px 8px", borderRadius: 999, cursor: "pointer",
                  border: `1px solid ${ind[key] ? color : "var(--border)"}`,
                  background: ind[key] ? `color-mix(in srgb, ${color} 16%, transparent)` : "transparent",
                  color: ind[key] ? "var(--text)" : "var(--text-muted)",
                  fontWeight: ind[key] ? 700 : 400,
                }}
              >
                {label}
              </button>
            ))}
          </span>
        )}
      </div>

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
        {(() => {
          // RENKO: report the BRICK, not a session. The header used to fall
          // through to the last candle's OHLC while the chart displayed bricks,
          // so it read as though those four numbers described what was drawn.
          // They never did — a brick has only two edges, and it may span part
          // of a session or several.
          if (renko && brickHover) {
            const b = brickHover;
            return (
              <>
                <span style={{ fontWeight: 700, color: b.up ? "#1fc16b" : "#ef4444" }}>
                  BRICK {b.up ? "▲" : "▼"}
                </span>
                <span>open {b.open.toFixed(2)}</span>
                <span>close {b.close.toFixed(2)}</span>
                <span style={{ color: "var(--text-muted)" }}>
                  formed on {b.sourceTime} · a brick is one ATR(14) move, not a session
                </span>
              </>
            );
          }
          // Show the hovered bar; otherwise show the LATEST bar (never generic
          // help text) so it's unambiguous which session the chart ends on — the
          // "why isn't the 24th here?" confusion was that only a hover revealed a
          // date, and that date was wherever the cursor sat.
          const bar = hover ?? _lastCandle;
          if (!bar) {
            return renko
              ? <span>Renko · ATR(14) bricks — x-axis is a brick SEQUENCE, not time</span>
              : <span>Candles · Ichimoku cloud (5·32·50) — hover for OHLC · scroll to zoom</span>;
          }
          return (
            <>
              <span style={{ fontWeight: 700, color: isStale ? "#ef4444" : "var(--text-dim)" }}>
                {renko ? "LATEST SESSION " : hover ? "" : "LATEST "}{bar.timestamp.slice(0, 10)}
              </span>
              {adrBudget && (
                <span style={{ color: adrBudget.used >= 1.5 ? "#ef4444"
                               : adrBudget.used >= 1.0 ? "#e0b341" : "var(--text-muted)",
                               fontWeight: adrBudget.used >= 1.0 ? 700 : 400 }}
                  title={`This session has covered ${adrBudget.used.toFixed(2)}x the average daily range of the last 20 sessions. Past ~1.5x the move has largely happened: breakout odds collapse and fade odds rise. Under ~0.5x there is room left in the day.`}>
                  ADR {adrBudget.used.toFixed(2)}x
                </span>
              )}
              {regime && (
                <span style={{ color: regime.tone, fontWeight: 700 }}
                  title={`Kaufman Efficiency Ratio(20) = ${regime.er.toFixed(3)} (0 = pure chop, 1 = pure trend; <0.30 is chop) · ADX(14) = ${regime.adx.toFixed(1)} (<20 = no trend worth trading). Trend-family signals — moving-average crosses, cloud position, breakouts — only carry information in a trending regime. Neither of these is a price forecast; they say whether the OTHER indicators mean anything right now.`}>
                  {regime.label}
                </span>
              )}
              <span>O {fmt(bar.open, ccy)}</span>
              <span>H {fmt(bar.high, ccy)}</span>
              <span>L {fmt(bar.low, ccy)}</span>
              <span style={{ color: bar.close >= bar.open ? "#1fc16b" : "#ef4444" }}>
                C {fmt(bar.close, ccy)}
              </span>
              <span>
                Vol {Number.isFinite(bar.volume) ? bar.volume.toLocaleString() : "—"}
                {(() => {
                  const rv = rvolByTs.get(String(bar.timestamp));
                  if (rv === undefined) return null;
                  // ≥1.5× average = real participation (breakout credible);
                  // ≤0.5× = thin (a poke through a level is suspect).
                  const tone = rv >= 1.5 ? "#1fc16b" : rv <= 0.5 ? "#e0b341" : "var(--text-muted)";
                  return (
                    <span style={{ color: tone, fontWeight: rv >= 1.5 || rv <= 0.5 ? 700 : 400 }}
                      title="This bar's volume vs its trailing 20-bar average — ×1.5+ = real participation behind the move; ×0.5- = thin, treat a level break as suspect">
                      {" "}(×{rv.toFixed(1)} avg)
                    </span>
                  );
                })()}
              </span>
              {!hover && <span style={{ color: "var(--text-muted)" }}>· hover for any bar · scroll to zoom</span>}
            </>
          );
        })()}
      </div>

      {/* FAIL-LOUD staleness banner — never let stale candles read as current. */}
      {isStale && !loading && (
        <div style={{
          fontSize: 11, fontWeight: 700, color: "#fff", background: "rgba(239,68,68,0.85)",
          border: "1px solid #ef4444", borderRadius: 5, padding: "4px 9px", marginBottom: 4,
        }}>
          ⚠ STALE DATA — last {resolution} bar is {lastBarDate}
          {resolution === "1d"
            ? ` (${_sessionsBehind} trading sessions behind)`
            : ` (${Math.floor(_ageDays as number)} days old)`}.
          The latest price/move is NOT shown here. Do not trade off this chart.
        </div>
      )}

      <div
        ref={outerRef}
        style={{
          position: "relative",
          width: "100%",
          height: boxH,
          resize: "vertical",
          overflow: "hidden",
          minHeight: 220,
          maxHeight: "85vh",
        }}
      >
        {/* The chart mounts here; the canvas overlay paints the cloud band on
            top, pointer-events:none so it never blocks the crosshair. autoSize
            makes the chart follow this container as the user drag-resizes it. */}
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

      {entryTiming && (
        <div
          style={{
            marginTop: 6,
            fontSize: 11,
            fontFamily: "monospace",
            padding: "5px 8px",
            borderRadius: 4,
            border: "1px solid #1b2233",
            background: entryTiming.barsLate > 5 || entryTiming.extPct > 8 ? "rgba(239,68,68,0.08)" : "rgba(31,193,107,0.06)",
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
            alignItems: "baseline",
          }}
          title="Latest entry vs the ideal signal onset (first 5/32/50 cloud-cross before it)"
        >
          <span style={{ color: "#4f8cff", fontWeight: 700 }}>Entry timing</span>
          <span>
            signal {entryTiming.signalDate} @{entryTiming.signalPrice.toFixed(2)} → entered{" "}
            {entryTiming.entryDate} @{entryTiming.entryPrice.toFixed(2)}
          </span>
          <span style={{ color: entryTiming.barsLate > 5 ? "#ef4444" : "#1fc16b", fontWeight: 700 }}>
            {entryTiming.barsLate === 0 ? "on the signal" : `${entryTiming.barsLate} bars ${entryTiming.barsLate > 5 ? "LATE" : "late"}`}
          </span>
          <span style={{ color: entryTiming.extPct > 8 ? "#ef4444" : "var(--text-muted)" }}>
            {entryTiming.extPct >= 0 ? "+" : ""}
            {entryTiming.extPct.toFixed(1)}% vs signal price
            {entryTiming.extPct > 8 ? " (chased)" : ""}
          </span>
        </div>
      )}

      <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
        Daily candles (green up / red down). Ichimoku Kinko Hyo at the STRATEGY's
        params: Tenkan-sen (5, blue) · Kijun-sen (32, orange) · the cloud is the
        band between Senkou Span A &amp; B (green when A&gt;B, red when below)
        shifted 32 bars forward · Chikou span (close shifted 32 back, purple) —
        so this matches the ichimoku_equity signal, not textbook 9/26/52.
        Indicators are computed on ~{PAD_DAYS} extra leading bars so the cloud
        spans the whole window. No data is fabricated — points without enough
        history are omitted.
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

/** Pivot-based support/resistance from the candle highs/lows: a bar is a swing
 * HIGH (resistance) if its high is the max over ±win bars, a swing LOW (support)
 * if its low is the min. Near-equal levels are clustered so we don't draw ten
 * lines a cent apart. Pure — scans the most recent `maxScan` bars only. */
// A clustered level + how many swing pivots fell in it. A level price turned at
// REPEATEDLY (higher `touches`) is stronger structure than a one-off spike.
type SRLevel = { level: number; touches: number };

function pivotLevels(
  candles: Candle[], win = 5, clusterPct = 0.005, maxScan = 240,
): { highs: SRLevel[]; lows: SRLevel[] } {
  const c = candles.slice(-maxScan);
  const rawHi: number[] = [];
  const rawLo: number[] = [];
  for (let i = win; i < c.length - win; i++) {
    let isHi = true;
    let isLo = true;
    for (let j = i - win; j <= i + win; j++) {
      if (c[j].high > c[i].high) isHi = false;
      if (c[j].low < c[i].low) isLo = false;
    }
    if (isHi) rawHi.push(c[i].high);
    if (isLo) rawLo.push(c[i].low);
  }
  // Collapse near-equal pivots (within clusterPct — tightened to 0.5% so distinct
  // shelves on a range-bound name like WBD, e.g. 27.35 vs 27.62, stay SEPARATE
  // instead of merging into one line). Track touch count as pivots fold in.
  const collapse = (xs: number[]): SRLevel[] => {
    const out: SRLevel[] = [];
    for (const v of [...xs].sort((a, b) => a - b)) {
      const prev = out[out.length - 1];
      if (prev === undefined || Math.abs(v - prev.level) / v > clusterPct) {
        out.push({ level: v, touches: 1 });
      } else {
        prev.level = (prev.level * prev.touches + v) / (prev.touches + 1);
        prev.touches += 1;
      }
    }
    return out;
  };
  return { highs: collapse(rawHi), lows: collapse(rawLo) };
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

// ── Trader indicators (22 Aug 2026 — owner: "the chart is not very usable"
// for intraday/swing). Pure functions over the candle array; each returns
// lightweight-charts points. Kept dependency-free and O(n).

/** Simple moving average of closes; first n-1 bars omitted (no fake ramp). */
function smaPoints(candles: Candle[], n: number): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= n) sum -= candles[i - n].close;
    if (i >= n - 1) out.push({ time: toTime(candles[i].timestamp), value: sum / n });
  }
  return out;
}

/** Session-anchored VWAP (intraday): Σ(typical·vol)/Σvol, reset each UTC day.
 *  The intraday reference line — above VWAP longs are "paying up", below they
 *  are buying value. Meaningless on daily bars (callers gate on resolution). */
function vwapPoints(candles: Candle[]): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  let day = "";
  let pv = 0;
  let vv = 0;
  for (const c of candles) {
    const d = String(c.timestamp).slice(0, 10);
    if (d !== day) { day = d; pv = 0; vv = 0; }
    const vol = Number.isFinite(c.volume) ? Number(c.volume) : 0;
    const typical = (c.high + c.low + c.close) / 3;
    pv += typical * vol;
    vv += vol;
    if (vv > 0) out.push({ time: toTime(c.timestamp), value: pv / vv });
  }
  return out;
}

/** Kaufman Efficiency Ratio(n) — net move ÷ total path travelled.
 *  0 = pure chop, 1 = pure trend. The missing test in the whole stack:
 *  cloud position, moving averages and breakout rules all encode "which way
 *  is the trend", and none of them asks whether a trend EXISTS. Measured on
 *  MU and WCC this weekend: 0.06 and 0.09 — price travelled an enormous
 *  distance to end up where it started, and trend-family signals fired in
 *  both. Below ~0.30 is chop. */
function efficiencyRatio(candles: Candle[], n = 20): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (let i = n; i < candles.length; i++) {
    const net = Math.abs(candles[i].close - candles[i - n].close);
    let path = 0;
    for (let j = i - n + 1; j <= i; j++) path += Math.abs(candles[j].close - candles[j - 1].close);
    if (path > 0) out.push({ time: toTime(candles[i].timestamp), value: net / path });
  }
  return out;
}

/** Wilder ADX(n) — trend STRENGTH, direction-agnostic. Below 20 conventionally
 *  means no trend worth trading; MU printed 12.0 and WCC 13.8 on signals that
 *  fired anyway. Pairs with the Efficiency Ratio: ER measures how straight the
 *  path was, ADX how persistent the directional pressure. */
function adxPoints(candles: Candle[], n = 14): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  if (candles.length <= n * 2) return out;
  let tr = 0, plus = 0, minus = 0;
  const dxs: number[] = [];
  let adx = 0;
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i], p = candles[i - 1];
    const trueRange = Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close));
    const up = c.high - p.high, down = p.low - c.low;
    const pDM = up > down && up > 0 ? up : 0;
    const mDM = down > up && down > 0 ? down : 0;
    if (i <= n) { tr += trueRange; plus += pDM; minus += mDM; continue; }
    // Wilder smoothing
    tr = tr - tr / n + trueRange;
    plus = plus - plus / n + pDM;
    minus = minus - minus / n + mDM;
    if (tr === 0) continue;
    const pDI = (plus / tr) * 100, mDI = (minus / tr) * 100;
    const sum = pDI + mDI;
    const dx = sum === 0 ? 0 : (Math.abs(pDI - mDI) / sum) * 100;
    dxs.push(dx);
    if (dxs.length === n) { adx = dxs.reduce((a, b) => a + b, 0) / n; }
    else if (dxs.length > n) { adx = (adx * (n - 1) + dx) / n; }
    if (dxs.length >= n) out.push({ time: toTime(candles[i].timestamp), value: adx });
  }
  return out;
}



/** On-Balance Volume — cumulative signed volume. Orthogonal to price, which
 *  is the point: it answers "is anyone actually behind this move". MU rallied
 *  +30.8% off the 29 Jul low while OBV sat at 71% of its own peak and topped
 *  first — the divergence you can see by eye but the stack could not express. */
function obvPoints(candles: Candle[]): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  let obv = 0;
  // Accumulating across a units change produces one enormous artificial step
  // and every reading after it is offset by it. Plot only the segment after
  // the break — a shorter honest series beats a long wrong one.
  const brk = volumeScaleBreak(candles);
  const from = brk > 0 ? brk : 1;
  for (let i = from; i < candles.length; i++) {
    const v = Number.isFinite(candles[i].volume) ? Number(candles[i].volume) : 0;
    if (candles[i].close > candles[i - 1].close) obv += v;
    else if (candles[i].close < candles[i - 1].close) obv -= v;
    out.push({ time: toTime(candles[i].timestamp), value: obv });
  }
  return out;
}

/** Session-anchored VWAP +/-1 and +/-2 sigma. Sigma is the volume-weighted
 *  dispersion of typical price about the running VWAP, reset each session, so
 *  the bands widen with genuine two-way trade rather than with elapsed time. */
function vwapBands(candles: Candle[]): {
  upper1: { time: UTCTimestamp; value: number }[];
  lower1: { time: UTCTimestamp; value: number }[];
  upper2: { time: UTCTimestamp; value: number }[];
  lower2: { time: UTCTimestamp; value: number }[];
} {
  const upper1: { time: UTCTimestamp; value: number }[] = [];
  const lower1: { time: UTCTimestamp; value: number }[] = [];
  const upper2: { time: UTCTimestamp; value: number }[] = [];
  const lower2: { time: UTCTimestamp; value: number }[] = [];
  let day = "", pv = 0, vv = 0, pv2 = 0;
  for (const c of candles) {
    const d = String(c.timestamp).slice(0, 10);
    if (d !== day) { day = d; pv = 0; vv = 0; pv2 = 0; }
    const v = Number.isFinite(c.volume) ? Number(c.volume) : 0;
    const tp = (c.high + c.low + c.close) / 3;
    pv += tp * v; vv += v; pv2 += tp * tp * v;
    if (vv <= 0) continue;
    const mean = pv / vv;
    const sd = Math.sqrt(Math.max(0, pv2 / vv - mean * mean));
    const t = toTime(c.timestamp);
    upper1.push({ time: t, value: mean + sd });
    lower1.push({ time: t, value: mean - sd });
    upper2.push({ time: t, value: mean + 2 * sd });
    lower2.push({ time: t, value: mean - 2 * sd });
  }
  return { upper1, lower1, upper2, lower2 };
}

/** Wilder RSI(n). First n bars omitted. */
function rsiPoints(candles: Candle[], n = 14): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  if (candles.length <= n) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= n; i++) {
    const d = candles[i].close - candles[i - 1].close;
    if (d >= 0) gain += d; else loss -= d;
  }
  let avgG = gain / n;
  let avgL = loss / n;
  const push = (i: number) => out.push({
    time: toTime(candles[i].timestamp),
    value: avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL),
  });
  push(n);
  for (let i = n + 1; i < candles.length; i++) {
    const d = candles[i].close - candles[i - 1].close;
    avgG = (avgG * (n - 1) + Math.max(d, 0)) / n;
    avgL = (avgL * (n - 1) + Math.max(-d, 0)) / n;
    push(i);
  }
  return out;
}

/** ISO date (or datetime) → UTC seconds Time for a daily series. */
function toTime(iso: string): UTCTimestamp {
  // Preserve the time-of-day when the timestamp carries one (intraday 1m/5m
  // bars) — truncating to the date collapsed every intraday bar of a day onto
  // one point. Date-only strings (daily bars) still resolve to 00:00:00Z, so
  // daily behaviour is unchanged.
  const hasTime = iso.includes("T") || iso.includes(" ");
  const ms = hasTime
    ? Date.parse(iso.replace(" ", "T"))
    : Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  return Math.floor(ms / 1000) as UTCTimestamp;
}

function fmt(v: number | null | undefined, ccy?: string | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const prefix = ccy === "USD" ? "$" : ccy === "GBP" ? "£" : ccy === "EUR" ? "€" : "";
  return `${prefix}${v.toFixed(2)}`;
}
