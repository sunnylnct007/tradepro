import { TRUST, type TrustGrade } from "../data/trustGrades";

/** Inline trust indicator. Renders a small coloured dot next to a
 * metric; hover (or focus) reveals a tooltip with the grade label,
 * reason, and "what would promote it" criterion.
 *
 * Pass an `id` matching an entry in `trustGrades.ts`. Unknown ids
 * render an invisible spacer + warn in dev so a typo is loud but
 * doesn't crash the page.
 *
 * The doc TRUST_STATUS.md mirrors the entries in trustGrades.ts —
 * change a grade here, update the doc and vice versa.
 *
 * Why a custom dot vs a tooltip library: the audit's whole point is
 * to be unintrusive and always-on. A 6px coloured dot is signal-rich
 * (red instantly grabs the eye) without consuming layout space.
 */
export function TrustDot({ id, size = 8 }: { id: string; size?: number }) {
  const entry = TRUST[id];
  if (!entry) {
    if (import.meta.env.DEV) {
      console.warn(`<TrustDot id="${id}" /> — no entry in trustGrades.ts`);
    }
    return null;
  }
  const colour = COLOUR[entry.grade];
  const tooltip =
    `${entry.label}  ·  ${entry.grade.toUpperCase()}\n\n` +
    `${entry.reason}` +
    (entry.promoteWhen ? `\n\nPromotes to next tier when: ${entry.promoteWhen}` : "");

  return (
    <span
      role="img"
      aria-label={`${entry.label}: trust grade ${entry.grade}`}
      title={tooltip}
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        background: colour,
        marginLeft: 4,
        marginRight: 2,
        verticalAlign: "middle",
        cursor: "help",
        // Subtle ring so the dot stays legible against any background
        // colour the parent metric is using.
        boxShadow: "0 0 0 1px rgba(0,0,0,0.35)",
        flexShrink: 0,
      }}
    />
  );
}

const COLOUR: Record<TrustGrade, string> = {
  green: "#3fb950",     // success-green; readable on dark + light bg
  yellow: "#d29922",    // warning-amber; not so bright it screams
  red: "#f85149",       // alert-red; reserved for "do not trust"
};

/** Inline legend strip — drop into a page header so first-time
 * visitors don't have to guess what the dots mean. */
export function TrustLegend() {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 12,
        fontSize: 11,
        color: "var(--text-muted)",
      }}
    >
      <span>
        <Dot grade="green" />
        verified
      </span>
      <span>
        <Dot grade="yellow" />
        in progress / caveats
      </span>
      <span>
        <Dot grade="red" />
        known issue
      </span>
      <a
        href="https://github.com/sunnylnct007/tradepro/blob/main/TRUST_STATUS.md"
        target="_blank"
        rel="noreferrer"
        style={{ color: "var(--text-dim)" }}
      >
        details
      </a>
    </span>
  );
}

function Dot({ grade }: { grade: TrustGrade }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: COLOUR[grade],
        marginRight: 4,
        verticalAlign: "middle",
        boxShadow: "0 0 0 1px rgba(0,0,0,0.35)",
      }}
    />
  );
}
