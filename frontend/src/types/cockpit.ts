/**
 * Shared types for the trader cockpit + cockpit components. Lifted
 * out of TraderCockpit.tsx so per-widget files import the shape
 * without crossing the cockpit shell file (and so the shell stays
 * readable).
 */
import type { api } from "../api/client";

export type T212Cash = Awaited<ReturnType<typeof api.t212Cash>>;

export type DecisionEntry = {
  barTs: string | null;
  symbol: string;
  action: string;
  reason: string;
  detail: Record<string, unknown>;
};

export type LatestSession = {
  strategy: string;
  requestId: string;
  completedAtUtc: string | null;
  decisions: DecisionEntry[];
  barsSeen: number;
  /**
   * Per-strategy Plotly figure dicts emitted by the strategy's
   * recent_charts() hook (ichimoku_equity → one cloud chart per
   * symbol). Renders inside StrategyChartsCard.
   */
  charts: Record<string, unknown>;
};

export type T212PosResp = {
  enabled: boolean;
  mode: string;
  positionCount: number;
  positions: Array<{
    ticker: string;
    yahooSymbol: string | null;
    quantity: number;
    averagePricePaid: number | null;
    currentPrice: number | null;
    unrealisedPct: number | null;
    unrealisedAbs: number | null;
    currency: string | null;
  }>;
  error?: string | null;
  fromCache?: boolean;
  ageSeconds?: number;
};
