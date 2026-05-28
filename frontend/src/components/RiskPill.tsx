import type { RiskRating } from "../api/types";

/**
 * Compact risk-rating pill rendered next to a bucket label or in a
 * holdings row. Hover reveals the full audit trail (factors list)
 * — no black-box rating; every input that drove the verdict appears
 * verbatim in the title attribute.
 */
const COLOURS: Record<RiskRating["rating"], string> = {
  LOW: "var(--up)",
  MEDIUM: "var(--neutral)",
  HIGH: "var(--down)",
  EXTREME: "#c34cff", // magenta — distinguish from HIGH so the eye reads "really high"
};

const POSITION_CAP_HINT: Record<RiskRating["rating"], string> = {
  LOW: "≤25% of portfolio",
  MEDIUM: "≤15% of portfolio",
  HIGH: "≤8% of portfolio",
  EXTREME: "≤4% of portfolio",
};

export function RiskPill({ rating }: { rating: RiskRating | null | undefined }) {
  if (!rating) return null;
  const colour = COLOURS[rating.rating];
  const title =
    `Risk: ${rating.rating} — recommended ${POSITION_CAP_HINT[rating.rating]}\n\n` +
    "Why:\n" +
    rating.factors.map((f) => `  · ${f}`).join("\n") +
    (rating.escalators > 0
      ? `\n\nBaseline tier: ${rating.baseline} (vol). +${rating.escalators} escalator${rating.escalators > 1 ? "s" : ""} applied.`
      : "");
  return (
    <span
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px 6px",
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.05em",
        color: colour,
        border: `1px solid ${colour}`,
        borderRadius: 3,
        whiteSpace: "nowrap",
        cursor: "help",
      }}
    >
      RISK · {rating.rating}
    </span>
  );
}
