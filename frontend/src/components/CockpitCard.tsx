import { useEffect, useState } from "react";

/**
 * Show/hide cockpit widget card.
 *
 * Per the trader's UX rule (single-screen cockpit, panels not pages):
 * each card has a chevron toggle in its header so the operator can
 * minimise the ones they don't currently care about and keep the
 * ones they do open side-by-side. Open/closed state persists per id
 * in localStorage so the layout the trader sets up survives reloads.
 *
 * `badge` is the small count/label rendered next to the title (e.g.
 * "3 pending"). Tinted via `tone` to make the failure surface
 * visible in the minimized state too — operator sees "Intents 3"
 * even when the panel body is collapsed.
 */
export function CockpitCard({
  id,
  title,
  badge,
  tone = "default",
  defaultOpen = true,
  actions,
  children,
}: {
  id: string;
  title: string;
  badge?: string | number;
  tone?: "default" | "ok" | "warn" | "down";
  defaultOpen?: boolean;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const storageKey = `cockpit.card.${id}.open`;
  const [open, setOpen] = useState<boolean>(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem(storageKey) : null;
    if (saved === "true") return true;
    if (saved === "false") return false;
    return defaultOpen;
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, String(open));
    } catch {
      // localStorage quota / disabled — non-fatal, just don't persist.
    }
  }, [open, storageKey]);

  const badgeTone = (() => {
    switch (tone) {
      case "ok":   return { fg: "#1fc16b", bg: "rgba(31,193,107,0.14)" };
      case "warn": return { fg: "#d97706", bg: "rgba(217,119,6,0.14)" };
      case "down": return { fg: "#ef4444", bg: "rgba(239,68,68,0.14)" };
      default:     return { fg: "var(--text-muted)", bg: "rgba(255,255,255,0.06)" };
    }
  })();

  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--surface-1, rgba(255,255,255,0.02))",
        marginBottom: 12,
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
        {actions && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ marginLeft: "auto", display: "flex", gap: 6 }}
          >
            {actions}
          </div>
        )}
      </header>
      {open && (
        <div style={{ padding: "10px 12px" }}>{children}</div>
      )}
    </section>
  );
}
