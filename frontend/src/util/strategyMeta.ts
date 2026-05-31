/**
 * strategyMeta — the single editable source of truth for how the UI
 * frames each strategy: who owns it (trader / desk), what it leans on
 * internally (indicators — descriptive only), and how it executes.
 *
 * Mental model (per the trader):
 *   - A STRATEGY generates trading signals. It may lean on technical
 *     indicators internally, but the indicator is NOT the strategy —
 *     indicators are analytical primitives that surfaces like /decide
 *     use to show trend. The catalog lists signal generators, not
 *     indicators.
 *   - A TRADER (desk persona) owns one or more strategies. Ownership is
 *     1-trader-to-many-strategies.
 *   - EXECUTION MODE is derived from the strategy → broker mapping:
 *       • LIVE        — mapped to a real broker (T212_* / IG_* / IBKR_*);
 *                       approved orders route to that broker.
 *       • SIGNAL-ONLY — mapped to PAPER, or unmapped. The strategy still
 *                       generates + records signals (OMS keeps them for
 *                       evaluation) but nothing is placed on a real
 *                       broker. This is how we judge a signal's quality
 *                       before trusting it with money — and the default
 *                       home for the options work.
 *
 * Rename desks freely — this map is the only place trader/owner labels
 * live, so both the catalog and the cockpit desks read the same names.
 */

export type DeskId = "trend" | "mean_reversion" | "intraday" | "options";

export type Desk = {
  id: DeskId;
  /** Friendly owner label shown across the UI. Editable. */
  trader: string;
  blurb: string;
};

export const DESKS: Record<DeskId, Desk> = {
  trend: {
    id: "trend",
    trader: "Trend Desk",
    blurb: "Rides established trends in US equities.",
  },
  mean_reversion: {
    id: "mean_reversion",
    trader: "Mean-Reversion Desk",
    blurb: "Fades stretched moves back toward the mean (equity + FX).",
  },
  intraday: {
    id: "intraday",
    trader: "Intraday Desk",
    blurb: "Opens and closes within the session — flat by the bell.",
  },
  options: {
    id: "options",
    trader: "Options Desk",
    blurb: "Defined-risk options spreads — signal-only for now.",
  },
};

const UNASSIGNED: Desk = {
  id: "trend",
  trader: "Unassigned",
  blurb: "No desk owner set — add it in strategyMeta.ts.",
};

export type AssetClass = "Equity" | "FX" | "Options";

export type StrategyMeta = {
  desk: DeskId;
  /** Indicators the strategy leans on internally. DESCRIPTIVE ONLY —
   * shown so a newcomer understands what's under the hood, not a claim
   * that the indicator is itself a strategy. */
  indicators: string[];
  assetClass: AssetClass;
  /** Broker this strategy routes to when set LIVE. null ⇒ no broker is
   * plugged, so the strategy can only run signal-only (e.g. options). */
  liveBroker: string | null;
};

export const STRATEGY_META: Record<string, StrategyMeta> = {
  // ── Trend Desk ────────────────────────────────────────────────
  ichimoku_equity: { desk: "trend", indicators: ["Ichimoku Cloud"], assetClass: "Equity", liveBroker: "T212_DEMO" },
  ma_crossover: { desk: "trend", indicators: ["EMA fast/slow crossover"], assetClass: "Equity", liveBroker: null },
  compass_momentum: { desk: "trend", indicators: ["COMPASS momentum"], assetClass: "Equity", liveBroker: null },

  // ── Mean-Reversion Desk ───────────────────────────────────────
  ichimoku_fx_mr: { desk: "mean_reversion", indicators: ["Ichimoku", "mean reversion"], assetClass: "FX", liveBroker: "IG_DEMO" },
  vwap_mean_reversion: { desk: "mean_reversion", indicators: ["VWAP fade"], assetClass: "Equity", liveBroker: null },
  bollinger_bounce: { desk: "mean_reversion", indicators: ["Bollinger Bands"], assetClass: "Equity", liveBroker: null },

  // ── Intraday Desk ─────────────────────────────────────────────
  intraday_flat: { desk: "intraday", indicators: ["scanner basket", "EOD flat"], assetClass: "Equity", liveBroker: "IG_DEMO" },
  orb: { desk: "intraday", indicators: ["opening-range breakout"], assetClass: "Equity", liveBroker: null },
  // Long-form alias kept by the registry for back-compat; same desk as orb.
  opening_range_breakout: { desk: "intraday", indicators: ["opening-range breakout"], assetClass: "Equity", liveBroker: null },
};

/** Registry names that are pure aliases of another strategy — filtered
 * out of the catalog so a strategy doesn't appear twice. */
export const STRATEGY_ALIASES = new Set<string>(["opening_range_breakout"]);

export type ExecutionMode = "live" | "signal-only";

const REAL_BROKER_RE = /^(T212|IG|IBKR)_/;

/** True when a broker string routes to a real venue (not PAPER / unset). */
export function isRealBroker(broker: string | null | undefined): boolean {
  return !!broker && REAL_BROKER_RE.test(broker);
}

/** Execution mode for a strategy given its current broker mapping. */
export function executionMode(mappedBroker: string | null | undefined): ExecutionMode {
  return isRealBroker(mappedBroker) ? "live" : "signal-only";
}

export function metaFor(strategyId: string): StrategyMeta | undefined {
  return STRATEGY_META[strategyId];
}

export function deskFor(strategyId: string): Desk {
  const m = STRATEGY_META[strategyId];
  return m ? DESKS[m.desk] : UNASSIGNED;
}

/** Stable display order for desks in grouped views. */
export const DESK_ORDER: DeskId[] = ["trend", "mean_reversion", "intraday", "options"];
