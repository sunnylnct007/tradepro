/**
 * coherentBucket — defence-in-depth coherence guard. Refuses to
 * display BUY when the underlying market_state says WAIT or AVOID.
 *
 * The AAPL case: at 52w high + RSI 79 + range_pct 100th percentile,
 * entry_signal = WAIT, entry_reason = "let it cool before adding".
 * The cached payload can still carry bucket=BUY (Donchian breakout
 * mechanically fires on new highs). Surface coherence — never show
 * BUY badges next to a WAIT decision trace.
 *
 * Apply at every display site that renders `row.bucket`:
 *   - Compare.tsx (top-level investment-decision page)
 *   - SymbolAnalysisCard (deep-dive panel)
 *   - SymbolScanGrid (cockpit symbol grid)
 *   - any future bucket-rendering surface
 *
 * This guard is the same logic the server's compare.py applies on
 * write; we re-apply at read so older cached payloads (from before
 * the server fix shipped) don't surface contradictions to the trader.
 */
export type Bucket = "BUY" | "WAIT" | "AVOID" | "HOLD" | string | null | undefined;

export function coherentBucket(
  bucket: Bucket,
  entrySignal: string | null | undefined,
): { bucket: Bucket; downgraded: boolean; downgradeReason?: string } {
  if (bucket !== "BUY") return { bucket, downgraded: false };
  const es = (entrySignal ?? "").toUpperCase();
  if (es === "WAIT") {
    return {
      bucket: "WAIT",
      downgraded: true,
      downgradeReason: "Downgraded from BUY: market_state entry_signal = WAIT",
    };
  }
  if (es === "AVOID") {
    return {
      bucket: "AVOID",
      downgraded: true,
      downgradeReason: "Downgraded from BUY: market_state entry_signal = AVOID",
    };
  }
  return { bucket, downgraded: false };
}
