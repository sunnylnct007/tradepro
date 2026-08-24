/**
 * RuleChart — the chart the RULE sees, not a generic price chart.
 *
 * Owner previously: "looking at graph like absolutely add me no value" — and
 * he was right about candlesticks with indicators sprinkled on. Then, asking
 * for a chart on the scanner, the context is different: he is looking at one
 * symbol and deciding about one signal. A chart earns its place here only if
 * it shows the DECISION.
 *
 * So it draws exactly four things and nothing else:
 *   • close, the last ~130 sessions
 *   • the 20-day mean — the TARGET, which moves and comes to meet you
 *   • the 2.5σ lower band — the TRIGGER; price must close below this line
 *   • the 200-day average — the TREND FLOOR; below it the rule refuses
 *
 * Plus every past trade marked at entry and exit, coloured by outcome, so
 * "14 trades, 86% win" stops being an abstraction and becomes visible: you
 * can see where it bought and whether the price came back.
 *
 * Hand-drawn SVG rather than a charting library — four polylines and some
 * circles do not justify a dependency, and this renders inside a table row
 * without layout fights.
 */
import type { Bar, SwingParams, SwingTrade } from "../../lib/tradeOdds";

const TONE = { ok: "#1D9E75", bad: "#D85A30", warn: "#E6A817" };
const mean_ = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
function pstdev(xs: number[]) {
  const m = mean_(xs);
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length);
}

export function RuleChart({ bars, p, trades, height = 150, sessions = 130 }: {
  bars: Bar[]; p: SwingParams; trades: SwingTrade[]; height?: number; sessions?: number;
}) {
  const W = 640, H = height, PAD = 4;
  const c = bars.map((b) => b.close);
  // Every line needs `trendWindow` bars of run-up before the first plotted
  // point, so draw as many sessions as the history actually allows rather
  // than rendering nothing. A short-history symbol gets a short chart, not a
  // blank panel — a blank panel looks like a bug and says nothing about why.
  const span_ = Math.min(sessions, c.length - p.trendWindow);
  if (span_ < 20) return null;

  const start = c.length - span_;
  const xs: number[] = [], band: number[] = [], m20: number[] = [], s200: number[] = [];
  for (let i = start; i < c.length; i++) {
    const w = c.slice(i - p.bbWindow + 1, i + 1);
    const m = mean_(w), sd = pstdev(w);
    xs.push(c[i]); m20.push(m); band.push(m - p.sigma * sd);
    s200.push(mean_(c.slice(i - p.trendWindow + 1, i + 1)));
  }
  const all = [...xs, ...band, ...m20, ...s200];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const X = (i: number) => PAD + (i * (W - 2 * PAD)) / (span_ - 1);
  const Y = (v: number) => PAD + (1 - (v - lo) / span) * (H - 2 * PAD);
  const path = (ys: number[]) => ys.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");

  const ds = bars.map((b) => b.ts.slice(0, 10));
  const idxOf = (d: string) => { const k = ds.indexOf(d); return k >= start ? k - start : -1; };
  const marks = trades.flatMap((t) => {
    const a = idxOf(t.signal), b = idxOf(t.exitDate);
    return a < 0 ? [] : [{ a, b, t }];
  });

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height, display: "block" }}>
        {/* trend floor — below this the rule does not fire at all */}
        <path d={path(s200)} fill="none" stroke="#5a6b8c" strokeWidth={1} strokeDasharray="4 3" />
        {/* the target: the 20-day mean, which moves toward a recovering price */}
        <path d={path(m20)} fill="none" stroke={TONE.ok} strokeWidth={1} opacity={0.75} />
        {/* the trigger: price must close BELOW this */}
        <path d={path(band)} fill="none" stroke={TONE.warn} strokeWidth={1} strokeDasharray="2 3" />
        <path d={path(xs)} fill="none" stroke="#cfd8e8" strokeWidth={1.6} />
        {marks.map(({ a, b, t }, k) => (
          <g key={k}>
            {b >= 0 && (
              <line x1={X(a)} y1={Y(xs[a])} x2={X(b)} y2={Y(xs[b])}
                    stroke={t.pct > 0 ? TONE.ok : TONE.bad} strokeWidth={1} opacity={0.5} />
            )}
            <circle cx={X(a)} cy={Y(xs[a])} r={3.2} fill={t.pct > 0 ? TONE.ok : TONE.bad}
                    stroke="#0b0f17" strokeWidth={1}>
              <title>{`${t.signal} entry ${t.entry.toFixed(2)} → ${t.exitDate} ${t.why} ${t.pct > 0 ? "+" : ""}${t.pct.toFixed(2)}%`}</title>
            </circle>
          </g>
        ))}
      </svg>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 3, lineHeight: 1.6 }}>
        <span style={{ color: "#cfd8e8" }}>━ close</span>{"  "}
        <span style={{ color: TONE.ok }}>━ 20-day mean (the target)</span>{"  "}
        <span style={{ color: TONE.warn }}>┄ {p.sigma}σ band (the trigger — price must close below)</span>{"  "}
        <span style={{ color: "#5a6b8c" }}>┄ {p.trendWindow}-day average (below it the rule refuses)</span>
        {marks.length > 0 && <>{"  "}● entries, coloured by outcome — hover for the trade</>}
        <div>Last {span_} sessions. Not a price chart — these are the four lines the rule reads.</div>
      </div>
    </div>
  );
}
