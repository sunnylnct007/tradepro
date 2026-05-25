import { NavLink } from "react-router-dom";

/**
 * Shared sub-nav for the paper-trading lifecycle pages:
 *   Paper         → trigger sessions, see queue status (PaperLive.tsx)
 *   OMS           → every order placed, approve/reject/cancel + mode toggle
 *   PA Reports    → pending-order queue + backtests (PaperBacktest.tsx)
 *   Intraday      → per-strategy leaderboard
 *
 * Rendered at the top of each of those pages so jumping between them
 * is one click instead of going back up to the header nav. Visually
 * lighter than the top header so it doesn't double up the chrome.
 */
const TABS: { to: string; label: string }[] = [
  { to: "/paper-live", label: "Sessions" },
  { to: "/oms", label: "OMS Orders" },
  { to: "/paper-backtest", label: "Pending + Reports" },
  { to: "/intraday/leaderboard", label: "Leaderboard" },
];

export function PaperSubNav() {
  return (
    <nav
      aria-label="Paper trading sub-nav"
      style={{
        display: "flex",
        gap: 2,
        marginBottom: 16,
        borderBottom: "1px solid var(--border)",
        padding: "0 0 0 0",
      }}
    >
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          // end:true on the sessions tab so /paper-live/session/:id
          // doesn't keep highlighting it when on Session Detail
          end={t.to === "/paper-live"}
          style={({ isActive }) => ({
            padding: "8px 14px",
            fontSize: 12,
            fontWeight: 500,
            color: isActive ? "var(--text)" : "var(--text-dim)",
            textDecoration: "none",
            borderBottom: isActive ? "2px solid #4f8cff" : "2px solid transparent",
            marginBottom: -1,
            letterSpacing: "0.02em",
          })}
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}
