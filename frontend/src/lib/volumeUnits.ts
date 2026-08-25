/**
 * Is this volume series on ONE scale?
 *
 * IBKR reports 100-share lots, and the lot conversion was applied twice in one
 * pipeline (data lane, 6c22ebd) — once in the C# `IBKRResponseParser`, and
 * again in `ibkr_web_provider`, which reads our own already-converted API
 * rather than IBKR. The result is stored volume that is 100x too high in some
 * months and correct in others, at every resolution: SPY daily bars read ~6.2bn
 * shares against a real ~59m, and SPY 5m bars read 50-80m against a real
 * 300k-1m.
 *
 * Anything that COMPARES volume across that seam is arithmetic on two
 * different units. A uniform error cancels in a ratio; an error that starts
 * partway through the window does not. The dangerous outputs are the ones that
 * still look plausible: `volume_vs_20d` reads 0.95-1.41 today, biased about
 * 17% high, and nobody questions a number like that.
 *
 * Detection is the largest ADJACENT step, NOT a half-vs-half median. My first
 * detector split the window in half and reported everything clean, because
 * only four of twenty bars preceded the change and both half-medians landed on
 * the inflated side. The obvious statistic is the wrong one here, which is why
 * the reason is written down: it is the version someone reintroduces.
 *
 * Lives in one place because the same three lines existed independently in the
 * two candidate screens before this, and two components quietly disagreeing is
 * the shape of most of this codebase's bugs.
 */

/** A units error is ~100x. Real volume never steps this far between two bars
 *  and stays there. */
export const VOLUME_UNIT_STEP = 20;

export type HasVolume = { volume?: number | null };

/**
 * Index of the bar where the scale changes, or -1 if the series is on one
 * scale. Bars before the index are on a different scale from those at and
 * after it.
 */
export function volumeScaleBreak(bars: HasVolume[]): number {
  let worst = -1, worstRatio = 1;
  for (let i = 1; i < bars.length; i++) {
    const a = Number(bars[i - 1].volume) || 0;
    const b = Number(bars[i].volume) || 0;
    if (a <= 0 || b <= 0) continue;
    const r = a > b ? a / b : b / a;
    if (r > worstRatio) { worstRatio = r; worst = i; }
  }
  return worstRatio >= VOLUME_UNIT_STEP ? worst : -1;
}

/**
 * Can an ABSOLUTE volume figure be shown?
 *
 * A ratio can be checked against its own window. A single number cannot —
 * there is nothing in "6,247,757,850" to tell a reader it is 100x too high,
 * and a one-day intraday window is uniformly wrong with no seam to detect.
 * The scale-break test can only ever return "definitely broken", never
 * "definitely fine", so an absolute figure sourced from the affected provider
 * is not something we can stand behind until the store is repaired.
 *
 * Returns null when it is safe to display, or the reason to show instead.
 */
export function absoluteVolumeCaveat(bars: HasVolume[]): string | null {
  if (volumeScaleBreak(bars) > 0) {
    return "the stored series changes volume units partway through this window";
  }
  return null;
}
