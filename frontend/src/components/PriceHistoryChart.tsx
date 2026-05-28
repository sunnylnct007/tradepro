import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { config } from "../config";
import type { Candle, CandleSeries, CorporateActionMarker, EarningsMarker } from "../api/types";

/**
 * Inline price history chart for a single symbol — used on the
 * Research (Signals) page and the Decide expand panel so the user
 * can SEE what the engine is reasoning about, not just read numbers.
 *
 * Key design decisions:
 *
 * - **Split-adjusted prices.** The chart uses `adjustedClose` rather
 *   than `close` so 4:1 / 2:1 splits don't show up as fake 75% / 50%
 *   crashes in the line. Yahoo Finance computes these for us; the
 *   adjusted series IS what every signal in the engine sees too.
 *
 * - **Reference lines.** SMA(200) drawn as a smooth secondary line
 *   so trend regime is visible at a glance. 52w high + 52w low as
 *   horizontal reference lines so the user reads "where am I in
 *   the year" without doing the arithmetic.
 *
 * - **Default 5-year window.** The full 5y is the same horizon the
 *   engine's drawdown_from_peak metric uses; gives the reader the
 *   long-term context (recovered from COVID-2020 / 2022 rate
 *   shock?). Smaller window via the prop when needed.
 */
interface Props {
  symbol: string;
  /** Days of history to plot. Default 5y so the user sees the full
   * regime picture the engine reasons about. */
  lookbackDays?: number;
  height?: number;
  /** Historical earnings dates from the compare payload — when
   * present, vertical markers are drawn on the price line so the
   * user can spot event-driven moves vs trend moves at a glance. */
  earnings?: EarningsMarker[];
  /** Corporate actions (dividends "D" + splits "S") — when present,
   * amber "D" or teal "S" chips are drawn on the price line at the
   * event date so the user can tell dividend drops from real price
   * moves and verify the adjusted-close continuity across splits. */
  corporateActions?: CorporateActionMarker[];
}

const DEFAULT_LOOKBACK_DAYS = 365 * 5;

// Range presets in approximate trading days — the user clicks one to
// snap the brush window to that lookback. "All" resets to full data.
const PRESETS: { label: string; days: number }[] = [
  { label: "1M", days: 22 },
  { label: "3M", days: 65 },
  { label: "6M", days: 130 },
  { label: "YTD", days: -2 },   // sentinel: compute at apply time
  { label: "1Y", days: 252 },
  { label: "5Y", days: 252 * 5 },
  { label: "All", days: -1 },   // sentinel: full range
];

export function PriceHistoryChart({
  symbol,
  lookbackDays = DEFAULT_LOOKBACK_DAYS,
  height = 280,
  earnings,
  corporateActions,
}: Props) {
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Brush-controlled visible window into the full `data` array.
  // `null` means "show everything"; we set it from PRESETS or by
  // dragging the brush handles.
  const [range, setRange] = useState<[number, number] | null>(null);
  const [activePreset, setActivePreset] = useState<string>("5Y");

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSeries(null);
    const to = new Date();
    const from = new Date(Date.now() - lookbackDays * 24 * 3600 * 1000);
    api.candles({
      symbol,
      provider: config.defaultProvider,
      interval: "1d",
      from: from.toISOString().slice(0, 10),
      to: to.toISOString().slice(0, 10),
    })
      .then((s) => { if (!cancelled) setSeries(s); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [symbol, lookbackDays]);

  // Compute reference levels + the SMA(200) overlay from the same
  // adjusted-close series the chart line uses, so the line and the
  // reference levels stay in sync across splits.
  const computed = useMemo(() => {
    if (!series || series.candles.length === 0) return null;
    const candles = series.candles;
    const adj = candles.map((c) => priceOf(c));
    const data = candles.map((c, i) => ({
      t: c.timestamp.slice(0, 10),
      price: priceOf(c),
      sma200: smaAt(adj, i, 200),
      volume: c.volume,
    }));
    // 52w window = last ~252 trading days. Track the dates of both
    // extremes so the user can tell whether the floor/ceiling was
    // tested recently (live signal) or months ago (stale).
    const startIdx = Math.max(0, candles.length - 252);
    const window = candles.slice(startIdx);
    const high52w = Math.max(...window.map(priceOf));
    const low52w = Math.min(...window.map(priceOf));
    const high52wIdx = startIdx + window.findIndex((c) => priceOf(c) === high52w);
    const low52wIdx = startIdx + window.findIndex((c) => priceOf(c) === low52w);
    const high52wDate = candles[high52wIdx]?.timestamp.slice(0, 10);
    const low52wDate = candles[low52wIdx]?.timestamp.slice(0, 10);
    const last = priceOf(candles[candles.length - 1]);
    const peak = Math.max(...adj);
    const peakIdx = adj.indexOf(peak);
    const peakDate = candles[peakIdx]?.timestamp.slice(0, 10);
    return {
      data,
      high52w, low52w, high52wDate, low52wDate,
      last, peak, peakDate,
    };
  }, [series]);

  // When new data arrives, snap the visible window to the active preset
  // (default 5Y / All). Without this the brush handles would point at
  // stale indices when the user switches symbols.
  useEffect(() => {
    if (!computed) return;
    setRange(presetToRange(activePreset, computed.data));
    // intentionally not depending on activePreset — preset clicks
    // already update the range directly via applyPreset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [computed]);

  const applyPreset = (label: string) => {
    if (!computed) return;
    setActivePreset(label);
    setRange(presetToRange(label, computed.data));
  };

  if (error) {
    return (
      <div style={{ color: "var(--down)", fontSize: 12 }}>
        Couldn't load price history: {error}
      </div>
    );
  }
  if (loading || !computed) {
    return (
      <div style={{ color: "var(--text-dim)", fontSize: 12, padding: 12 }}>
        Loading {symbol} price history…
      </div>
    );
  }

  const { data, high52w, low52w, high52wDate, low52wDate, last, peak, peakDate } = computed;
  const tone = last >= (high52w + low52w) / 2 ? "var(--up)" : "var(--neutral)";
  const lastDate = data[data.length - 1].t;
  // Range zones: bottom 35% = "dip" (green tint), middle 30% = neutral,
  // top 35% = "near highs" (amber tint). Visualises the same percentile
  // the engine's range-position guard reasons about, so the user can
  // see at a glance which zone today's price is sitting in.
  const span = high52w - low52w;
  const zoneDipTop = low52w + span * 0.35;
  const zoneAmberBottom = low52w + span * 0.65;

  // Visible-window stats (recomputed when the brush moves) so the
  // header reflects what the user is actually inspecting. Falls back
  // to the full series when no range is set.
  const visible = range
    ? data.slice(range[0], range[1] + 1)
    : data;
  const visibleHigh = visible.length ? Math.max(...visible.map((d) => d.price)) : last;
  const visibleLow = visible.length ? Math.min(...visible.map((d) => d.price)) : last;

  return (
    <div className="card" style={{ padding: "12px 14px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8, gap: 12, flexWrap: "wrap" }}>
        <div className="stat-label">
          {symbol} price · split-adjusted
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span>now <strong className="num" style={{ color: tone }}>{last.toFixed(2)}</strong></span>
          {/* Surface BOTH 52w extreme dates so the user can tell whether
              the floor/ceiling is "live" (recently tested) or "stale". */}
          <span>52w high <span className="num">{high52w.toFixed(2)}</span> {high52wDate && <span style={{ color: "var(--text-muted)" }}>· {high52wDate}</span>}</span>
          <span>52w low <span className="num">{low52w.toFixed(2)}</span> {low52wDate && <span style={{ color: "var(--text-muted)" }}>· {low52wDate}</span>}</span>
          <span>5y peak <span className="num">{peak.toFixed(2)}</span> {peakDate && <span style={{ color: "var(--text-muted)" }}>· {peakDate}</span>}</span>
          <span style={{ borderLeft: "1px solid rgba(155,161,173,0.3)", paddingLeft: 14 }}>
            window <span className="num">{visibleLow.toFixed(2)}</span> – <span className="num">{visibleHigh.toFixed(2)}</span>
          </span>
        </div>
      </div>
      {/* Range presets: snap the brush to a common lookback in one
          click. The brush handles below the chart still allow free-form
          zoom into any sub-window. */}
      <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => applyPreset(p.label)}
            style={{
              fontSize: 10,
              padding: "3px 8px",
              borderRadius: 999,
              border: "1px solid rgba(155,161,173,0.3)",
              background: activePreset === p.label
                ? "rgba(155,110,255,0.18)"
                : "rgba(0,0,0,0.18)",
              color: activePreset === p.label ? "#cbb6ff" : "var(--text-muted)",
              cursor: "pointer",
            }}
          >
            {p.label}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }} syncId="priceHistory">
          <CartesianGrid stroke="rgba(155,161,173,0.15)" strokeDasharray="3 3" />
          <XAxis dataKey="t" tick={{ fill: "#9ba1ad", fontSize: 10 }} minTickGap={48} />
          <YAxis tick={{ fill: "#9ba1ad", fontSize: 10 }} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{
              background: "rgba(20,24,33,0.92)",
              border: "1px solid rgba(155,161,173,0.3)",
              borderRadius: 6,
              color: "white",
              fontSize: 12,
            }}
            formatter={(v: number, name: string) =>
              name === "Volume" ? v.toLocaleString() : v.toFixed(2)
            }
          />
          {/* Range zones: dip / neutral / near-highs bands across the
              52w price range. ifOverflow="hidden" so they only paint
              the slice that intersects the visible Y window. */}
          <ReferenceArea y1={low52w} y2={zoneDipTop} fill="var(--up)" fillOpacity={0.06} ifOverflow="hidden" />
          <ReferenceArea y1={zoneAmberBottom} y2={high52w} fill="var(--neutral)" fillOpacity={0.06} ifOverflow="hidden" />
          {/* ifOverflow="hidden" so when the user zooms into a window
              that doesn't include the 52w extreme, the reference line
              quietly disappears instead of forcing the Y-axis to expand
              and defeating the zoom. */}
          <ReferenceLine y={high52w} stroke="var(--up)" strokeDasharray="3 3" ifOverflow="hidden" label={{ value: "52w high", position: "right", fill: "var(--up)", fontSize: 10 }} />
          <ReferenceLine y={low52w} stroke="var(--down)" strokeDasharray="3 3" ifOverflow="hidden" label={{ value: "52w low", position: "right", fill: "var(--down)", fontSize: 10 }} />
          {/* Mark when the 52w extremes were actually hit so the user
              can tell at a glance whether the floor/ceiling is freshly
              tested or months stale. */}
          {high52wDate && (
            <ReferenceDot x={high52wDate} y={high52w} r={3.5} fill="var(--up)" stroke="white" strokeWidth={0.5} ifOverflow="hidden" />
          )}
          {low52wDate && (
            <ReferenceDot x={low52wDate} y={low52w} r={3.5} fill="var(--down)" stroke="white" strokeWidth={0.5} ifOverflow="hidden" />
          )}
          {/* Earnings event markers. Each reported earnings shows as a
              short vertical line + dot anchored to the close on that
              date. ifOverflow="hidden" so zooming outside the date or
              price range silently drops the marker (no axis blow-up).
              Colour: green = beat, red = miss, grey = unknown. */}
          {earnings && earnings.length > 0 && earnings.map((e) => {
            const bar = data.find((d) => d.t === e.date);
            if (!bar) return null;
            const colour =
              e.surprise_pct === null || e.surprise_pct === undefined
                ? "var(--neutral)"
                : e.surprise_pct >= 0
                ? "var(--up)"
                : "var(--down)";
            return (
              <ReferenceDot
                key={`er-${e.date}`}
                x={e.date}
                y={bar.price}
                r={3}
                fill={colour}
                stroke="white"
                strokeWidth={0.5}
                ifOverflow="hidden"
                label={{ value: "E", position: "top", fill: colour, fontSize: 9 }}
              />
            );
          })}
          {/* Corporate action chips. Dividends = amber "D" below the bar,
              splits = teal "S" above. ifOverflow="hidden" keeps them
              invisible outside the brush window. */}
          {corporateActions && corporateActions.length > 0 && corporateActions.map((ca) => {
            const bar = data.find((d) => d.t === ca.date);
            if (!bar) return null;
            const isDividend = ca.type === "dividend";
            const colour = isDividend ? "#f59e0b" : "#06b6d4"; // amber | cyan
            const label = isDividend ? "D" : "S";
            const position = isDividend ? "bottom" : "insideTop";
            const tooltip = isDividend
              ? ca.amount != null ? `Div $${ca.amount.toFixed(2)}` : "Dividend"
              : ca.ratio ? `Split ${ca.ratio}` : "Split";
            return (
              <ReferenceDot
                key={`ca-${ca.date}-${ca.type}`}
                x={ca.date}
                y={bar.price}
                r={3}
                fill={colour}
                stroke="white"
                strokeWidth={0.5}
                ifOverflow="hidden"
                label={{ value: label, position, fill: colour, fontSize: 9 }}
              >
                <title>{tooltip}</title>
              </ReferenceDot>
            );
          })}
          {/* "Today" marker: vertical line + dot at the right edge so
              the user can locate the current bar without squinting. */}
          <ReferenceLine x={lastDate} stroke="rgba(255,255,255,0.45)" strokeDasharray="2 4" ifOverflow="hidden" label={{ value: "today", position: "top", fill: "rgba(255,255,255,0.6)", fontSize: 10 }} />
          <ReferenceDot x={lastDate} y={last} r={4} fill={tone} stroke="white" strokeWidth={1} ifOverflow="hidden" />
          <Line type="monotone" dataKey="price" stroke="#cbd2dc" strokeWidth={1.5} dot={false} name="Price (adj)" isAnimationActive={false} />
          <Line type="monotone" dataKey="sma200" stroke="#9b6eff" strokeWidth={1.4} strokeDasharray="6 3" dot={false} name="SMA(200)" isAnimationActive={false} />
          <Brush
            dataKey="t"
            height={26}
            stroke="#9b6eff"
            travellerWidth={10}
            fill="rgba(155,110,255,0.08)"
            startIndex={range?.[0]}
            endIndex={range?.[1]}
            onChange={(e) => {
              if (typeof e?.startIndex === "number" && typeof e?.endIndex === "number") {
                // Free-form drag → mark preset as "Custom" so the
                // selected-pill highlight doesn't lie about state.
                setRange([e.startIndex, e.endIndex]);
                setActivePreset("Custom");
              }
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
      {/* Volume strip — separate chart sharing the same X axis via
          syncId so the Brush window above slaves this one too. A rally
          on heavy volume is more meaningful than a rally on thin volume:
          gives the user a participation read at a glance. */}
      <ResponsiveContainer width="100%" height={70}>
        <BarChart data={data} margin={{ top: 0, right: 16, left: 0, bottom: 0 }} syncId="priceHistory">
          <XAxis dataKey="t" hide />
          <YAxis hide domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{
              background: "rgba(20,24,33,0.92)",
              border: "1px solid rgba(155,161,173,0.3)",
              borderRadius: 6,
              color: "white",
              fontSize: 12,
            }}
            formatter={(v: number) => v.toLocaleString()}
            labelStyle={{ color: "#9ba1ad" }}
          />
          <Bar dataKey="volume" name="Volume" fill="#9b6eff" fillOpacity={0.5} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
        Price line uses split-adjusted close so 4:1 / 2:1 splits don't render as
        fake crashes. SMA(200) is the same line the engine uses for the trend
        check; 52w reference lines hide when zoomed outside their level. Coloured
        dots mark when each 52w extreme was hit (live vs stale floor). Volume
        bars below share the brush window — a rally on thick bars shows broad
        participation; thin bars suggest a thin rally. "E" dots = reported
        earnings (green=beat, red=miss, grey=unknown); amber "D" = dividend
        ex-date; teal "S" = stock split. Drag the brush or click a preset to zoom.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function priceOf(c: Candle): number {
  // Prefer adjustedClose (split + dividend adjusted) so the line is
  // continuous across splits. Fall back to close when adjustedClose
  // is absent (some providers or older payloads).
  return c.adjustedClose ?? c.close ?? 0;
}

function smaAt(prices: number[], idx: number, window: number): number | null {
  if (idx + 1 < window) return null;
  let sum = 0;
  for (let i = idx - window + 1; i <= idx; i++) sum += prices[i];
  return sum / window;
}

function presetToRange(
  label: string,
  data: { t: string }[],
): [number, number] {
  const total = data.length;
  if (total === 0) return [0, 0];
  const last = total - 1;
  const preset = PRESETS.find((p) => p.label === label);
  if (!preset || preset.days === -1) return [0, last];
  if (preset.days === -2) {
    // YTD: walk backwards until the year changes.
    const lastYear = data[last].t.slice(0, 4);
    let i = last;
    while (i > 0 && data[i].t.slice(0, 4) === lastYear) i--;
    return [Math.min(i + 1, last), last];
  }
  return [Math.max(0, total - preset.days), last];
}
