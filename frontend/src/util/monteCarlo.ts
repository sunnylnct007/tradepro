/**
 * Client-side block-bootstrap Monte Carlo — mirrors the Python quant engine
 * (`quant_engine/monte_carlo.py`) so per-symbol projections can run in the
 * browser from candle returns without a backend round-trip. The portfolio /
 * strategy MC is still computed server-side (richer, ensemble returns); this
 * produces the SAME data shape (`MonteCarloData`) so one renderer serves both.
 *
 * Method: draw random BLOCKS of consecutive daily returns (default 21 ≈ 1
 * trading month) and stitch them into synthetic equity paths. Blocks (not IID
 * single days) preserve autocorrelation + fat tails. Each path compounds from
 * `initial`. We report the distribution of final values + max drawdowns, and a
 * down-sampled percentile fan over time.
 *
 * Honest-by-construction: returns null when there isn't enough history to
 * sample (callers render "not enough data" rather than a fabricated cone).
 */

/** Percentile-fan + summary payload — matches the server MC shape so the same
 *  chips + fan chart render server (portfolio) and client (per-symbol) results. */
export interface MonteCarloData {
  fan_chart: {
    years_axis: number[];
    q05: number[];
    q25: number[];
    q50: number[];
    q75: number[];
    q95: number[];
  };
  summary: {
    percentiles: Record<string, { final_value: number; multiple: number; cagr_pct: number }>;
    p_double: number;
    p_5x: number;
    p_lose_money: number;
    max_dd_pct: { p50: number };
  };
  n_sims: number;
  years: number;
  initial: number;
  source: "client" | "server";
}

export interface MonteCarloOpts {
  years?: number;
  nSims?: number;
  initial?: number;
  blockSize?: number;
  periodsPerYear?: number;
  seed?: number;
  /** ~number of x-axis points in the fan (paths are sampled to keep it light). */
  fanPoints?: number;
}

/** Deterministic RNG (mulberry32) — same seed ⇒ same projection (reproducible). */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

/** Daily simple returns from a close series (drops non-finite + the first NaN). */
export function returnsFromCloses(closes: Array<number | null | undefined>): number[] {
  const c = closes.filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0);
  const out: number[] = [];
  for (let i = 1; i < c.length; i++) out.push(c[i] / c[i - 1] - 1);
  return out;
}

/**
 * Run the block-bootstrap. Returns null when `returns` has fewer than
 * `blockSize` observations (can't draw a single block honestly).
 */
export function runBlockBootstrapMC(
  returns: number[],
  opts: MonteCarloOpts = {},
): MonteCarloData | null {
  const years = opts.years ?? 5;
  const nSims = opts.nSims ?? 500;
  const initial = opts.initial ?? 10_000;
  const blockSize = opts.blockSize ?? 21;
  const ppy = opts.periodsPerYear ?? 252;
  const seed = opts.seed ?? 42;
  const fanPoints = Math.max(2, opts.fanPoints ?? 120);

  const src = returns.filter((r) => Number.isFinite(r));
  const nSrc = src.length;
  if (nSrc < blockSize) return null;

  const nDays = Math.max(1, Math.round(years * ppy));
  const rng = mulberry32(seed);

  // Sample indices for the fan (down-sampled time axis, always incl. day 0 + last).
  const step = Math.max(1, Math.floor(nDays / (fanPoints - 1)));
  const sampleIdx: number[] = [];
  for (let d = 0; d <= nDays; d += step) sampleIdx.push(d);
  if (sampleIdx[sampleIdx.length - 1] !== nDays) sampleIdx.push(nDays);

  // sampledCols[k] = array of equity values across sims at sampleIdx[k].
  const sampledCols: number[][] = sampleIdx.map(() => new Array<number>(nSims));
  const finals = new Array<number>(nSims);
  const maxDDs = new Array<number>(nSims);

  for (let s = 0; s < nSims; s++) {
    let equity = initial;
    let peak = initial;
    let maxDD = 0; // most-negative (equity-peak)/peak
    let day = 0;
    let sampleK = 0;
    // record day 0
    if (sampleIdx[sampleK] === 0) sampledCols[sampleK++][s] = equity;

    while (day < nDays) {
      const start = Math.floor(rng() * Math.max(1, nSrc - blockSize + 1));
      const take = Math.min(blockSize, nDays - day);
      for (let b = 0; b < take; b++) {
        equity *= 1 + src[start + b];
        day++;
        if (equity > peak) peak = equity;
        const dd = peak > 0 ? (equity - peak) / peak : 0;
        if (dd < maxDD) maxDD = dd;
        while (sampleK < sampleIdx.length && sampleIdx[sampleK] === day) {
          sampledCols[sampleK++][s] = equity;
        }
      }
    }
    finals[s] = equity;
    maxDDs[s] = maxDD * 100; // as percent (negative)
  }

  // Per-step percentiles for the fan.
  const q05: number[] = [], q25: number[] = [], q50: number[] = [], q75: number[] = [], q95: number[] = [];
  for (let k = 0; k < sampleIdx.length; k++) {
    const col = sampledCols[k].slice().sort((a, b) => a - b);
    q05.push(percentile(col, 5));
    q25.push(percentile(col, 25));
    q50.push(percentile(col, 50));
    q75.push(percentile(col, 75));
    q95.push(percentile(col, 95));
  }
  const years_axis = sampleIdx.map((d) => d / ppy);

  // Final-value percentiles + headline probabilities.
  const finalsSorted = finals.slice().sort((a, b) => a - b);
  const pctLabels = [5, 10, 25, 50, 75, 90, 95];
  const percentiles: MonteCarloData["summary"]["percentiles"] = {};
  for (const p of pctLabels) {
    const fv = percentile(finalsSorted, p);
    percentiles[`p${p}`] = {
      final_value: Math.round(fv * 100) / 100,
      multiple: initial > 0 ? Math.round((fv / initial) * 1e4) / 1e4 : 0,
      cagr_pct: initial > 0 && years > 0 ? (Math.pow(fv / initial, 1 / years) - 1) * 100 : 0,
    };
  }
  const frac = (pred: (v: number) => boolean) => finals.reduce((n, v) => n + (pred(v) ? 1 : 0), 0) / nSims;
  const ddSorted = maxDDs.slice().sort((a, b) => a - b);

  return {
    fan_chart: { years_axis, q05, q25, q50, q75, q95 },
    summary: {
      percentiles,
      p_double: frac((v) => v >= 2 * initial),
      p_5x: frac((v) => v >= 5 * initial),
      p_lose_money: frac((v) => v < initial),
      max_dd_pct: { p50: percentile(ddSorted, 50) },
    },
    n_sims: nSims,
    years,
    initial,
    source: "client",
  };
}
