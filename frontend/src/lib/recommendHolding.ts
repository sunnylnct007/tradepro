import type { CompareRow, SwingScore } from "../api/types";

/**
 * Client-side mirror of the Python `analyse_holding` engine
 * (strategies/tradepro_strategies/holdings.py). Same thresholds,
 * same priority order — kept in lock-step intentionally so the
 * dashboard, email digest, and MCP all hand out identical advice.
 */

export interface HoldingLite {
  ticker: string | null;
  yahooSymbol: string | null;
  instrumentName: string | null;
  currency: string | null;
  quantity: number;
  averagePricePaid: number | null;
  currentPrice: number | null;
  unrealisedPct: number | null;
}

export type HoldingAction = "BUY_MORE" | "HOLD" | "TRIM";
export type Horizon = "6mo" | "1y" | "3y" | "5y";

export interface HoldingRecommendation {
  symbol: string;
  action: HoldingAction;
  narrative: string;
  horizon: Horizon;
  evidence: string[];
  avgCostAfterEqualTranche: number | null;
}

interface HorizonProfile {
  trim_rsi_min: number;
  avg_down_rsi: number;
  intact_swing: number;
  broken_swing: number;
  tolerate_wait: boolean;
}

const AVG_DOWN_LOSS_PCT = -3.0;
const TAKE_PROFIT_GAIN_PCT = 15.0;

const HORIZON_PROFILES: Record<Horizon, HorizonProfile> = {
  "6mo": { trim_rsi_min: 60, avg_down_rsi: 35, intact_swing: 5, broken_swing: 2, tolerate_wait: false },
  "1y":  { trim_rsi_min: 65, avg_down_rsi: 35, intact_swing: 4, broken_swing: 1, tolerate_wait: false },
  "3y":  { trim_rsi_min: 75, avg_down_rsi: 30, intact_swing: 3, broken_swing: 1, tolerate_wait: true },
  "5y":  { trim_rsi_min: 80, avg_down_rsi: 30, intact_swing: 2, broken_swing: 0, tolerate_wait: true },
};

export const DEFAULT_HORIZON: Horizon = "1y";

export function recommendHolding(
  holding: HoldingLite,
  row: CompareRow | null,
  horizon: Horizon = DEFAULT_HORIZON,
): HoldingRecommendation {
  const sym = (holding.yahooSymbol ?? holding.ticker ?? holding.instrumentName ?? "—").toUpperCase();
  const upct = num(holding.unrealisedPct);
  const avgCost = num(holding.averagePricePaid);
  const current = num(holding.currentPrice);
  const profile = HORIZON_PROFILES[horizon];

  if (!row) {
    return {
      symbol: sym,
      action: "HOLD",
      horizon,
      narrative: `Not in any tracked universe — no action recommendation; run evaluate_symbols("${sym}") for an ad-hoc verdict.`,
      evidence: [],
      avgCostAfterEqualTranche: null,
    };
  }

  const bucket = (row.bucket ?? "").toUpperCase();
  const swing: SwingScore | null = row.swing_score ?? null;
  const swingTotal = swing?.total ?? null;
  const ms = row.market_state;
  const rsi = num(ms?.rsi_14);
  const aboveSma = ms?.above_sma_200;

  const inAvgDownZone =
    upct !== null && upct <= AVG_DOWN_LOSS_PCT &&
    rsi !== null && rsi <= profile.avg_down_rsi;
  const inTakeProfitZone =
    upct !== null && upct >= TAKE_PROFIT_GAIN_PCT &&
    rsi !== null && rsi >= profile.trim_rsi_min;
  const structurallyIntact =
    bucket === "BUY" ||
    (profile.tolerate_wait && bucket === "WAIT") ||
    (swingTotal !== null && swingTotal >= profile.intact_swing);
  const structurallyBroken =
    bucket === "AVOID" ||
    (swingTotal !== null && swingTotal <= profile.broken_swing);

  const evidence: string[] = [`horizon ${horizon}`];
  if (upct !== null) evidence.push(`position ${upct >= 0 ? "+" : ""}${upct.toFixed(2)}% vs cost`);
  if (rsi !== null) evidence.push(`RSI ${rsi.toFixed(0)}`);
  if (aboveSma === true) evidence.push("above 200d SMA");
  else if (aboveSma === false) evidence.push("below 200d SMA");
  if (bucket) evidence.push(`bucket ${bucket}`);
  if (swingTotal !== null && swing) evidence.push(`swing ${swingTotal}/8 (${swing.verdict ?? "?"})`);

  // 1. Structurally broken + in profit → TRIM
  if (structurallyBroken && upct !== null && upct > 0) {
    return {
      symbol: sym,
      action: "TRIM",
      horizon,
      narrative:
        `${sym} is in profit (${upct >= 0 ? "+" : ""}${upct.toFixed(1)}%) but the system says ` +
        `AVOID / swing ${swingTotal ?? "?"}/8 — consider trimming before the trend gives back what you've earned.`,
      evidence,
      avgCostAfterEqualTranche: null,
    };
  }

  // 2. Take-profit zone → TRIM
  if (inTakeProfitZone) {
    return {
      symbol: sym,
      action: "TRIM",
      horizon,
      narrative:
        `${sym} is up ${upct! >= 0 ? "+" : ""}${upct!.toFixed(1)}% with RSI ${rsi!.toFixed(0)} — both signals ` +
        `suggest momentum is overextended. Consider trimming a partial position to lock in gains.`,
      evidence,
      avgCostAfterEqualTranche: null,
    };
  }

  // 3. Average-down zone + thesis intact → BUY_MORE
  if (inAvgDownZone && structurallyIntact) {
    let avgAfter: number | null = null;
    if (avgCost !== null && avgCost > 0 && current !== null && current > 0) {
      avgAfter = (avgCost + current) / 2.0;
    }
    const ccy = holding.currency ?? "";
    const partA =
      `${sym} is down ${upct! >= 0 ? "+" : ""}${upct!.toFixed(1)}% with RSI ${rsi!.toFixed(0)} (oversold zone) ` +
      `and the structural thesis is intact (bucket ${bucket}, swing ${swingTotal ?? "?"}/8). Classic average-down opportunity.`;
    const partB = (avgAfter !== null && avgCost !== null && current !== null)
      ? ` Adding an equal tranche at ${current.toFixed(2)} ${ccy} would bring your cost basis from ${avgCost.toFixed(2)} to ${avgAfter.toFixed(2)}.`
      : "";
    return {
      symbol: sym,
      action: "BUY_MORE",
      horizon,
      narrative: partA + partB,
      evidence,
      avgCostAfterEqualTranche: avgAfter,
    };
  }

  // 4. Default → HOLD
  let qual: string;
  if (bucket === "BUY" && upct !== null && upct > 0) {
    qual = "in profit and the system still rates BUY — let it run.";
  } else if (bucket === "WAIT") {
    qual = "system says WAIT — don't add until trend confirms.";
  } else if (bucket === "AVOID" && upct !== null && upct <= 0) {
    qual =
      "system says AVOID and you're at break-even or worse — consider whether the structural thesis still holds before averaging into a damaged position.";
  } else {
    qual = "no fresh edge in either direction; hold the line.";
  }
  return {
    symbol: sym,
    action: "HOLD",
    horizon,
    narrative: `${sym}: ${qual}`,
    evidence,
    avgCostAfterEqualTranche: null,
  };
}

function num(v: number | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v !== "number" || Number.isNaN(v) || !Number.isFinite(v)) return null;
  return v;
}
