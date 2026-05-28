import {
  Area,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HelpDiagramKind } from "../docs/help-content";

/**
 * Inline recharts demos used by the Help page to visualise each
 * concept. Data is synthetic — designed to teach the shape, not to
 * claim any real market period. Visual learners get a chart; readers
 * get the markdown body underneath.
 *
 * Brand semantics: green = up / pass, red = down / fail / overbought,
 * amber = warn / oversold zone. Matches Compare + email digest.
 */
const COLOUR_UP = "#1fc16b";
const COLOUR_DOWN = "#e2483a";
const COLOUR_NEUTRAL = "#e8a23a";
const COLOUR_TEXT_DIM = "#9ba1ad";
const COLOUR_LINE_FAST = "#4f8cff";
const COLOUR_LINE_SLOW = "#9b6eff";

const PANEL_HEIGHT = 220;

export function StrategyDiagram({ kind }: { kind: HelpDiagramKind }) {
  switch (kind) {
    case "sma_crossover": return <SmaCrossover />;
    case "rsi_bands": return <RsiBands />;
    case "macd_histogram": return <MacdHistogram />;
    case "donchian_breakout": return <DonchianBreakout />;
    case "range_position": return <RangePosition />;
    case "return_histogram": return <ReturnHistogram />;
  }
}

// ---------------------------------------------------------------------------
// SMA crossover — fast vs slow with golden-cross + death-cross markers
// ---------------------------------------------------------------------------

function smaSeries(): { i: number; price: number; fast: number; slow: number }[] {
  // Synthetic: trough, climb to peak, drift down. Designed so the
  // fast SMA crosses the slow SMA twice — once up (golden), once
  // down (death) — like every textbook example.
  const out: { i: number; price: number; fast: number; slow: number }[] = [];
  const n = 60;
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    const cycle = Math.sin(t * Math.PI * 1.4 - 0.4) * 18 + 100;
    const noise = (Math.sin(i * 0.7) + Math.cos(i * 0.4)) * 1.2;
    out.push({ i, price: +(cycle + noise).toFixed(2), fast: 0, slow: 0 });
  }
  // Compute SMAs over the synthetic series.
  const sma = (window: number) => (idx: number) => {
    const start = Math.max(0, idx - window + 1);
    const slice = out.slice(start, idx + 1).map((r) => r.price);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  };
  const fastFn = sma(8);
  const slowFn = sma(20);
  out.forEach((r, idx) => {
    r.fast = +fastFn(idx).toFixed(2);
    r.slow = +slowFn(idx).toFixed(2);
  });
  return out;
}

function SmaCrossover() {
  const data = smaSeries();
  // Find the first golden cross (fast crossing above slow) and the
  // first death cross after it. Marked on the chart so the reader
  // sees what "crossover" actually looks like.
  let golden: number | null = null;
  let death: number | null = null;
  for (let i = 1; i < data.length; i++) {
    const prev = data[i - 1];
    const cur = data[i];
    if (golden === null && prev.fast <= prev.slow && cur.fast > cur.slow) {
      golden = i;
    } else if (golden !== null && death === null && prev.fast >= prev.slow && cur.fast < cur.slow) {
      death = i;
    }
  }
  return (
    <DiagramFrame caption="Synthetic — fast (8) crosses slow (20) up = golden cross; down = death cross">
      <ResponsiveContainer width="100%" height={PANEL_HEIGHT}>
        <LineChart data={data} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
          <XAxis dataKey="i" hide />
          <YAxis tick={{ fill: COLOUR_TEXT_DIM, fontSize: 11 }} />
          <Tooltip {...tooltipProps} />
          <Legend {...legendProps} />
          <Line type="monotone" dataKey="price" stroke="#cbd2dc" strokeWidth={1.2} dot={false} name="Price" />
          <Line type="monotone" dataKey="fast" stroke={COLOUR_LINE_FAST} strokeWidth={1.8} dot={false} name="Fast SMA (8)" />
          <Line type="monotone" dataKey="slow" stroke={COLOUR_LINE_SLOW} strokeWidth={1.8} dot={false} name="Slow SMA (20)" />
          {golden !== null && (
            <ReferenceDot x={golden} y={data[golden].fast} r={6} fill={COLOUR_UP} stroke="white" label={{ value: "Golden ✕", position: "top", fill: COLOUR_UP, fontSize: 11 }} />
          )}
          {death !== null && (
            <ReferenceDot x={death} y={data[death].fast} r={6} fill={COLOUR_DOWN} stroke="white" label={{ value: "Death ✕", position: "top", fill: COLOUR_DOWN, fontSize: 11 }} />
          )}
        </LineChart>
      </ResponsiveContainer>
    </DiagramFrame>
  );
}

// ---------------------------------------------------------------------------
// RSI bands — series with 30/70 horizontal zones
// ---------------------------------------------------------------------------

function rsiSeries(): { i: number; rsi: number }[] {
  // Synthetic RSI sweeping through both bands so the reader sees
  // each zone fire. Smooth so the eye reads the regime, not noise.
  const out: { i: number; rsi: number }[] = [];
  for (let i = 0; i < 60; i++) {
    const t = i / 59;
    const v = 50 + 28 * Math.sin(t * Math.PI * 2 - 0.6);
    out.push({ i, rsi: +v.toFixed(1) });
  }
  return out;
}

function RsiBands() {
  const data = rsiSeries();
  return (
    <DiagramFrame caption="RSI ≥ 70 = overbought (red), ≤ 30 = oversold (amber)">
      <ResponsiveContainer width="100%" height={PANEL_HEIGHT}>
        <LineChart data={data} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
          <XAxis dataKey="i" hide />
          <YAxis domain={[0, 100]} tick={{ fill: COLOUR_TEXT_DIM, fontSize: 11 }} ticks={[0, 30, 50, 70, 100]} />
          <Tooltip {...tooltipProps} />
          <ReferenceArea y1={70} y2={100} fill={COLOUR_DOWN} fillOpacity={0.10} />
          <ReferenceArea y1={0} y2={30} fill={COLOUR_NEUTRAL} fillOpacity={0.10} />
          <ReferenceLine y={70} stroke={COLOUR_DOWN} strokeDasharray="4 4" label={{ value: "70 — overbought", position: "right", fill: COLOUR_DOWN, fontSize: 10 }} />
          <ReferenceLine y={30} stroke={COLOUR_NEUTRAL} strokeDasharray="4 4" label={{ value: "30 — oversold", position: "right", fill: COLOUR_NEUTRAL, fontSize: 10 }} />
          <Line type="monotone" dataKey="rsi" stroke={COLOUR_LINE_FAST} strokeWidth={2} dot={false} name="RSI(14)" />
        </LineChart>
      </ResponsiveContainer>
    </DiagramFrame>
  );
}

// ---------------------------------------------------------------------------
// MACD — line + signal + histogram (the bar chart the user asked for)
// ---------------------------------------------------------------------------

function macdSeries(): { i: number; macd: number; signal: number; hist: number }[] {
  // Build a smooth MACD that crosses its signal line a couple of
  // times so the histogram visibly flips between green and red bars.
  const out: { i: number; macd: number; signal: number; hist: number }[] = [];
  for (let i = 0; i < 50; i++) {
    const t = i / 49;
    const macd = 1.4 * Math.sin(t * Math.PI * 2.4 - 0.5);
    const signal = 1.1 * Math.sin(t * Math.PI * 2.4 - 0.85);  // lagged
    out.push({
      i,
      macd: +macd.toFixed(3),
      signal: +signal.toFixed(3),
      hist: +(macd - signal).toFixed(3),
    });
  }
  return out;
}

function MacdHistogram() {
  const data = macdSeries();
  return (
    <DiagramFrame caption="Histogram = MACD − Signal. Positive (green) = bullish, negative (red) = bearish">
      <ResponsiveContainer width="100%" height={PANEL_HEIGHT}>
        <ComposedChart data={data} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
          <XAxis dataKey="i" hide />
          <YAxis tick={{ fill: COLOUR_TEXT_DIM, fontSize: 11 }} />
          <Tooltip {...tooltipProps} />
          <Legend {...legendProps} />
          <ReferenceLine y={0} stroke={COLOUR_TEXT_DIM} strokeOpacity={0.5} />
          {/* Histogram first so the lines render on top. Per-bar
              colour via the `fill` prop on each Bar shape — recharts
              doesn't support data-driven colours via `fill` alone, so
              we use Cell internally below. */}
          <Bar dataKey="hist" name="Histogram" barSize={6}>
            {data.map((d, idx) => (
              <Cell key={idx} fill={d.hist >= 0 ? COLOUR_UP : COLOUR_DOWN} />
            ))}
          </Bar>
          <Line type="monotone" dataKey="macd" stroke={COLOUR_LINE_FAST} strokeWidth={2} dot={false} name="MACD" />
          <Line type="monotone" dataKey="signal" stroke={COLOUR_LINE_SLOW} strokeWidth={2} dot={false} name="Signal" />
        </ComposedChart>
      </ResponsiveContainer>
    </DiagramFrame>
  );
}

// ---------------------------------------------------------------------------
// Donchian breakout — price + upper/lower channel + breakout marker
// ---------------------------------------------------------------------------

function donchianSeries(): { i: number; price: number; upper: number; lower: number }[] {
  const out: { i: number; price: number; upper: number; lower: number }[] = [];
  const n = 60;
  for (let i = 0; i < n; i++) {
    // Sideways → breakout up at i ≈ 45
    let p: number;
    if (i < 45) {
      p = 100 + Math.sin(i * 0.7) * 3 + (Math.cos(i * 0.4)) * 1.5;
    } else {
      p = 100 + (i - 44) * 1.6 + (Math.sin(i * 0.7)) * 1.0;
    }
    out.push({ i, price: +p.toFixed(2), upper: 0, lower: 0 });
  }
  // 20-bar Donchian channel — max/min of trailing 20 bars.
  const lookback = 20;
  out.forEach((row, idx) => {
    const start = Math.max(0, idx - lookback + 1);
    const slice = out.slice(start, idx + 1).map((r) => r.price);
    row.upper = +Math.max(...slice).toFixed(2);
    row.lower = +Math.min(...slice).toFixed(2);
  });
  return out;
}

function DonchianBreakout() {
  const data = donchianSeries();
  // First bar where price punches the upper channel (i.e. price ==
  // upper after the consolidation phase).
  const breakoutIdx = data.findIndex(
    (d, i) => i >= 25 && d.price >= d.upper - 0.01,
  );
  return (
    <DiagramFrame caption="Channel = high/low of last 20 bars. Buy when price punches the upper channel.">
      <ResponsiveContainer width="100%" height={PANEL_HEIGHT}>
        <ComposedChart data={data} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
          <XAxis dataKey="i" hide />
          <YAxis tick={{ fill: COLOUR_TEXT_DIM, fontSize: 11 }} domain={["auto", "auto"]} />
          <Tooltip {...tooltipProps} />
          <Legend {...legendProps} />
          {/* Channel fill — area between upper and lower */}
          <Area type="monotone" dataKey="upper" stroke="none" fill={COLOUR_UP} fillOpacity={0.06} stackId="1" name="Channel" legendType="none" />
          <Line type="monotone" dataKey="upper" stroke={COLOUR_UP} strokeDasharray="4 4" strokeWidth={1.4} dot={false} name="Upper (20-bar high)" />
          <Line type="monotone" dataKey="lower" stroke={COLOUR_DOWN} strokeDasharray="4 4" strokeWidth={1.4} dot={false} name="Lower (20-bar low)" />
          <Line type="monotone" dataKey="price" stroke="#cbd2dc" strokeWidth={2} dot={false} name="Price" />
          {breakoutIdx > 0 && (
            <ReferenceDot x={breakoutIdx} y={data[breakoutIdx].price} r={6} fill={COLOUR_UP} stroke="white" label={{ value: "BREAKOUT", position: "top", fill: COLOUR_UP, fontSize: 10, fontWeight: 700 }} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </DiagramFrame>
  );
}

// ---------------------------------------------------------------------------
// Range position — visual demo of where price sits in 52w range
// ---------------------------------------------------------------------------

function RangePosition() {
  const examples = [
    { name: "Near low (BUY zone)", pct: 20, colour: COLOUR_UP },
    { name: "Mid-range (neutral)", pct: 52, colour: COLOUR_NEUTRAL },
    { name: "Near highs (HOLD)", pct: 73, colour: COLOUR_DOWN },
    { name: "At highs (capped)", pct: 95, colour: COLOUR_DOWN },
  ];
  return (
    <DiagramFrame caption="Where current price sits in the 52w (low → high) range. ≥70th pctile downgrades a BUY → HOLD; ≥80th hard-caps swing at WATCH.">
      <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "8px 4px" }}>
        {examples.map((row) => (
          <div key={row.name} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 160, fontSize: 12, color: "var(--text-dim)" }}>{row.name}</div>
            <div style={{ flex: 1, position: "relative", height: 22, background: "rgba(155,161,173,0.12)", borderRadius: 4 }}>
              {/* Threshold guides */}
              <div style={{ position: "absolute", left: "40%", top: 0, bottom: 0, width: 1, background: "rgba(155,161,173,0.4)" }} />
              <div style={{ position: "absolute", left: "70%", top: 0, bottom: 0, width: 1, background: COLOUR_NEUTRAL, opacity: 0.6 }} />
              <div style={{ position: "absolute", left: "80%", top: 0, bottom: 0, width: 1, background: COLOUR_DOWN, opacity: 0.6 }} />
              {/* Fill bar */}
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${row.pct}%`, background: row.colour, opacity: 0.55, borderRadius: 4 }} />
              {/* Marker dot */}
              <div style={{ position: "absolute", left: `calc(${row.pct}% - 5px)`, top: 5, width: 12, height: 12, borderRadius: 6, background: row.colour, border: "2px solid white", boxShadow: "0 0 0 1px rgba(0,0,0,0.3)" }} />
            </div>
            <div style={{ width: 56, fontSize: 12, color: row.colour, fontWeight: 700, textAlign: "right" }}>
              {row.pct}th
            </div>
          </div>
        ))}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 10, color: "var(--text-muted)" }}>
          <span>0th = at 52w low</span>
          <span style={{ color: COLOUR_NEUTRAL }}>70th</span>
          <span style={{ color: COLOUR_DOWN }}>80th cap</span>
          <span>100th = at 52w high</span>
        </div>
      </div>
    </DiagramFrame>
  );
}

// ---------------------------------------------------------------------------
// Return histogram — distribution of daily returns, mean + sigma
// ---------------------------------------------------------------------------

function returnHistogramData(): { bin: string; count: number }[] {
  // Synthetic Gaussian-ish daily-return distribution.
  // Buckets: −5%, −4%, ..., +5% (centred on 0).
  const counts = [3, 5, 12, 28, 56, 92, 78, 42, 18, 8, 2];
  return counts.map((c, i) => ({
    bin: `${i - 5 >= 0 ? "+" : ""}${i - 5}%`,
    count: c,
  }));
}

function ReturnHistogram() {
  const data = returnHistogramData();
  return (
    <DiagramFrame caption="Daily-return distribution. Sharpe = mean ÷ stdev — measures how peaky AND how centred above zero the histogram is.">
      <ResponsiveContainer width="100%" height={PANEL_HEIGHT}>
        <ComposedChart data={data} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
          <XAxis dataKey="bin" tick={{ fill: COLOUR_TEXT_DIM, fontSize: 11 }} />
          <YAxis tick={{ fill: COLOUR_TEXT_DIM, fontSize: 11 }} />
          <Tooltip {...tooltipProps} />
          <ReferenceLine x="0%" stroke={COLOUR_TEXT_DIM} strokeWidth={1.5} label={{ value: "Mean", position: "top", fill: COLOUR_TEXT_DIM, fontSize: 11 }} />
          <ReferenceArea x1="-2%" x2="+2%" fill={COLOUR_UP} fillOpacity={0.06} label={{ value: "±1σ", position: "insideTop", fill: COLOUR_TEXT_DIM, fontSize: 10 }} />
          <Bar dataKey="count" name="Days">
            {data.map((d, idx) => {
              const v = parseInt(d.bin);
              const c = v >= 0 ? COLOUR_UP : COLOUR_DOWN;
              return <Cell key={idx} fill={c} fillOpacity={0.7} />;
            })}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </DiagramFrame>
  );
}

// ---------------------------------------------------------------------------
// Shared chrome
// ---------------------------------------------------------------------------

const tooltipProps = {
  contentStyle: {
    background: "rgba(20, 24, 33, 0.92)",
    border: "1px solid rgba(155, 161, 173, 0.3)",
    borderRadius: 6,
    color: "white",
    fontSize: 12,
  } as React.CSSProperties,
  cursor: { stroke: "rgba(155,161,173,0.4)", strokeDasharray: "3 3" },
};

const legendProps = {
  iconSize: 8,
  wrapperStyle: { fontSize: 11, paddingTop: 6 } as React.CSSProperties,
};

function DiagramFrame({
  caption,
  children,
}: {
  caption: string;
  children: React.ReactNode;
}) {
  return (
    <figure style={{ margin: "8px 0 16px 0" }}>
      <div
        style={{
          padding: "10px 12px 4px",
          border: "1px solid var(--border)",
          borderRadius: 6,
          background: "rgba(0,0,0,0.10)",
        }}
      >
        {children}
      </div>
      <figcaption style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)", textAlign: "center" }}>
        {caption}
      </figcaption>
    </figure>
  );
}

