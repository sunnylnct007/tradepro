/**
 * Renko brick construction.
 *
 * Renko throws away TIME and small moves: a brick is emitted only when price
 * travels a fixed distance, so consolidation vanishes and trends look clean.
 * That is the whole appeal and also the whole danger — a Renko chart of random
 * noise still looks like an orderly sequence of trends. It is a way of LOOKING
 * at price, not evidence about it. (See the 15 Aug 2026 S/R study: lines that
 * looked authoritative on a chart carried exactly zero predictive edge over
 * randomly-placed lines, across 76,260 events.)
 *
 * Classic rules implemented here:
 *   - a brick is `brickSize` tall, drawn from the previous brick's close;
 *   - continuing the current direction needs 1 × brickSize;
 *   - REVERSING needs 2 × brickSize (the standard rule — without it, a chart
 *     in a range flip-flops every bar and shows nothing).
 *
 * Bricks are built from CLOSES only. Intra-bar excursions that never closed
 * beyond a threshold do not create bricks; that is standard, and it means a
 * Renko chart is not a faithful record of what traded.
 */

export type Brick = {
  time: number;   // synthetic, strictly ascending — see note in renkoBricks
  open: number;
  close: number;
  up: boolean;
  sourceTime: number; // the real bar time the brick formed on
};

type SrcCandle = { timestamp: string | number; high: number; low: number; close: number };

/** Wilder-style ATR over `period` bars, used as the default brick height so a
 *  brick means the same thing (one typical day's range) across instruments
 *  priced at $8 and $800. Falls back to a percentage of the last close when
 *  there is not enough history. */
export function atrBrickSize(candles: SrcCandle[], period = 14): number {
  if (candles.length < 2) return 0;
  const trs: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].high, l = candles[i].low, pc = candles[i - 1].close;
    trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
  }
  const use = trs.slice(-period);
  if (!use.length) return 0;
  const atr = use.reduce((a, b) => a + b, 0) / use.length;
  if (atr > 0) return atr;
  const last = candles[candles.length - 1].close;
  return last > 0 ? last * 0.01 : 0;
}

function toEpochSeconds(t: string | number): number {
  if (typeof t === "number") return t > 1e11 ? Math.floor(t / 1000) : Math.floor(t);
  const ms = Date.parse(t);
  return Number.isNaN(ms) ? 0 : Math.floor(ms / 1000);
}

/**
 * Build Renko bricks from candles.
 *
 * TIME IS SYNTHETIC. Several bricks can form on one bar, and lightweight-charts
 * requires strictly ascending unique times, so each brick takes the source
 * bar's time pushed forward by one second per brick. The x-axis on a Renko
 * chart is therefore a SEQUENCE, not a clock — spacing carries no information
 * and must not be read as duration. `sourceTime` keeps the real bar time for
 * tooltips and for anyone who needs to map a brick back to reality.
 */
export function renkoBricks(candles: SrcCandle[], brickSize: number): Brick[] {
  if (!candles.length || !(brickSize > 0)) return [];
  const bricks: Brick[] = [];
  let anchor = candles[0].close;   // close of the last emitted brick
  let dir: 0 | 1 | -1 = 0;
  let lastTime = -Infinity;

  const push = (open: number, close: number, up: boolean, srcT: number) => {
    const t = Math.max(srcT, lastTime + 1);
    lastTime = t;
    bricks.push({ time: t, open, close, up, sourceTime: srcT });
  };

  for (const c of candles) {
    const p = c.close;
    const srcT = toEpochSeconds(c.timestamp);
    // A single bar can span many bricks; emit until the move is exhausted.
    // Bounded by construction — every iteration moves `anchor` by brickSize
    // toward p — but guard anyway so a pathological brickSize cannot hang the
    // render thread.
    for (let guard = 0; guard < 10_000; guard++) {
      if (dir >= 0 && p >= anchor + brickSize) {
        push(anchor, anchor + brickSize, true, srcT);
        anchor += brickSize; dir = 1; continue;
      }
      if (dir <= 0 && p <= anchor - brickSize) {
        push(anchor, anchor - brickSize, false, srcT);
        anchor -= brickSize; dir = -1; continue;
      }
      // Reversals cost double.
      if (dir === 1 && p <= anchor - 2 * brickSize) {
        push(anchor - brickSize, anchor - 2 * brickSize, false, srcT);
        anchor -= 2 * brickSize; dir = -1; continue;
      }
      if (dir === -1 && p >= anchor + 2 * brickSize) {
        push(anchor + brickSize, anchor + 2 * brickSize, true, srcT);
        anchor += 2 * brickSize; dir = 1; continue;
      }
      break;
    }
  }
  return bricks;
}
