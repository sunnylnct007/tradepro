import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { config } from "../config";
import type { Candle, CandleSeries } from "../api/types";

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
}

const DEFAULT_LOOKBACK_DAYS = 365 * 5;

export function PriceHistoryChart({
  symbol,
  lookbackDays = DEFAULT_LOOKBACK_DAYS,
  height = 280,
}: Props) {
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    }));
    // 52w window = last ~252 trading days.
    const window = candles.slice(Math.max(0, candles.length - 252));
    const high52w = Math.max(...window.map(priceOf));
    const low52w = Math.min(...window.map(priceOf));
    const last = priceOf(candles[candles.length - 1]);
    const peak = Math.max(...adj);
    const peakIdx = adj.indexOf(peak);
    const peakDate = candles[peakIdx]?.timestamp.slice(0, 10);
    return { data, high52w, low52w, last, peak, peakDate };
  }, [series]);

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

  const { data, high52w, low52w, last, peak, peakDate } = computed;
  const tone = last >= (high52w + low52w) / 2 ? "var(--up)" : "var(--neutral)";

  return (
    <div className="card" style={{ padding: "12px 14px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8, gap: 12, flexWrap: "wrap" }}>
        <div className="stat-label">
          {symbol} price · {Math.round(lookbackDays / 365)}y · split-adjusted
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span>now <strong className="num" style={{ color: tone }}>{last.toFixed(2)}</strong></span>
          <span>52w high <span className="num">{high52w.toFixed(2)}</span></span>
          <span>52w low <span className="num">{low52w.toFixed(2)}</span></span>
          <span>5y peak <span className="num">{peak.toFixed(2)}</span> {peakDate && <span style={{ color: "var(--text-muted)" }}>· {peakDate}</span>}</span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
            formatter={(v: number) => v.toFixed(2)}
          />
          <ReferenceLine y={high52w} stroke="var(--up)" strokeDasharray="3 3" label={{ value: "52w high", position: "right", fill: "var(--up)", fontSize: 10 }} />
          <ReferenceLine y={low52w} stroke="var(--down)" strokeDasharray="3 3" label={{ value: "52w low", position: "right", fill: "var(--down)", fontSize: 10 }} />
          <Line type="monotone" dataKey="price" stroke="#cbd2dc" strokeWidth={1.5} dot={false} name="Price (adj)" />
          <Line type="monotone" dataKey="sma200" stroke="#9b6eff" strokeWidth={1.4} strokeDasharray="6 3" dot={false} name="SMA(200)" />
        </LineChart>
      </ResponsiveContainer>
      <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
        Price line uses split-adjusted close so 4:1 / 2:1 splits don't render as
        fake crashes. SMA(200) is the same line the engine uses for the trend
        check; reference lines are the 52w extremes used by the range-position
        guard.
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
