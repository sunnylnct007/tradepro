/**
 * ONE definition of the chart's resolutions and the ranges each offers —
 * shared by the symbol chart in the rail and the Data screen's flexible chart.
 *
 * 21 Nov 2026, owner: "i do not see 5 min candle selection on graph anymore".
 * The rail chart only ever had daily pills; the 5-minute choice lived on the
 * Data screen with its own copy of this table. Two copies is how one surface
 * loses an option the other keeps. Every resolution is served by
 * /marketdata/candles from the golden bar store (docs/DATA_ARCHITECTURE.md);
 * a symbol the intraday lanes do not harvest renders the chart's own
 * "No data" state at 1m/5m, never a daily series dressed up as intraday.
 */
export const TF_FOR_RES: Record<string, readonly string[]> = {
  "1m": ["1D", "5D", "1M"],
  "5m": ["1D", "5D", "1M", "3M"],
  "1d": ["1M", "3M", "6M", "1Y", "5Y"],
};

export const RES_OPTS = [
  { k: "1m", label: "1-min" },
  { k: "5m", label: "5-min" },
  { k: "1d", label: "Daily" },
] as const;

export type ChartRes = (typeof RES_OPTS)[number]["k"];

/** The range to show when `res` changes: keep `tf` if that resolution offers
 *  it, else that resolution's first range. */
export function rangeFor(res: string, tf: string): string {
  const tfs = TF_FOR_RES[res] ?? TF_FOR_RES["1d"];
  return tfs.includes(tf) ? tf : tfs[0];
}
