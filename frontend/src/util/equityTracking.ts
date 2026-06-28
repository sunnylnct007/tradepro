/**
 * equityTracking — "is the live systematic clone tracking its backtest?"
 *
 * Pure mirror of the Python `assess_tracking` (strategies/.../quant_engine/
 * equity_tracking.py) so the cockpit shows the SAME verdict the CLI does.
 * Keep the two in lock-step — both are ~30 lines of the same arithmetic.
 *
 * Deliberately conservative: a few days of live P&L is mostly noise, so it
 * refuses to judge return-tracking until enough sessions pass (TOO_EARLY)
 * rather than crying success/failure on noise — the no-false-positives rule
 * applied to the track record itself. A live drawdown worse than the backtest's
 * own max-DD is the one hard flag (DRAWDOWN_BREACH) regardless of how early.
 */
export type TrackingStatus =
  | "TOO_EARLY"
  | "ON_TRACK"
  | "AHEAD"
  | "BEHIND"
  | "DRAWDOWN_BREACH";

export interface TrackingVerdict {
  status: TrackingStatus;
  elapsedDays: number;
  actualReturnPct: number; // live realised+open ÷ capital
  expectedReturnPct: number; // backtest CAGR pro-rated to elapsed
  drawdownBoundPct: number; // backtest max DD (negative) — the line not to cross
  note: string;
}

export interface TrackingParams {
  expectedCagrPct: number;
  drawdownBoundPct: number;
  minDays?: number;
  bandPct?: number;
}

export function assessTracking(
  actualReturnPct: number,
  elapsedDays: number,
  { expectedCagrPct, drawdownBoundPct, minDays = 20, bandPct = 6.0 }: TrackingParams,
): TrackingVerdict {
  const years = Math.max(elapsedDays, 0) / 365.0;
  const expected = years > 0 ? (Math.pow(1 + expectedCagrPct / 100.0, years) - 1) * 100.0 : 0.0;

  const base = {
    elapsedDays,
    actualReturnPct,
    expectedReturnPct: expected,
    drawdownBoundPct,
  };

  // Drawdown bound is breached regardless of how early — a risk-profile failure,
  // not a return question.
  if (actualReturnPct <= drawdownBoundPct) {
    return {
      ...base,
      status: "DRAWDOWN_BREACH",
      note:
        `live ${fmt(actualReturnPct)}% breached the backtest max-DD bound ` +
        `${drawdownBoundPct.toFixed(1)}% — live risk ≠ backtested risk; investigate.`,
    };
  }

  if (elapsedDays < minDays) {
    return {
      ...base,
      status: "TOO_EARLY",
      note:
        `only ${elapsedDays}d of clean-config history (<${minDays}d) — too noisy to ` +
        `judge return tracking yet; no verdict (no false positives).`,
    };
  }

  let status: TrackingStatus;
  let msg: string;
  if (actualReturnPct > expected + bandPct) {
    status = "AHEAD";
    msg = "live ahead of the backtest pace";
  } else if (actualReturnPct < expected - bandPct) {
    status = "BEHIND";
    msg = "live behind the backtest pace — watch for divergence";
  } else {
    status = "ON_TRACK";
    msg = "live within tolerance of the backtest expectation";
  }
  return {
    ...base,
    status,
    note:
      `${msg}: actual ${fmt(actualReturnPct)}% vs expected ${fmt(expected)}% ` +
      `(±${bandPct.toFixed(0)}pp) over ${elapsedDays}d.`,
  };
}

function fmt(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}`;
}
