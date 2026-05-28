import type { HorizonClassification, HorizonVerdict } from "../api/types";

/**
 * Three-pill display of the per-horizon verdicts (swing / long-term /
 * passive). Surfaces the TRADEPRO-SPEC-001 §6.2 horizon split that the
 * single-bucket verdict can't carry on its own — answers
 * "this might be a long-term hold but is it a swing trade today?"
 * which the bucket label alone leaves ambiguous (the NVDA/AMZN class).
 *
 * Each pill shows the horizon label + the signal + the score (X/8).
 * Colour follows the signal: BUY=green, WATCH=amber, AVOID=red,
 * N/A=grey. Tooltip carries the verdict's reasons list so the user
 * can drill in without expanding anything else.
 */

const COLOUR: Record<HorizonVerdict["signal"], { bg: string; border: string; text: string }> = {
  "BUY":   { bg: "rgba(31,193,107,0.16)",  border: "rgba(31,193,107,0.55)",  text: "var(--up)" },
  "WATCH": { bg: "rgba(255,180,80,0.14)",  border: "rgba(255,180,80,0.45)",  text: "var(--neutral)" },
  "AVOID": { bg: "rgba(255,80,80,0.14)",   border: "rgba(255,80,80,0.45)",   text: "var(--down)" },
  "N/A":   { bg: "rgba(155,161,173,0.10)", border: "rgba(155,161,173,0.35)", text: "var(--text-muted)" },
};

const LABEL: Record<keyof Omit<HorizonClassification, "range_pct">, string> = {
  swing:     "Swing",
  long_term: "Long-term",
  passive:   "Passive",
};

const HORIZON_WINDOW: Record<keyof Omit<HorizonClassification, "range_pct">, string> = {
  swing:     "1-8 weeks",
  long_term: "6-18 months",
  passive:   "3-5 years",
};

interface Props {
  classification?: HorizonClassification | null;
  /** Render in a compact inline row (header context) or as a more
   *  spacious block (expand panel). Default = compact. */
  variant?: "compact" | "block";
}

export function HorizonPills({ classification, variant = "compact" }: Props) {
  if (!classification) return null;
  const keys: (keyof Omit<HorizonClassification, "range_pct">)[] = ["swing", "long_term", "passive"];

  // Heuristic disagreement flag — when long-term says BUY but swing
  // says AVOID, surface a one-line warning above the pills so the
  // user sees the split without reading the rationale.
  const split =
    classification.long_term?.signal === "BUY" &&
    classification.swing?.signal === "AVOID";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: variant === "block" ? 8 : 4 }}>
      {variant === "block" && (
        <div className="stat-label" style={{ marginBottom: 0 }}>
          Horizon split
          {classification.range_pct != null && (
            <span style={{ color: "var(--text-muted)", marginLeft: 8, fontWeight: 400 }}>
              · {classification.range_pct.toFixed(0)}th pctile of 52w
            </span>
          )}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {keys.map((k) => {
          const v = classification[k];
          if (!v) return null;
          const c = COLOUR[v.signal] ?? COLOUR["N/A"];
          const reasonsLine = (v.reasons ?? []).slice(0, 5).join(" · ");
          // `score` is already pre-formatted server-side as "X/8" or
          // "N/A"; just render as-is. Skip it on signal=N/A since the
          // signal label already carries that info.
          const scoreSuffix = v.score && v.score !== "N/A" ? ` ${v.score}` : "";
          const title =
            `${LABEL[k]} horizon (${HORIZON_WINDOW[k]}): ${v.signal}${scoreSuffix}` +
            (reasonsLine ? `\n${reasonsLine}` : "");
          return (
            <span
              key={k}
              title={title}
              style={{
                fontSize: 10,
                fontWeight: 600,
                padding: variant === "block" ? "3px 9px" : "2px 7px",
                borderRadius: 999,
                background: c.bg,
                border: `1px solid ${c.border}`,
                color: c.text,
                whiteSpace: "nowrap",
              }}
            >
              {LABEL[k]} · {v.signal}
              {scoreSuffix && (
                <span style={{ opacity: 0.7, marginLeft: 4 }}>{v.score}</span>
              )}
            </span>
          );
        })}
      </div>
      {split && variant === "block" && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-dim)",
            padding: "4px 8px",
            borderLeft: "2px solid var(--neutral)",
            background: "rgba(255,180,80,0.04)",
          }}
        >
          Long-term BUY but swing AVOID — strong multi-year hold candidate,
          NOT a fresh swing entry today.
        </div>
      )}
    </div>
  );
}
