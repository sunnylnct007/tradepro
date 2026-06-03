/**
 * Number formatting helpers shared across the cockpit.
 *
 * fmtQty: strip binary-float noise from a quantity for display. OMS net
 * quantities accumulate tiny errors (e.g. summing IG mini-lots gives
 * 1.0000000000000007 or 0.9999999999999997); rounding to 6 dp collapses that
 * back to a clean 1 / 0.1 without losing genuine fractional sizes.
 */
export function fmtQty(n: number): number {
  return Number(n.toFixed(6));
}
