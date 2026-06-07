/**
 * ModeBadge — a tiny LIVE / DEMO chip for the /desk cockpit.
 *
 * Makes real-money vs paper unmistakable at a glance: LIVE (amber, real money,
 * read-only — e.g. IBKR LIVE) vs DEMO (muted grey — T212 DEMO / IG DEMO). The
 * mode is derived upstream from the broker label/id/mode already on the API
 * rows (see accountMode in deskFormat); this component is presentation only and
 * changes no data.
 */
import { modeColour, type AccountMode } from "./deskFormat";

export function ModeBadge({ mode }: { mode: AccountMode }) {
  const colour = modeColour(mode);
  return (
    <span
      title={mode === "LIVE" ? "Real-money account (read-only)" : "Paper / demo account"}
      style={{
        fontSize: 9, fontWeight: 800, letterSpacing: "0.06em",
        color: colour, border: `1px solid ${colour}`, borderRadius: 4,
        padding: "0 4px", lineHeight: "14px", verticalAlign: "middle",
        // LIVE gets a faint fill so it reads as a warning, not just text.
        background: mode === "LIVE" ? "rgba(212,121,59,0.12)" : "transparent",
      }}
    >
      {mode}
    </span>
  );
}
