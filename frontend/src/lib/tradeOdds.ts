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


/**
 * DIP-FROM-OPEN scan — the owner's own strategy shape.
 *
 * Different from `barrierScan` in one way that matters: the limit is placed
 * relative to each session's OPEN, not to one reference price. That is how the
 * order would actually be worked — you decide "1% below wherever it opens"
 * every morning, not a fixed price.
 *
 * SAME-SESSION AMBIGUITY. If the session's low reaches the limit AND its high
 * clears the target, the trade only worked if the low came first, and daily
 * bars cannot say. `pessimistic` (the default, and what the study graded on)
 * assumes it did not, and carries the position. The owner spotted this
 * unprompted: "the high might have hit early then we placed the order and then
 * it never hit that high in that day."
 *
 * EXPECTANCY IS THE ANSWER, NOT WIN RATE. Backtested at a 0.5% target against
 * an 8% stop this wins 66% of the time and loses 0.41% per trade, because it
 * needs 94% to break even. The UI must show both or it teaches the wrong
 * lesson. See INTRADAY_DIP_GATES_V1.md — REJECTED.
 */
export function dipScan(
  bars: Bar[],
  opts: { dipPct: number; targetPct: number; stopPct: number; carryDays: number;
          pessimistic?: boolean; startIdx?: number },
) {
  const { dipPct, targetPct, stopPct, carryDays } = opts;
  const pess = opts.pessimistic ?? true;
  const MAXDAY = 0.35;
  let sessions = 0, fills = 0, sameDay = 0;
  const res: number[] = [], days: number[] = [];
  const n = bars.length;
  let i = Math.max(1, opts.startIdx ?? 1);
  while (i < n) {
    const o = bars[i].open, pc = bars[i - 1].close;
    if (!(o > 0) || !(pc > 0) || Math.abs(o / pc - 1) > MAXDAY) { i++; continue; }
    sessions++;
    const limit = o * (1 - dipPct / 100);
    if (bars[i].low > limit) { i++; continue; }   // never traded down to us
    fills++;
    const T = limit * (1 + targetPct / 100), S = limit * (1 + stopPct / 100);
    let out: [number, number] | null = null;
    for (let j = i; j < Math.min(n, i + carryDays + 1); j++) {
      if (j > i && (!(bars[j - 1].close > 0) || Math.abs(bars[j].close / bars[j - 1].close - 1) > MAXDAY)) break;
      let hitT = bars[j].high >= T;
      const hitS = bars[j].low <= S;
      if (j === i && pess) hitT = false;
      if (hitS) { out = [stopPct, j - i]; break; }
      if (hitT) { out = [targetPct, j - i]; break; }
    }
    if (!out) {
      const j = Math.min(n - 1, i + carryDays);
      out = [100 * (bars[j].close / limit - 1), j - i];
    }
    res.push(out[0]); days.push(Math.max(1, out[1]));
    if (out[1] === 0) sameDay++;
    i += Math.max(1, out[1]) + 1;
  }
  if (!res.length) return null;
  const totalDays = days.reduce((a, b) => a + b, 0);
  const wins = res.filter((x) => x > 0).length;
  return {
    sessions, fills, trades: res.length,
    fillRate: sessions ? fills / sessions : 0,
    winRate: (100 * wins) / res.length,
    expPerTrade: res.reduce((a, b) => a + b, 0) / res.length,
    // Capital-time weighting: total return over total days COMMITTED. Not
    // mean(return/days), which overweights short lucky trades — that error
    // reported a 11.6x result that was really 5.08x.
    expPerDayHeld: totalDays ? res.reduce((a, b) => a + b, 0) / totalDays : 0,
    meanHold: totalDays / res.length,
    sameDayPct: (100 * sameDay) / res.length,
  };
}

/** Being long open→close, the thing any of this has to beat. */
export function benchmarkPerDay(bars: Bar[]) {
  const r: number[] = [];
  for (let i = 1; i < bars.length; i++) {
    const o = bars[i].open, pc = bars[i - 1].close;
    if (o > 0 && pc > 0 && Math.abs(bars[i].close / pc - 1) <= 0.35) r.push(100 * (bars[i].close / o - 1));
  }
  return r.length ? r.reduce((a, b) => a + b, 0) / r.length : null;
}

/**
 * THE STRATEGY RULES, evaluated against one symbol on demand.
 *
 * Owner: "what if I want to see the probability on a particular symbol — will
 * I just put that symbol in odds and it will run all the strategy with latest
 * data for it."
 *
 * It did not, and that was a fair expectation to have. Odds answered "if I
 * place THIS order, how often did it work" — it never asked what our own
 * strategies think of the name.
 *
 * These are ports of `signals/mean_reversion.py` and the momentum entry in
 * `cli/momentum_candidates.py`. They are checked against the Python by
 * `strategies/scripts/check-trade-odds-parity.sh` — if you change one, change
 * both and re-run it. A drifting copy of an entry rule is how a screen starts
 * disagreeing with the backtest that justified it.
 *
 * Each returns not just fires/does-not-fire but HOW FAR from firing, because
 * "no" is the answer on almost every symbol on almost every day, and a bare
 * "no" tells you nothing about whether to look again tomorrow.
 */
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const smaAt = (c: number[], i: number, n: number) => mean(c.slice(i - n + 1, i + 1));

export type RuleCheck = {
  fires: boolean;
  headline: string;
  clauses: Array<{ label: string; value: string; ok: boolean }>;
  plan?: { entry: number; target?: number; stop: number; targetPct?: number };
};

/** Swing — 2.5σ below the 20-day mean, while above the 200-day average. */
export function checkSwing(bars: Bar[]): RuleCheck | null {
  const c = bars.map((b) => b.close);
  const i = c.length - 1;
  if (i < 210) return null;
  const w = c.slice(i - 19, i + 1);
  const m20 = mean(w);
  const sd = Math.sqrt(mean(w.map((x) => (x - m20) ** 2)));   // population sd, as Python
  const s200 = smaAt(c, i, 200);
  const band = m20 - 2.5 * sd;
  const belowBand = c[i] < band;
  const aboveTrend = c[i] > s200;
  const sigmasBelow = sd > 0 ? (m20 - c[i]) / sd : 0;
  const fires = belowBand && aboveTrend;
  return {
    fires,
    // A negative "sigmasBelow" means the price is ABOVE its mean, and reading
    // "-2.2σ below" is worse than useless — TSLA sits 2.2σ ABOVE and would
    // have read as almost qualifying. Say which side of the mean it is on.
    headline: fires
      ? `FIRES — ${sigmasBelow.toFixed(1)}σ below the 20-day mean, above the 200-day average`
      : sigmasBelow < 0
        ? `no — trading ${Math.abs(sigmasBelow).toFixed(1)}σ ABOVE its 20-day mean; this rule buys dips`
        : `no — only ${sigmasBelow.toFixed(1)}σ below the mean, needs 2.5σ${aboveTrend ? "" : ". It is also BELOW its 200-day average, which blocks the rule outright"}`,
    clauses: [
      { label: "At least 2.5σ below the 20-day mean", ok: belowBand,
        value: `${sigmasBelow >= 0 ? "" : "+"}${Math.abs(sigmasBelow).toFixed(2)}σ ${sigmasBelow >= 0 ? "below" : "ABOVE"} · close ${c[i].toFixed(2)} vs band ${band.toFixed(2)}` },
      { label: "Above the 200-day average (the trend floor)", ok: aboveTrend,
        value: `${(100 * (c[i] / s200 - 1)).toFixed(1)}% · close ${c[i].toFixed(2)} vs ${s200.toFixed(2)}` },
    ],
    plan: { entry: c[i], target: m20, stop: c[i] * 0.92,
            targetPct: 100 * (m20 / c[i] - 1) },
  };
}

/** Momentum — a pullback TO the 10-day average inside a confirmed uptrend. */
export function checkMomentum(bars: Bar[]): RuleCheck | null {
  const c = bars.map((b) => b.close);
  const i = c.length - 1;
  if (i < 210) return null;
  const s200 = smaAt(c, i, 200), s50 = smaAt(c, i, 50);
  const s20 = smaAt(c, i, 20), s10 = smaAt(c, i, 10);
  const prev10 = smaAt(c, i - 1, 10);
  const cl = [
    { label: "Above the 200-day average", ok: c[i] > s200,
      value: `${(100 * (c[i] / s200 - 1)).toFixed(1)}%` },
    { label: "20-day above the 50-day (uptrend)", ok: s20 > s50,
      value: `${(100 * (s20 / s50 - 1)).toFixed(1)}%` },
    { label: "Still above the 20-day average", ok: c[i] > s20,
      value: `${(100 * (c[i] / s20 - 1)).toFixed(1)}%` },
    { label: "Pulled back TO the 10-day average (trigger)", ok: c[i] <= s10 * 1.005,
      value: `${(100 * (c[i] / s10 - 1)).toFixed(1)}% (fires at ≤ +0.5%)` },
    { label: "Was above it yesterday — a pullback, not a breakdown", ok: c[i - 1] > prev10,
      value: `${(100 * (c[i - 1] / prev10 - 1)).toFixed(1)}%` },
  ];
  const fires = cl.every((x) => x.ok);
  const failed = cl.filter((x) => !x.ok);
  return {
    fires,
    headline: fires ? "FIRES — pullback to the 10-day average in an uptrend"
      : `no — ${failed.length} of 5 conditions unmet (${failed[0].label.toLowerCase()})`,
    clauses: cl,
    plan: { entry: c[i], stop: c[i] * 0.92 },
  };
}
