import { useEffect, useState } from "react";

/**
 * Show/hide cockpit widget card.
 *
 * Per the trader's UX rule (single-screen cockpit, panels not pages):
 * each card has a chevron toggle to minimise + an × to fully hide.
 * Hidden cards reappear from the cockpit's "Hidden widgets" toolbar.
 * Open + hidden state both persist per `id` in localStorage so the
 * trader's layout survives reloads.
 *
 * `badge` is the small count/label next to the title (e.g. "3 pending").
 * Tinted via `tone` to make the failure surface visible in the
 * minimized state too — operator sees "Intents 3" even when the
 * panel body is collapsed.
 */
export function CockpitCard({
  id,
  title,
  badge,
  tone = "default",
  defaultOpen = true,
  actions,
  children,
  fullWidth = false,
  hideable = true,
  onHide,
}: {
  id: string;
  title: string;
  badge?: string | number;
  tone?: "default" | "ok" | "warn" | "down";
  defaultOpen?: boolean;
  actions?: React.ReactNode;
  children: React.ReactNode;
  /** Spans every grid column when the parent is a CSS grid. */
  fullWidth?: boolean;
  /** Whether the × hide button is rendered. Off for cards that must
   *  always be visible (e.g. health row). Default true. */
  hideable?: boolean;
  /** Called when the trader clicks ×. Cockpit shell tracks the hidden
   *  set + re-rendering this card disappears. */
  onHide?: () => void;
}) {
  const storageKey = `cockpit.card.${id}.open`;
  const [open, setOpen] = useState<boolean>(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem(storageKey) : null;
    if (saved === "true") return true;
    if (saved === "false") return false;
    return defaultOpen;
  });

  useEffect(() => {
    try { localStorage.setItem(storageKey, String(open)); } catch { /* noop */ }
  }, [open, storageKey]);

  const badgeTone = BADGE_TONES[tone];

  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--surface-1, rgba(255,255,255,0.02))",
        marginBottom: 0,
        gridColumn: fullWidth ? "1 / -1" : undefined,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: open ? "1px solid var(--border)" : "none",
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setOpen((x) => !x)}
      >
        <span
          aria-hidden
          style={{
            width: 14,
            color: "var(--text-muted)",
            fontSize: 10,
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform 0.1s ease",
          }}
        >
          ▸
        </span>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
          {title}
        </h3>
        {badge !== undefined && badge !== null && badge !== "" && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              padding: "2px 7px",
              borderRadius: 999,
              background: badgeTone.bg,
              color: badgeTone.fg,
              letterSpacing: "0.04em",
            }}
          >
            {badge}
          </span>
        )}
        <div
          onClick={(e) => e.stopPropagation()}
          style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}
        >
          {actions}
          {hideable && onHide && (
            <button
              onClick={onHide}
              title="Hide this widget — restore it from the 'Hidden widgets' toolbar at the top"
              aria-label={`Hide ${title}`}
              style={{
                width: 22, height: 22,
                padding: 0, fontSize: 14,
                background: "transparent",
                border: "1px solid transparent",
                borderRadius: 4,
                color: "var(--text-muted)",
                cursor: "pointer",
                lineHeight: 1,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = "var(--border)";
                e.currentTarget.style.color = "var(--text-dim)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = "transparent";
                e.currentTarget.style.color = "var(--text-muted)";
              }}
            >
              ×
            </button>
          )}
        </div>
      </header>
      {open && (
        <div style={{ padding: "10px 12px" }}>{children}</div>
      )}
    </section>
  );
}

const BADGE_TONES: Record<
  "default" | "ok" | "warn" | "down",
  { fg: string; bg: string }
> = {
  default: { fg: "var(--text-muted)", bg: "rgba(255,255,255,0.06)" },
  ok:      { fg: "#1fc16b",           bg: "rgba(31,193,107,0.14)" },
  warn:    { fg: "#d97706",           bg: "rgba(217,119,6,0.14)" },
  down:    { fg: "#ef4444",           bg: "rgba(239,68,68,0.14)" },
};
