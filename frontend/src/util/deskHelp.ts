/**
 * deskHelp — the end-to-end "how this desk works" explainer the trader asked
 * for, one entry per LIVE strategy. Content is grounded in the actual strategy
 * source (strategies/tradepro_strategies/paper/strategies/*.py) — keep it in
 * sync when the strategy logic changes. Every claim here should be something a
 * newcomer can verify from the UI + this text alone (explainability rule).
 *
 * Shape: an ordered list of sections; the desk card renders them in a
 * collapsible panel behind a (?) icon. PIPELINE is shared (every desk routes
 * the same way) and spliced in by deskHelp() so we describe it once.
 */
export type HelpSection = { heading: string; body: string };

// Shared order lifecycle — identical for every desk, so written once.
const PIPELINE: HelpSection[] = [
  {
    heading: "How an order travels (end-to-end)",
    body:
      "1. A Python daemon for this strategy runs on the Mac (launchd, not the cloud) and evaluates the latest bar. " +
      "2. On a live bar it emits an order intent to the backend (POST /api/ingest/paper-pending-order). " +
      "3. The OMS records the intent, de-duplicates it (client-order-id + in-flight + supersede tiers) and runs the RiskGate " +
      "(market-hours window, per-strategy position cap, kill-switches). " +
      "4. Approved orders are dispatched to the strategy's broker — read from config (strategy_broker_map), never hardcoded. " +
      "5. A fill poller asks the broker to confirm the fill and writes back the fill price. " +
      "6. Positions + P&L are read back from the broker, which is the golden source.",
  },
  {
    heading: "Where the P&L comes from",
    body:
      "Open (unrealised) P&L is mark-to-market on the broker's live positions. Realised P&L — what we actually banked, " +
      "including spread, commission and overnight financing — is read from the broker's own closed-deal history (the " +
      "“Broker realised P&L” strip on the P&L card). The two answer different questions: “what are open positions worth now” " +
      "vs “did we make money”. Realised is the honest scorecard.",
  },
];

// Per-desk specifics, keyed by strategy_id. The desk card prepends the strategy
// label and appends PIPELINE.
const DESK_SPECIFICS: Record<string, { oneLiner: string; sections: HelpSection[] }> = {
  ichimoku_equity: {
    oneLiner:
      "Daily Ichimoku trend-following on US equities (T212). Long-or-flat, one position per name, Market-on-Open.",
    sections: [
      {
        heading: "What it trades & when",
        body:
          "US equity universe, daily bars. The signal is computed ONCE at session start from the prior day's daily data, " +
          "then the entry/exit fires on the first bar of the new session (Market-on-Open). A position is held until the daily " +
          "signal flips — this is built for multi-week/multi-year holds, NOT intraday trading.",
      },
      {
        heading: "Signal logic",
        body:
          "Ichimoku Cloud trend filter — long when price holds above the cloud, flat when the trend breaks. Long-or-flat only " +
          "(never short, per the trader's spec). A regime gate blocks NEW longs when SPY is below its 200-day SMA (existing " +
          "positions still exit on their own signal). Size is vol-targeted: bigger when 60-day realised vol is low, smaller when high.",
      },
      {
        heading: "Gates before an order fires",
        body:
          "Optional LLM news gate (advisory, fail-open): can VETO a new entry or BOOST its size on strong sentiment — exits are " +
          "never gated. Manual overrides (pause, veto, force-close, price/size override) are checked every bar.",
      },
      {
        heading: "Caveats",
        body:
          "Single-indicator (Ichimoku) trend filter — vulnerable when the market shifts from trending to ranging. Vol-target " +
          "sizing uses trailing 60-day vol, so it lags fast vol spikes. Daily cadence: at most one entry per name per session.",
      },
    ],
  },
  ichimoku_fx_mr: {
    oneLiner:
      "Hourly G10-FX mean-reversion on IG — fades cloud breaks. SIGNED (can be long or short), capped at 3 units/pair.",
    sections: [
      {
        heading: "What it trades & when",
        body:
          "G10 FX pairs, hourly bars, on IG demo (T212 has no FX API). One instance trades all pairs, each with its own state. " +
          "The signal is recomputed on every hourly bar over a rolling window (no lookahead).",
      },
      {
        heading: "Signal logic",
        body:
          "Mean-reversion: it FADES breaks away from the Ichimoku cloud, ensembled across several horizons and smoothing windows. " +
          "Unlike the equity desk, the FX position is SIGNED: +1 long (fading a bearish break), −1 short (fading a bullish break). " +
          "Size is vol-targeted off 480-hour realised vol, and the position per pair is capped at 3 units.",
      },
      {
        heading: "Gates before an order fires",
        body:
          "LLM news gate (advisory, fail-open) evaluates only NEW entries from flat — exits always pass. Overrides apply as on the " +
          "other desks. The IG session is a cached singleton (re-authing per request previously tripped IG's anti-abuse block).",
      },
      {
        heading: "Reading the FX P&L",
        body:
          "FX open P&L = price move × contract size (units per contract, e.g. 10,000) converted from the quote currency to USD via " +
          "IG's own rates — so a JPY-pair move is reported in USD, not yen. Realised P&L nets the spread and the daily financing/admin " +
          "fees IG charges to hold FX overnight.",
      },
      {
        heading: "Caveats",
        body:
          "Shares one IG account (and its cash) with the intraday desk — per-desk “cash” is the configured allocation, not segregated " +
          "broker cash. Overnight financing accrues daily and shows up in realised P&L.",
      },
    ],
  },
  intraday_flat: {
    oneLiner:
      "Long-only intraday on IG with a scanner-locked basket — picks the day's best Ichimoku longs and is flat by the close, every day.",
    sections: [
      {
        heading: "What it trades & when",
        body:
          "US equities/ETFs on IG (24-hour instruments), intraday only. Pre-open it SCANS the universe, scores each name with a " +
          "continuous Ichimoku-strength score, and LOCKS the top-N as today's basket — no intraday re-ranking, so the day's thesis " +
          "(“these N names, picked at the open, traded long”) stays readable in the audit.",
      },
      {
        heading: "Signal & trade management",
        body:
          "Long-only (locked by the operator — shorting would double the risk surface and split the news gate). At most one entry per " +
          "basket name per day, no averaging in. Each position carries a mandatory stop-loss, take-profit and time-stop, and the whole " +
          "position is flattened before the close. An on-session-end backstop + an out-of-band reconciliation against IG guarantee " +
          "“flat by close” even if the flatten bar is missed.",
      },
      {
        heading: "Gates before an order fires",
        body:
          "Per symbol, in order: regime → position cap → halt check → IG epic resolves (refuses to route an unmapped symbol, fail-loud) " +
          "→ LLM news veto (fail-open) → risk-based sizing → fire. Every gate writes its reason to the decision log.",
      },
      {
        heading: "Caveats",
        body:
          "This is the highest-churn desk — it opens and closes within the day, so commission + spread on every round-trip add up fast " +
          "(it was the main driver of the recent realised loss). No intraday rescan, no partial exits, no cross-strategy gross-exposure " +
          "cap yet. The LLM gate fails open: an LLM outage APPROVES rather than blocks.",
      },
    ],
  },
};

export function deskHelp(strategyId: string):
  | { oneLiner: string; sections: HelpSection[] }
  | undefined {
  const s = DESK_SPECIFICS[strategyId];
  if (!s) return undefined;
  return { oneLiner: s.oneLiner, sections: [...s.sections, ...PIPELINE] };
}
