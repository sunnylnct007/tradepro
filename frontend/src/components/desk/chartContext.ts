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

/** TREND READ in two separate concepts (owner, 28 Nov 2026, from another
 *  agent's advice): DIRECTION — is the stock bullish, bearish or sideways
 *  (daily 50/200-day structure) — and STAGE — is it accelerating, weakening,
 *  recovering or reversing (ATR-brick renko run + RSI14 slope). Plus a
 *  yes/no REVERSAL CONFIRMED with the unmet conditions named. Display only. */
export type TrendRead = {
  direction: "bullish" | "bearish" | "sideways" | "unknown";
  stage: string;
  reversalConfirmed: boolean | null;
  reversalNote: string;
  tone: "good" | "warn" | "bad" | "muted";
  detail: string;
};

export function trendRead(candles: Candle[]): TrendRead {
  const cl = candles.map((c) => c.close);
  const reg = regimeOf(candles);
  const rk = renkoState(candles);
  const r = rsi14(cl);
  const last = cl[cl.length - 1];
  if (rk.dir == null || r.value == null || reg.sma50 == null) {
    return { direction: "unknown", stage: "not enough bars", reversalConfirmed: null, reversalNote: "", tone: "muted", detail: "" };
  }
  const s50 = reg.sma50, s200 = reg.sma200;
  const slope50 = cl.length > 60 ? (sma(cl.slice(0, -10), 50) ?? s50) : s50;
  const rising50 = s50 > slope50;
  let direction: TrendRead["direction"] = "sideways";
  if (last > s50 && (s200 == null || s50 >= s200) && rising50) direction = "bullish";
  else if (last < s50 && (s200 == null || s50 <= s200) && !rising50) direction = "bearish";
  // stage from renko (buying/selling pressure) and RSI slope (strength)
  let stage: string;
  if (direction === "bearish") {
    if (rk.dir === "up" && rk.run >= 2) stage = "short-term recovery";
    else if (rk.dir === "up") stage = "bounce attempt (one brick)";
    else if (r.rising) stage = "weakening downtrend (RSI rising while price falls)";
    else stage = "accelerating downtrend";
  } else if (direction === "bullish") {
    if (rk.dir === "up" && r.rising) stage = "accelerating";
    else if (rk.dir === "up") stage = "advancing, momentum weakening (RSI falling)";
    else if (rk.run >= 2) stage = "pullback (renko down ×" + rk.run + ")";
    else stage = "pausing";
  } else {
    stage = rk.dir === "up" ? "ranging, buying returning" : "ranging, selling pressure";
  }
  // reversal confirmed = direction flips on price structure AND pressure AND strength
  let reversalConfirmed: boolean | null = null, reversalNote = "";
  if (direction === "bearish") {
    const conds = [
      [last > s50, `close above the 50-day (${s50.toFixed(2)})`],
      [rk.dir === "up" && rk.run >= 3, `renko up ×3 (now ${rk.dir} ×${rk.run})`],
      [r.value > 50 && r.rising === true, `RSI above 50 and rising (now ${r.value}${r.rising ? " rising" : " falling"})`],
    ] as const;
    reversalConfirmed = conds.every(([ok]) => ok);
    reversalNote = reversalConfirmed ? "yes — structure, pressure and strength all turned" : "no — needs " + conds.filter(([ok]) => !ok).map(([, t]) => t).join("; ");
  } else if (direction === "bullish") {
    const conds = [
      [last < s50, `close below the 50-day (${s50.toFixed(2)})`],
      [rk.dir === "down" && rk.run >= 3, `renko down ×3 (now ${rk.dir} ×${rk.run})`],
      [r.value < 50 && r.rising === false, `RSI below 50 and falling (now ${r.value})`],
    ] as const;
    reversalConfirmed = conds.every(([ok]) => ok);
    reversalNote = reversalConfirmed ? "breakdown confirmed" : "no breakdown — would need " + conds.filter(([ok]) => !ok).map(([, t]) => t).join("; ");
  } else {
    reversalNote = "no established trend to reverse";
  }
  const tone: TrendRead["tone"] = direction === "bullish" ? (stage.startsWith("accelerating") ? "good" : "warn")
    : direction === "bearish" ? (reversalConfirmed ? "good" : stage.startsWith("short-term") ? "warn" : "bad") : "muted";
  const detail = `renko ${rk.dir} ×${rk.run} since ${rk.since} (brick ${rk.brick?.toFixed(2)}) · RSI14 ${r.value} ${r.rising ? "rising" : "falling"} · close ${last.toFixed(2)} vs 50-day ${s50.toFixed(2)}${s200 != null ? ` / 200-day ${s200.toFixed(2)}` : ""} · 50-day ${rising50 ? "rising" : "falling"}`;
  return { direction, stage, reversalConfirmed, reversalNote, tone, detail };
}

/** Kept for callers that want one label; built from trendRead. */
export function momentumRead(candles: Candle[]): { label: string; tone: "good" | "warn" | "bad" | "muted"; detail: string } {
  const t = trendRead(candles);
  if (t.direction === "unknown") return { label: "momentum: not enough bars", tone: "muted", detail: "" };
  return { label: `Direction: ${t.direction} · Stage: ${t.stage} · Reversal confirmed: ${t.reversalConfirmed == null ? "n/a" : t.reversalConfirmed ? "yes" : "no"}`,
           tone: t.tone, detail: `${t.reversalNote} · ${t.detail}` };
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
