/** Pure maths for the chart's context header (review item 15 + the
 *  renko/RSI/trend advice of 28 Nov 2026). No fetching here, so it is
 *  testable; the header fetches and calls these. */
import type { Candle } from "../../api/types";
import { atrBrickSize, renkoBricks } from "./renko";

export type Regime = { above50: boolean | null; above200: boolean | null; sma50: number | null; sma200: number | null };

export function pct(a: number | null | undefined, b: number | null | undefined): number | null {
  if (a == null || b == null || !(b > 0)) return null;
  return (a / b - 1) * 100;
}

export function sma(cl: number[], n: number): number | null {
  if (cl.length < n) return null;
  let s = 0; for (let i = cl.length - n; i < cl.length; i++) s += cl[i];
  return s / n;
}

export function regimeOf(candles: Candle[]): Regime {
  const cl = candles.map((c) => c.close);
  const last = cl[cl.length - 1];
  const s50 = sma(cl, 50), s200 = sma(cl, 200);
  return { above50: s50 == null ? null : last > s50, above200: s200 == null ? null : last > s200, sma50: s50, sma200: s200 };
}

/** Wilder RSI(14) on closes, plus its 5-bar slope sign. */
export function rsi14(cl: number[]): { value: number | null; rising: boolean | null } {
  const n = 14;
  if (cl.length < n + 6) return { value: null, rising: null };
  const series: number[] = [];
  let g = 0, l = 0;
  for (let i = 1; i <= n; i++) { const d = cl[i] - cl[i - 1]; if (d > 0) g += d; else l -= d; }
  g /= n; l /= n;
  series.push(l === 0 ? 100 : 100 - 100 / (1 + g / l));
  for (let i = n + 1; i < cl.length; i++) {
    const d = cl[i] - cl[i - 1];
    g = (g * (n - 1) + Math.max(d, 0)) / n; l = (l * (n - 1) + Math.max(-d, 0)) / n;
    series.push(l === 0 ? 100 : 100 - 100 / (1 + g / l));
  }
  const v = series[series.length - 1], prev = series[series.length - 6];
  return { value: Math.round(v), rising: prev == null ? null : v > prev };
}

/** Renko state from ATR(14) bricks: direction of the last brick, how many
 *  consecutive bricks in that direction, and when the current run began. */
export function renkoState(candles: Candle[]): { dir: "up" | "down" | null; run: number; since: string | null; brick: number | null } {
  const src = candles.map((c) => ({ timestamp: c.timestamp, high: c.high, low: c.low, close: c.close }));
  if (src.length < 30) return { dir: null, run: 0, since: null, brick: null };
  const brick = atrBrickSize(src, 14);
  const bricks = renkoBricks(src, brick);
  if (!bricks.length) return { dir: null, run: 0, since: null, brick };
  const last = bricks[bricks.length - 1];
  let run = 0, i = bricks.length - 1;
  while (i >= 0 && bricks[i].up === last.up) { run++; i--; }
  const since = new Date(bricks[bricks.length - run].sourceTime * 1000).toISOString().slice(0, 10);
  return { dir: last.up ? "up" : "down", run, since, brick };
}

/** The three-leg read: trend (daily) says whether to be bullish, renko says
 *  whether buying has returned, RSI confirms strength. Display only. */
export function momentumRead(candles: Candle[]): { label: string; tone: "good" | "warn" | "bad" | "muted"; detail: string } {
  const cl = candles.map((c) => c.close);
  const reg = regimeOf(candles);
  const rk = renkoState(candles);
  const r = rsi14(cl);
  if (rk.dir == null || r.value == null) return { label: "momentum: not enough bars", tone: "muted", detail: "" };
  const trendUp = reg.above50 === true && reg.above200 === true;
  const buyingBack = rk.dir === "up" && rk.run >= 2;
  const strong = r.value > 50 && r.rising === true;
  const detail = `renko ${rk.dir} ×${rk.run} since ${rk.since} (brick ${rk.brick?.toFixed(2)}) · RSI14 ${r.value} ${r.rising ? "rising" : "falling"} · ${trendUp ? "above" : "not above"} the 50 & 200-day`;
  if (trendUp && buyingBack && strong) return { label: "momentum returning, trend intact", tone: "good", detail };
  if (buyingBack && !trendUp) return { label: "bounce in a downtrend — renko up, trend not", tone: "warn", detail };
  if (!buyingBack && trendUp) return { label: "trend intact, buying not back yet", tone: "warn", detail };
  return { label: "no momentum: renko down, trend down", tone: "bad", detail };
}

/** Rebase several close series to 100 at the first common date. */
export function rebase(series: Record<string, Candle[]>): Record<string, { time: string; value: number }[]> {
  const keys = Object.keys(series).filter((k) => series[k]?.length);
  if (!keys.length) return {};
  const start = keys.map((k) => series[k][0].timestamp.slice(0, 10)).sort().reverse()[0]; // latest first date
  const out: Record<string, { time: string; value: number }[]> = {};
  for (const k of keys) {
    const rows = series[k].filter((c) => c.timestamp.slice(0, 10) >= start);
    const base = rows[0]?.close;
    if (!base) continue;
    out[k] = rows.map((c) => ({ time: c.timestamp.slice(0, 10), value: (c.close / base) * 100 }));
  }
  return out;
}
