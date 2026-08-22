/**
 * Trade odds — barrier maths, ported from
 * `strategies/tradepro_strategies/cli/trade_odds.py`.
 *
 * TWO IMPLEMENTATIONS OF THE SAME MATHS, deliberately. The Python CLI is the
 * reference and is what studies are run through; this runs in the browser so
 * the screen can be interactive without a Python service behind it (the API is
 * C#). They are validated against each other on a known case — see
 * `tradeOdds.test.ts`. If you change one, change both and re-check.
 *
 * The rules that matter, and why:
 *
 *  - A limit BELOW the market is a bet price falls to you FIRST. So the answer
 *    is a chain — P(filled), then P(target | filled) — never just the second.
 *  - Daily bars cannot say whether the high or the low came first. A session
 *    touching BOTH barriers is counted as a STOP. Every number is therefore
 *    biased downward, which is the right direction for money.
 *  - Everything is measured in PERCENT from the price at order time, so a
 *    history spanning an 11x price change stays comparable.
 */
export type Bar = { ts: string; open: number; high: number; low: number; close: number };

export type Scan = {
  attempts: number; filled: number; won: number; lost: number; timedOut: number;
  pFill: number | null; pTargetGivenFill: number | null; pBoth: number | null;
  medianBarsToTarget: number | null; meanOutcomePct: number | null;
};

const median = (xs: number[]) =>
  xs.length ? [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)] : null;

export function barrierScan(
  bars: Bar[],
  opts: { limitPct: number; targetPct: number; stopPct: number;
          fillWindow: number; tradeWindow: number; startIdx?: number },
): Scan {
  const { limitPct, targetPct, stopPct, fillWindow, tradeWindow } = opts;
  const start = opts.startIdx ?? 0;
  let attempts = 0, filled = 0, won = 0, lost = 0, timedOut = 0;
  const barsToWin: number[] = [];
  const outcomes: number[] = [];
  const n = bars.length;

  for (let i = start; i < n - 1; i++) {
    const ref = bars[i].close;
    if (!(ref > 0)) continue;
    attempts++;
    const limit = ref * (1 + limitPct);
    let fillJ = -1;
    for (let j = i + 1; j < Math.min(n, i + fillWindow + 1); j++) {
      if ((limitPct <= 0 && bars[j].low <= limit) || (limitPct > 0 && bars[j].high >= limit)) {
        fillJ = j; break;
      }
    }
    if (fillJ < 0) continue;
    filled++;

    const tgt = limit * (1 + targetPct);
    const stp = limit * (1 + stopPct);
    let done = false;
    for (let j = fillJ; j < Math.min(n, fillJ + tradeWindow + 1); j++) {
      const hitT = bars[j].high >= tgt;
      const hitS = bars[j].low <= stp;
      if (hitT && hitS) { lost++; outcomes.push(stopPct * 100); done = true; break; }
      if (hitT) { won++; barsToWin.push(j - fillJ); outcomes.push(targetPct * 100); done = true; break; }
      if (hitS) { lost++; outcomes.push(stopPct * 100); done = true; break; }
    }
    if (!done) {
      timedOut++;
      const end = Math.min(n - 1, fillJ + tradeWindow);
      outcomes.push(100 * (bars[end].close / limit - 1));
    }
  }
  const mean = outcomes.length ? outcomes.reduce((a, b) => a + b, 0) / outcomes.length : null;
  return {
    attempts, filled, won, lost, timedOut,
    pFill: attempts ? filled / attempts : null,
    pTargetGivenFill: filled ? won / filled : null,
    pBoth: attempts ? won / attempts : null,
    medianBarsToTarget: median(barsToWin),
    meanOutcomePct: mean === null ? null : Math.round(mean * 100) / 100,
  };
}

/** How far the symbol actually TRAVELS up within the window — the measured
 *  answer to the question support/resistance lines are usually asked. */
export function excursion(bars: Bar[], window: number, startIdx = 0) {
  const ups: number[] = [];
  for (let i = startIdx; i < bars.length - 1; i++) {
    if (!(bars[i].close > 0)) continue;
    let hi = -Infinity;
    for (let j = i + 1; j < Math.min(bars.length, i + window + 1); j++) hi = Math.max(hi, bars[j].high);
    if (hi > -Infinity) ups.push(100 * (hi / bars[i].close - 1));
  }
  if (!ups.length) return null;
  ups.sort((a, b) => a - b);
  const q = (p: number) => Math.round(ups[Math.floor(p * (ups.length - 1))] * 10) / 10;
  return { n: ups.length, p25: q(0.25), median: q(0.5), p75: q(0.75), p90: q(0.9) };
}

export const DEFAULT_TARGETS = [1, 2, 3, 5, 8, 12, 18, 25];

export function sweepTargets(
  bars: Bar[],
  opts: { limitPct: number; stopPct: number; fillWindow: number; tradeWindow: number;
          startIdx?: number; targets?: number[] },
) {
  return (opts.targets ?? DEFAULT_TARGETS).map((t) => {
    const r = barrierScan(bars, { ...opts, targetPct: t / 100 });
    return {
      targetPct: t,
      pTargetGivenFill: r.pTargetGivenFill,
      expectancyPct: r.meanOutcomePct,
      rewardRisk: opts.stopPct ? Math.round((t / Math.abs(opts.stopPct * 100)) * 100) / 100 : null,
      medianBarsToTarget: r.medianBarsToTarget,
      filled: r.filled,
    };
  });
}
