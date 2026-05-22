/** Single source of truth for the per-metric trust grades surfaced
 * on Decide / Portfolio / Symbol Deep Dive. Mirrored in TRUST_STATUS.md
 * — when you change a grade here, update the doc and vice versa.
 *
 * Grading rubric:
 *   green  — source verified, regression test exists, at least one
 *            bug-and-fix cycle survived. Sensible to act on.
 *   yellow — source known + code correct under inspection. No
 *            regression test, OR recently changed, OR known caveat.
 *            Use as input not as decision.
 *   red    — known issue, do not act until promoted to yellow.
 *
 * Stable IDs — UI passes `id` to <TrustDot/>, lookup happens here. */

export type TrustGrade = "green" | "yellow" | "red";

export interface TrustEntry {
  /** Short human label for the metric. */
  label: string;
  grade: TrustGrade;
  /** One- to two-sentence explanation a user can scan. */
  reason: string;
  /** What would move this to Green (or Yellow, from Red). */
  promoteWhen?: string;
}

/**
 * Keep IDs stable across UI surfaces — re-used between Decide
 * matrix headers, Portfolio columns, and Symbol Deep Dive sections.
 */
export const TRUST: Record<string, TrustEntry> = {
  // ── Decide page ──────────────────────────────────────────────
  "decide.verdict": {
    label: "Verdict (BUY / WAIT / AVOID)",
    grade: "yellow",
    reason:
      "Authoritative pricing-rules layer; survived two recent bug cycles "
      + "(BABA trend-coherence, MTUM R/R). Two miscategorisations in 30 days "
      + "suggests more edge cases. Doesn't factor in upcoming earnings.",
    promoteWhen:
      "earnings-blackout rule + multi-source price consensus (Phase 6.8) ship.",
  },
  "decide.top_candidate_label": {
    label: "Top candidate label",
    grade: "green",
    reason:
      "After 2026-05-21 fix the card explicitly labels itself "
      + "'Watchlist · closest to a buy (still WAIT/AVOID)' with an italic "
      + "disclaimer when no BUY exists.",
  },
  "decide.strategy_cell": {
    label: "Per-strategy long/flat/short",
    grade: "green",
    reason:
      "Direct read of the strategy's last-emitted signal. Each base "
      + "strategy has its own regression coverage in features/*.feature.",
  },
  "decide.swing_score": {
    label: "Swing score (0-8)",
    grade: "yellow",
    reason:
      "Aggregation math correct, but sub-scores render 0 silently when "
      + "upstream data (Sharpe / earnings) is missing — hides coverage gaps.",
    promoteWhen: "missing-data states render as '—' instead of silent 0.",
  },
  "decide.best_stat": {
    label: "Best Sharpe / CAGR ranking",
    grade: "green",
    reason:
      "Computed from adj_close return series. Stable math, no known bugs.",
  },
  "decide.rr": {
    label: "Price target · stop · R/R",
    grade: "yellow",
    reason:
      "After 2026-05-21 fix, ABOVE-cloud setups now correctly show NO "
      + "target (was surfacing negative R/R). Honest but unactionable for "
      + "the common bullish case.",
    promoteWhen:
      "projection-based target lands for ABOVE-cloud setups with documented methodology.",
  },
  "decide.stale_badge": {
    label: "Stale-data badge",
    grade: "green",
    reason: "Reads data_age_days directly. 7-day threshold is conservative.",
  },
  "decide.sentiment_banner": {
    label: "Sentiment-demotion banner",
    grade: "yellow",
    reason:
      "Logic correct, but news coverage on UK/EU stocks is patchy via "
      + "Finnhub — the demotion can fail to fire on real bad-news days.",
  },

  // ── Portfolio page ───────────────────────────────────────────
  "portfolio.t212_chip": {
    label: "T212 connection / mode",
    grade: "green",
    reason:
      "Reads mode + error directly from integration health endpoint. "
      + "401/403/network errors render specific remediation copy.",
  },
  "portfolio.position_row": {
    label: "Position qty / avg cost / current price",
    grade: "green",
    reason: "T212's own numbers, displayed as-is.",
  },
  "portfolio.pnl_cells": {
    label: "P&L % / abs",
    grade: "green",
    reason: "Calculated by T212, displayed as-is.",
  },
  "portfolio.total_unrealised": {
    label: "Total unrealised P&L",
    grade: "yellow",
    reason:
      "Sum-skips null unrealisedAbs (T212 returns null intermittently for "
      + "fractional shares of certain ETFs). Total can understate slightly.",
  },
  "portfolio.today_verdict": {
    label: "Today verdict (per holding)",
    grade: "yellow",
    reason:
      "Same Yellow as Decide's verdict, plus a symbol-resolution caveat: "
      + "'—' renders for both 'no universe match' AND 'symbol-mapping "
      + "failure', so users can't tell which case they're in.",
    promoteWhen:
      "resolver disambiguates 'no universe match' from 'symbol-mapping failure'.",
  },
  "portfolio.swing_mini": {
    label: "Swing column (per holding)",
    grade: "yellow",
    reason:
      "Inherits Decide's swing-score caveats (silent 0 on missing sub-score data).",
  },

  // ── Symbol Deep Dive ─────────────────────────────────────────
  "deepdive.header": {
    label: "Header (price / state)",
    grade: "yellow",
    reason:
      "Reads cached compare row. Doesn't show cache age — user can't tell "
      + "if they're looking at today's or yesterday's number.",
    promoteWhen: "cache-age stamp shown on the header.",
  },
  "deepdive.verdict": {
    label: "Verdict badge",
    grade: "yellow",
    reason:
      "Same verdict logic as Decide; inherits the same caveats. Adds "
      + "cross-strategy in_position count next to the badge.",
  },
  "deepdive.decision_trace": {
    label: "Decision trace (Section 3)",
    grade: "green",
    reason:
      "Renders market_state.decision_trace verbatim — every rule's pass/"
      + "fail/warn label + detail. Ground-truth audit log. Most-trustworthy "
      + "surface in the app.",
  },
  "deepdive.conflict_ux": {
    label: "Strategy conflict surfacing (Section 4)",
    grade: "green",
    reason:
      "Pure UI logic over verified row data. Surfaces 'long-term says "
      + "AVOID, swing says BUY — here's why' explicitly.",
  },
  "deepdive.news": {
    label: "News + sentiment (Section 5)",
    grade: "yellow",
    reason:
      "Coverage gaps on UK/EU stocks (Finnhub thin outside US). "
      + "Sentiment from one LLM pass, no human validation. Bigger "
      + "gap: no catalyst extraction — TradePro reads headlines but "
      + "doesn't extract DATED catalysts (elections, FOMC, OPEC) so "
      + "it can't reason about 'this trade is real because of an "
      + "event in 10 days'. See DATA_ROADMAP §13.5 catalyst sprint.",
    promoteWhen:
      "Catalyst sprint (§13.5 / Phases 17.1–17.5) ships — dated-event extraction + combined verdict.",
  },
  "deepdive.analyst_static": {
    label: "Analyst consensus (Section 6 — static counts + mean target)",
    grade: "yellow",
    reason:
      "Looks correct vs TipRanks on spot-checked symbols but no automated "
      + "cross-check.",
  },
  "deepdive.analyst_upgrades": {
    label: "Analyst upgrades feed (Section 6 — events list)",
    grade: "red",
    reason:
      "Reviewer-confirmed: Finnhub drops upgrade events for ADRs (BABA "
      + "upgrade missed). Task #71 open to audit + fix.",
    promoteWhen: "task #71 ships — feed audited + second-source verified.",
  },
  "deepdive.earnings": {
    label: "Earnings event risk (Section 7)",
    grade: "yellow",
    reason:
      "Shows next-earnings date when present. Forward calendar is sparse "
      + "(task #66 hasn't shipped). No 'flatten before earnings' rule yet.",
  },
  "deepdive.regime_survival": {
    label: "Regime survival (Section 8)",
    grade: "red",
    reason:
      "Placeholder — not built. Needs task #66 backend prep (per-strategy "
      + "regime data on the compare row).",
  },
  "deepdive.peer_comparison": {
    label: "Peer comparison (Section 9)",
    grade: "red",
    reason: "Placeholder — not built. Needs symbol → tags map (task #66).",
  },
  "deepdive.hit_rate": {
    label: "Per-strategy hit rate (Section 10)",
    grade: "yellow",
    reason:
      "Fires parallel /api/signals/hitrate per strategy. Math is direct "
      + "but no regression test pins it; hit-rate horizon choice is "
      + "documented as task #67 open question.",
  },

  // ── Intraday surfaces ────────────────────────────────────────
  "intraday.engine_output": {
    label: "Intraday engine output",
    grade: "yellow",
    reason:
      "End-to-end roundtrip verified, but manual placement only (orders "
      + "queue as Pending, nothing reaches T212). Yahoo intraday bars "
      + "unreliable during US market hours.",
    promoteWhen:
      "Polygon.io intraday wired in (Phase 7.2) + pre-trade gate enforces.",
  },
  "intraday.leaderboard": {
    label: "Strategy leaderboard",
    grade: "yellow",
    reason:
      "Just shipped. SQL rollup is direct; tested by hand against the one "
      + "completed session. No regression coverage yet.",
  },
};
