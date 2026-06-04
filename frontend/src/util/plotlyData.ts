/**
 * plotlyData — pull plain arrays out of the backend's Plotly ichimoku_cloud
 * figure so a non-Plotly renderer (lightweight-charts, TradingView's lib) can
 * draw candles + Ichimoku overlays + fill markers. Plotly encodes numeric
 * arrays as typed-array specs ({dtype, bdata: base64}); decode those here.
 */
export type Candle = { time: number; open: number; high: number; low: number; close: number };
export type LinePoint = { time: number; value: number };
export type FillMarker = { time: number; side: "BUY" | "SELL"; price: number };

export type IchimokuSeries = {
  candles: Candle[];
  tenkan: LinePoint[];
  kijun: LinePoint[];
  senkouA: LinePoint[];
  senkouB: LinePoint[];
  markers: FillMarker[];
};

/** Decode a Plotly array value (plain list OR {dtype,bdata} typed array). */
function decodeArray(y: unknown): number[] {
  if (Array.isArray(y)) return y.map((v) => Number(v));
  if (y && typeof y === "object" && "bdata" in (y as Record<string, unknown>)) {
    const o = y as { dtype?: string; bdata: string };
    const bin = atob(o.bdata);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    const dv = new DataView(buf.buffer);
    const out: number[] = [];
    const dt = o.dtype || "f8";
    if (dt === "f4") for (let i = 0; i + 4 <= buf.length; i += 4) out.push(dv.getFloat32(i, true));
    else if (dt === "i4") for (let i = 0; i + 4 <= buf.length; i += 4) out.push(dv.getInt32(i, true));
    else for (let i = 0; i + 8 <= buf.length; i += 8) out.push(dv.getFloat64(i, true)); // f8 / default
    return out;
  }
  return [];
}

const toSec = (iso: string): number => Math.floor(Date.parse(iso) / 1000);

function unwrap(figure: unknown): { data: unknown[]; } | null {
  if (!figure || typeof figure !== "object") return null;
  const f = figure as Record<string, unknown>;
  const inner = (Array.isArray(f.data) ? f : (f.figure as Record<string, unknown>)) ?? f;
  if (!inner || !Array.isArray((inner as Record<string, unknown>).data)) return null;
  return { data: (inner as Record<string, unknown>).data as unknown[] };
}

/** Extract candles + Ichimoku lines + BUY/SELL markers from the figure. */
export function extractIchimoku(figure: unknown): IchimokuSeries | null {
  const fig = unwrap(figure);
  if (!fig) return null;
  const out: IchimokuSeries = { candles: [], tenkan: [], kijun: [], senkouA: [], senkouB: [], markers: [] };

  const line = (t: Record<string, unknown>): LinePoint[] => {
    const x = (t.x as string[]) ?? [];
    const y = decodeArray(t.y);
    const pts: LinePoint[] = [];
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      if (Number.isFinite(y[i])) pts.push({ time: toSec(x[i]), value: y[i] });
    }
    return dedupeSorted(pts) as LinePoint[];
  };

  // Senkou A is the first (unnamed) line trace; Senkou B is the "Cloud" fill trace.
  let sawFirstLine = false;
  for (const trRaw of fig.data) {
    const t = trRaw as Record<string, unknown>;
    const type = t.type as string | undefined;
    const name = (t.name as string | undefined) ?? "";

    if (type === "candlestick") {
      const x = (t.x as string[]) ?? [];
      const o = decodeArray(t.open), h = decodeArray(t.high), l = decodeArray(t.low), c = decodeArray(t.close);
      for (let i = 0; i < x.length; i++) {
        if ([o[i], h[i], l[i], c[i]].every(Number.isFinite)) {
          out.candles.push({ time: toSec(x[i]), open: o[i], high: h[i], low: l[i], close: c[i] });
        }
      }
      out.candles = dedupeSorted(out.candles) as Candle[];
    } else if (name === "Cloud") {
      out.senkouB = line(t);
    } else if (/^Tenkan/.test(name)) {
      out.tenkan = line(t);
    } else if (/^Kijun/.test(name)) {
      out.kijun = line(t);
    } else if (name === "Buys" || name === "Sells") {
      const x = (t.x as string[]) ?? [];
      const y = decodeArray(t.y);
      for (let i = 0; i < Math.min(x.length, y.length); i++) {
        out.markers.push({ time: toSec(x[i]), side: name === "Buys" ? "BUY" : "SELL", price: y[i] });
      }
    } else if ((type === "scatter" || type === undefined) && !sawFirstLine && (t.line as Record<string, unknown>)?.width === 0) {
      // First zero-width scatter = Senkou A (cloud base).
      out.senkouA = line(t);
      sawFirstLine = true;
    }
  }
  if (out.candles.length === 0) return null;
  return out;
}

function dedupeSorted<T extends { time: number }>(pts: T[]): T[] {
  const m = new Map<number, T>();
  for (const p of pts) m.set(p.time, p);  // last write wins per timestamp
  return [...m.values()].sort((a, b) => a.time - b.time);
}
