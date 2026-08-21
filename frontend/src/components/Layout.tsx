import { useState, type CSSProperties } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { SystemBanners } from "./SystemBanners";
import { WorkerStatusBadge } from "./WorkerStatusBadge";
import { useAuth } from "../auth/AuthProvider";
import { ModePill } from "./ModePill";
import { OmsModeBadge } from "./OmsModeBadge";
import { ConnectivityBadge } from "./ConnectivityBadge";
import { SHELL_BG, BAR_BG, RAIL_BG, SEP, useIsMobile } from "./desk/shellTheme";

// App-wide shell for every non-/desk route. Re-skinned to the IBKR-Desktop
// admin look the /desk cockpit uses (DeskShell): dark chrome, a persistent
// LEFT SIDEBAR (no dropdown), a top bar carrying the status badges, and a
// fixed-viewport grid where ONLY the work-area scrolls.
//
// Two personas drive the nav grouping:
//
//   Trader        — opens MARKET / RESEARCH / PAPER all day. These sit at the
//                   top of the sidebar in workflow order: decide → research →
//                   sim → paper-trade.
//   IT analyst    — opens SYSTEM when something looks wrong (broker
//                   disconnected, scheduled job missed, deploy issue) or when
//                   triaging via Support / IT Guide runbooks.
//
//  MARKET   — daily decision surface. Decide is the index; Portfolio shows
//             live positions; Scan explores a single strategy; OMS is orders.
//  STRATEGY — strategy catalog + per-strategy pages + backtests.
//  RESEARCH — analysis tools (signals explorer, backtest, scanner, charts, docs).
//  PAPER    — paper-trading lifecycle (live sessions, PA reports, intraday board).
//  SYSTEM   — diagnostics + config + runbooks.
//
// Routes haven't changed — only the chrome — so bookmarks + CI URL maps still
// resolve. NO dropdown: the user explicitly dislikes dropdowns on primary
// surfaces, so every link is always visible in a labeled, grouped sidebar.

type NavItem = { to: string; label: string; end?: boolean };
type NavSection = { label: string; items: NavItem[] };

// Trader's daily surfaces — the top, ungrouped block of the sidebar.
const marketNav: NavItem[] = [
  // Cockpit points at the IBKR-style /desk surface (the legacy
  // TraderCockpit was deleted 21 Aug 2026).
  { to: "/desk",         label: "Cockpit"    },
  // Decide — long-term / multi-strategy comparison view. Daily-use.
  { to: "/compare",      label: "Decide"     },
  { to: "/scan",         label: "Scan"       },
  { to: "/oms",          label: "OMS"        },
  { to: "/portfolio",    label: "Portfolio"  },
];

// Grouped secondary surfaces. Previously hidden behind a "More ▾" dropdown;
// now rendered inline as labeled sidebar sections (the section labels double
// as group headers).
const moreSections: NavSection[] = [
  { label: "Strategy", items: [
    { to: "/strategies",                  label: "Strategies catalog" },
    { to: "/strategies/ichimoku-equity",  label: "Ichimoku Equity"    },
    { to: "/strategies/ichimoku-fx",      label: "Ichimoku FX"        },
    { to: "/backtests",                   label: "Backtests"          },
  ]},
  { label: "Research", items: [
    { to: "/signals",      label: "Research"   },
    { to: "/simulations",  label: "Backtest"   },
    { to: "/scanner",      label: "Scanner"    },
    { to: "/charts",       label: "Charts"     },
    { to: "/documents",    label: "Docs"       },
    { to: "/screener",     label: "Daily Screener" },
  ]},
  { label: "Paper / History", items: [
    { to: "/paper-live",            label: "Paper sessions"  },
    { to: "/paper-backtest",        label: "PA Reports"      },
    { to: "/intraday/leaderboard",  label: "Intraday board"  },
  ]},
  { label: "System", items: [
    { to: "/risk",               label: "Risk"      },
    { to: "/universes",          label: "Universes" },
    { to: "/settings",           label: "Settings"  },
    { to: "/health",             label: "Health"    },
    { to: "/admin/data",         label: "IT Data"   },
    { to: "/help/trade-support", label: "Support"   },
    { to: "/help/ops-runbook",   label: "IT Guide"  },
  ]},
];

const SIDEBAR_WIDTH = 210;

const sidebarLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  display: "block",
  padding: "7px 14px",
  textDecoration: "none",
  color: isActive ? "var(--text)" : "var(--text-dim)",
  background: isActive ? "var(--bg-hover)" : "transparent",
  fontWeight: isActive ? 600 : 500,
  fontSize: 13,
  borderLeft: `3px solid ${isActive ? "var(--accent, #4f8cff)" : "transparent"}`,
  transition: "background 0.15s ease, color 0.15s ease",
});

const sectionLabelStyle: CSSProperties = {
  fontSize: 9,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.08em",
  padding: "14px 14px 4px",
};

/** The grouped, labeled, no-dropdown sidebar nav. Shared desktop + drawer. */
function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav style={{ display: "flex", flexDirection: "column", paddingBottom: 16 }}>
      {/* Primary market block — ungrouped, at the top. */}
      <div style={{ paddingTop: 8 }}>
        {marketNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            style={sidebarLinkStyle}
          >
            {item.label}
          </NavLink>
        ))}
      </div>
      {moreSections.map((s) => (
        <div key={s.label}>
          <div style={sectionLabelStyle}>{s.label}</div>
          {s.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onNavigate}
              style={sidebarLinkStyle}
            >
              {item.label}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

/** Wordmark — matches DeskShell's "Trade<accent>Pro</accent>" style. */
function Wordmark() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontWeight: 800,
        fontSize: 16,
        letterSpacing: "0.02em",
        color: "var(--text)",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          width: 24,
          height: 24,
          borderRadius: 7,
          background: "linear-gradient(135deg, #4f8cff, #1fc16b)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: "white",
          fontSize: 13,
          fontWeight: 800,
        }}
      >
        T
      </span>
      Trade<span style={{ color: "var(--accent, #4f8cff)" }}>Pro</span>
    </div>
  );
}

export function Layout() {
  const { user, firebaseAvailable, error, signIn, signOut } = useAuth();
  const mobile = useIsMobile();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Status group lives in the top bar on every page (was the right side of
  // the old header). Order + components preserved from the legacy layout.
  const statusGroup = (
    <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
      {/* Intraday / Long-term mode switch — persisted to localStorage. */}
      <ModePill />
      {/* System connectivity traffic light — click for per-service detail. */}
      <ConnectivityBadge />
      {/* Worker status — visible on every page so the user can tell at a
          glance whether the Mac worker is mid-refresh, idle, or stale. */}
      <WorkerStatusBadge />
      {/* OMS placement mode (auto/manual). */}
      <OmsModeBadge />
      <span
        className="num"
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          padding: "4px 8px",
          border: `1px solid ${SEP}`,
          borderRadius: 6,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        UK · GBP
      </span>
      {firebaseAvailable ? (
        user ? (
          <div style={{
            display: "flex", gap: 8, alignItems: "center",
            padding: "3px 4px 3px 10px", borderRadius: 999,
            border: `1px solid ${SEP}`,
          }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              {user.displayName ?? user.email}
            </span>
            <button onClick={signOut} style={{ fontSize: 11, padding: "4px 9px", borderRadius: 999 }}>
              Sign out
            </button>
          </div>
        ) : (
          <button className="primary" onClick={signIn} style={{ fontSize: 12, padding: "6px 12px" }}>
            Sign in with Google
          </button>
        )
      ) : (
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>(local mode)</span>
      )}
    </div>
  );

  return (
    <div
      style={{
        // Fixed-viewport shell: the page never scrolls; only <main> does.
        height: "100vh",
        background: SHELL_BG,
        color: "var(--text)",
        display: "grid",
        gridTemplateColumns: mobile ? "1fr" : `${SIDEBAR_WIDTH}px 1fr`,
        gridTemplateRows: "52px 1fr",
        gridTemplateAreas: mobile
          ? `"topbar" "main"`
          : `"topbar topbar" "sidebar main"`,
        overflow: "hidden",
      }}
    >
      {/* Top bar — wordmark + (mobile) hamburger on the left, status badges
          on the right; mirrors DeskShell's TopBar chrome. */}
      <header
        style={{
          gridArea: "topbar",
          background: BAR_BG,
          borderBottom: `1px solid ${SEP}`,
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "0 14px",
          minWidth: 0,
        }}
      >
        {mobile && (
          <button
            type="button"
            aria-label="Toggle navigation"
            onClick={() => setDrawerOpen((v) => !v)}
            style={{
              background: "transparent",
              border: `1px solid ${SEP}`,
              borderRadius: 6,
              color: "var(--text)",
              fontSize: 16,
              lineHeight: 1,
              padding: "5px 9px",
              cursor: "pointer",
              flexShrink: 0,
            }}
          >
            ☰
          </button>
        )}
        <Wordmark />
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", minWidth: 0, overflow: "hidden" }}>
          {statusGroup}
        </div>
      </header>

      {/* Desktop persistent sidebar. */}
      {!mobile && (
        <aside
          style={{
            gridArea: "sidebar",
            background: RAIL_BG,
            borderRight: `1px solid ${SEP}`,
            overflowY: "auto",
            minHeight: 0,
          }}
        >
          <SidebarNav />
        </aside>
      )}

      {/* Mobile drawer — hamburger-toggled overlay sidebar. */}
      {mobile && drawerOpen && (
        <>
          <div
            onClick={() => setDrawerOpen(false)}
            style={{
              position: "fixed",
              inset: 0,
              top: 52,
              zIndex: 40,
              background: "rgba(0,0,0,0.5)",
            }}
          />
          <aside
            style={{
              position: "fixed",
              top: 52,
              bottom: 0,
              left: 0,
              width: SIDEBAR_WIDTH,
              zIndex: 41,
              background: RAIL_BG,
              borderRight: `1px solid ${SEP}`,
              overflowY: "auto",
            }}
          >
            <SidebarNav onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </>
      )}

      {/* Work-area — the ONLY scrolling region. Full width (no centered
          max-width column); generous inner cap so wide tables breathe but
          prose-width pages stay readable. */}
      <main
        style={{
          gridArea: "main",
          minWidth: 0,
          minHeight: 0,
          overflowY: "auto",
        }}
      >
        <SystemBanners />
        <div style={{ padding: mobile ? "16px 16px" : "24px 28px", maxWidth: 1600, margin: "0 auto", width: "100%" }}>
          {error && (
            <div
              style={{
                padding: "10px 14px",
                marginBottom: 16,
                borderRadius: 8,
                border: "1px solid var(--down)",
                background: "var(--down-soft)",
                color: "var(--down)",
                fontSize: 13,
              }}
            >
              Sign-in failed: {error}
            </div>
          )}
          <Outlet />
        </div>
      </main>
    </div>
  );
}
