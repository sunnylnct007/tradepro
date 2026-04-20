import { Link } from "react-router-dom";
import { HELP, type HelpEntry } from "../docs/tooltips";

interface Props {
  /** Key into the HELP dictionary. */
  k: keyof typeof HELP;
}

/** Small ⓘ icon with a hover/focus popover explaining the adjacent control.
 * Falls back to the native title attribute so screen readers still see the text. */
export function Info({ k }: Props) {
  const entry: HelpEntry | undefined = HELP[k];
  if (!entry) return null;
  return (
    <span className="info" tabIndex={0} aria-label={entry.title} title={`${entry.title} — ${entry.body}`}>
      <span aria-hidden>i</span>
      <span className="info-pop" role="tooltip">
        <strong style={{ display: "block", marginBottom: 4, color: "var(--text)" }}>{entry.title}</strong>
        <span style={{ color: "var(--text-dim)" }}>{entry.body}</span>
        {entry.href && (
          <>
            <br />
            <Link to={`/help${entry.href}`} style={{ fontSize: 11 }}>Learn more →</Link>
          </>
        )}
      </span>
    </span>
  );
}
