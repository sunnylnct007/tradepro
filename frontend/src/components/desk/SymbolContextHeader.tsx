/**
 * One line above the chart that answers the owner's question before the
 * chart does (review item 15, 28 Nov 2026): the symbol's day against its
 * sector ETF, QQQ and SPY; the symbol's and the sector's regime (50/200-day);
 * the momentum read (daily trend → renko → RSI); the next dated events.
 *
 * Clarity rules: one answer at a glance, no legend, fixed colours; futures
 * are a FIGURE here when a feed exists — today there is none, and the line
 * says "no futures feed" rather than inventing one.
 */
import { useEffect, useState } from "react";

import { api } from "../../api/client";
import type { Candle } from "../../api/types";
import { pct, regimeOf, trendRead } from "./chartContext";
import { sectorEtfFor } from "./sectorEtf";

export type HeaderEvent = { on: string; kind: string; title: string };
const TONE = { good: "#3fb950", warn: "#d29922", bad: "#f85149", muted: "var(--text-muted)", dim: "var(--text-dim)" };
const COLOUR: Record<string, string> = { SPY: "#9aa4b2", QQQ: "#4f8cff" };

function fmt(p: number | null): string { return p == null ? "—" : `${p > 0 ? "+" : ""}${p.toFixed(1)}%`; }
function tone(p: number | null): string { return p == null ? TONE.muted : p >= 0 ? TONE.good : TONE.bad; }

export function useDailyCandles(symbols: string[], days = 400): Record<string, Candle[]> {
  const [out, setOut] = useState<Record<string, Candle[]>>({});
  const key = symbols.join(",");
  useEffect(() => {
    let live = true;
    const from = new Date(Date.now() - days * 86400_000).toISOString().slice(0, 10);
    Promise.all(symbols.map((s) => api.candles({ symbol: s, interval: "1d", from }).then((r) => [s, r.candles ?? []] as const).catch(() => [s, []] as const)))
      .then((pairs) => { if (live) setOut(Object.fromEntries(pairs)); });
    return () => { live = false; };
  }, [key, days]);  // eslint-disable-line react-hooks/exhaustive-deps
  return out;
}

export function useSymbolEvents(symbol: string, aheadDays = 60): HeaderEvent[] {
  const [ev, setEv] = useState<HeaderEvent[]>([]);
  useEffect(() => {
    let live = true;
    Promise.all([
      api.catalysts({ symbol, lookbackDays: 0, lookaheadDays: aheadDays, status: "active" }).catch(() => null),
      api.catalysts({ symbol: "MARKET", lookbackDays: 0, lookaheadDays: aheadDays, status: "active" }).catch(() => null),
      api.earningsCalendar(symbol, 0, aheadDays).catch(() => null),
    ]).then(([own, mkt, earn]) => {
      if (!live) return;
      const rows: HeaderEvent[] = [];
      for (const c of [...(own?.catalysts ?? []), ...(mkt?.catalysts ?? [])]) if (c.occurs_on) rows.push({ on: c.occurs_on.slice(0, 10), kind: c.kind, title: c.title });
      for (const e of earn?.events ?? []) if (e.report_date) rows.push({ on: String(e.report_date).slice(0, 10), kind: "earnings", title: `${symbol.toUpperCase()} earnings${e.session ? ` (${e.session})` : ""}` });
      const seen = new Set<string>();
      setEv(rows.filter((r) => { const k = `${r.on}|${r.kind}`; if (seen.has(k)) return false; seen.add(k); return true; }).sort((a, b) => a.on.localeCompare(b.on)));
    });
    return () => { live = false; };
  }, [symbol, aheadDays]);
  return ev;
}

export function SymbolContextHeader({ symbol, sector, candles, events }: {
  symbol: string; sector?: string | null; candles: Record<string, Candle[]>; events: HeaderEvent[];
}) {
  const { etf, fallback } = sectorEtfFor(symbol, sector);
  const own = candles[symbol] ?? [];
  const day = (s: string) => { const c = candles[s] ?? []; return c.length >= 2 ? pct(c[c.length - 1].close, c[c.length - 2].close) : null; };
  const asOf = own.length ? own[own.length - 1].timestamp.slice(0, 10) : null;
  const reg = own.length ? regimeOf(own) : null;
  const sreg = candles[etf]?.length ? regimeOf(candles[etf]) : null;
  const tr = own.length ? trendRead(own) : null;
  const today = new Date().toISOString().slice(0, 10);
  const next = events.filter((e) => e.on >= today).slice(0, 3);
  const daysTo = (iso: string) => Math.round((new Date(iso + "T00:00:00Z").getTime() - new Date(today + "T00:00:00Z").getTime()) / 86400_000);
  const regTxt = (r: typeof reg) => r == null ? "—" : `${r.above50 == null ? "" : (r.above50 ? "above" : "below") + " 50-day"}${r.above200 == null ? "" : `, ${r.above200 ? "above" : "below"} 200`}`;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", alignItems: "baseline", fontSize: 12, lineHeight: 1.5, marginBottom: 6 }}
         title={asOf ? `settled daily closes to ${asOf}` : undefined}>
      <span><b style={{ fontSize: 13 }}>{symbol}</b> <b style={{ color: tone(day(symbol)) }}>{fmt(day(symbol))}</b> <span style={{ color: TONE.muted }}>{asOf ? (asOf === today ? "today" : asOf.slice(5)) : ""}</span></span>
      <span style={{ color: "#d29922" }}>{etf}{fallback ? "*" : ""} <b style={{ color: tone(day(etf)) }}>{fmt(day(etf))}</b></span>
      <span style={{ color: COLOUR.QQQ }}>QQQ <b style={{ color: tone(day("QQQ")) }}>{fmt(day("QQQ"))}</b></span>
      <span style={{ color: COLOUR.SPY }}>SPY <b style={{ color: tone(day("SPY")) }}>{fmt(day("SPY"))}</b></span>
      <span style={{ color: TONE.dim }}>{symbol} {regTxt(reg)} · {etf} {regTxt(sreg)}</span>
      {tr && tr.direction !== "unknown" && (
        <span title={`${tr.reversalNote} · ${tr.detail}`} style={{ color: TONE[tr.tone] }}>
          Direction: <b>{tr.direction}</b> · Stage: <b>{tr.stage}</b> · Reversal confirmed: <b>{tr.reversalConfirmed == null ? "n/a" : tr.reversalConfirmed ? "yes" : "no"}</b>
          {tr.reversalConfirmed === false && <span style={{ color: TONE.muted }}> ({tr.reversalNote.replace(/^no( breakdown)? — /, "")})</span>}
        </span>
      )}
      <span style={{ color: TONE.dim }}>
        {next.length ? next.map((e) => `${e.kind === "earnings" ? "E" : e.kind === "fomc" ? "F" : e.kind === "cpi" ? "C" : e.kind[0].toUpperCase()} ${e.title.replace(/ \(.*\)$/, "")} in ${daysTo(e.on)}d`).join(" · ") : "no dated events in 60 days"}
      </span>
      <span style={{ color: TONE.muted, fontSize: 10.5 }}>no futures feed{fallback ? ` · *${symbol} has no sector map; SPY used` : ""}</span>
    </div>
  );
}
