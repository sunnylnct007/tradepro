/**
 * chartBySymbol — single reusable helper that maps a bare symbol to its
 * Ichimoku chart figure, pulled from the per-strategy paper snapshots.
 *
 * Used by every surface that turns a position into a chart (PositionsPanel
 * click-to-chart, PositionChartsCard tiles) so the matching rule lives in ONE
 * place: chart keys are "ichimoku_cloud:EURUSD" → keyed by the upper-cased
 * symbol after the colon. bareTicker/bareSymbol upstream maps broker tickers
 * (AAPL_US_EQ, CS.D.EURUSD.MINI.IP) to that same bare form.
 */
import type { LatestSession } from "../types/cockpit";

export function buildChartBySymbol(
  latestSessions: LatestSession[],
): Map<string, unknown> {
  const m = new Map<string, unknown>();
  for (const s of latestSessions) {
    for (const [name, fig] of Object.entries(s.charts ?? {})) {
      const idx = name.indexOf(":");
      const sym = (idx > 0 ? name.slice(idx + 1) : name).toUpperCase();
      if (!m.has(sym)) m.set(sym, fig);
    }
  }
  return m;
}
