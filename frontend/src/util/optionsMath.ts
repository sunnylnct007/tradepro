/**
 * Compact Black-Scholes for the options payoff explorer (Sensibull-style):
 * price, implied vol (from a quoted mid), greeks, and probability of profit.
 * European, non-dividend, flat rate — good enough for the explorer's "what-if"
 * curves and greeks; execution still uses live broker quotes. r defaults to 4%.
 */
export type OptType = "put" | "call";

const SQRT2PI = Math.sqrt(2 * Math.PI);

export function normPdf(x: number): number {
  return Math.exp(-(x * x) / 2) / SQRT2PI;
}

// Abramowitz & Stegun 7.1.26 — ~1e-7 accuracy, plenty for greeks/PoP.
export function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-(x * x) / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}

export function bsPrice(type: OptType, S: number, K: number, t: number, vol: number, r = 0.04): number {
  if (t <= 0 || vol <= 0 || S <= 0 || K <= 0) {
    return type === "call" ? Math.max(0, S - K) : Math.max(0, K - S);
  }
  const sqt = Math.sqrt(t);
  const d1 = (Math.log(S / K) + (r + (vol * vol) / 2) * t) / (vol * sqt);
  const d2 = d1 - vol * sqt;
  return type === "call"
    ? S * normCdf(d1) - K * Math.exp(-r * t) * normCdf(d2)
    : K * Math.exp(-r * t) * normCdf(-d2) - S * normCdf(-d1);
}

export interface Greeks {
  delta: number;  // per share
  gamma: number;  // Δ change per $1 move
  theta: number;  // per share, per calendar day
  vega: number;   // per share, per 1 vol-point (1%)
}

export function greeks(type: OptType, S: number, K: number, t: number, vol: number, r = 0.04): Greeks {
  if (t <= 0 || vol <= 0 || S <= 0 || K <= 0) {
    const delta = type === "call" ? (S > K ? 1 : 0) : (S < K ? -1 : 0);
    return { delta, gamma: 0, theta: 0, vega: 0 };
  }
  const sqt = Math.sqrt(t);
  const d1 = (Math.log(S / K) + (r + (vol * vol) / 2) * t) / (vol * sqt);
  const d2 = d1 - vol * sqt;
  const pdf = normPdf(d1);
  const delta = type === "call" ? normCdf(d1) : normCdf(d1) - 1;
  const gamma = pdf / (S * vol * sqt);
  const vega = (S * pdf * sqt) / 100;
  const theta =
    (type === "call"
      ? -S * pdf * vol / (2 * sqt) - r * K * Math.exp(-r * t) * normCdf(d2)
      : -S * pdf * vol / (2 * sqt) + r * K * Math.exp(-r * t) * normCdf(-d2)) / 365;
  return { delta, gamma, theta, vega };
}

/** Implied vol from a quoted price via bisection. null if not solvable. */
export function impliedVol(type: OptType, price: number, S: number, K: number, t: number, r = 0.04): number | null {
  if (price <= 0 || t <= 0 || S <= 0 || K <= 0) return null;
  const intrinsic = type === "call" ? Math.max(0, S - K) : Math.max(0, K - S);
  if (price < intrinsic - 1e-6) return null;
  let lo = 1e-3, hi = 5;
  for (let i = 0; i < 100; i++) {
    const mid = (lo + hi) / 2;
    const p = bsPrice(type, S, K, t, mid, r);
    if (Math.abs(p - price) < 1e-4) return mid;
    if (p > price) hi = mid; else lo = mid;
  }
  return (lo + hi) / 2;
}

/**
 * Probability the underlying finishes ABOVE a level at expiry (risk-neutral,
 * lognormal). For a short put, pass breakeven → probability of keeping a profit.
 */
export function probAbove(S: number, level: number, t: number, vol: number, r = 0.04): number {
  if (t <= 0 || vol <= 0 || S <= 0 || level <= 0) return S > level ? 1 : 0;
  const d2 = (Math.log(S / level) + (r - (vol * vol) / 2) * t) / (vol * Math.sqrt(t));
  return normCdf(d2);
}
