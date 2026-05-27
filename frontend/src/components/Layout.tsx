import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { WorkerStatusBadge } from "./WorkerStatusBadge";
import { useAuth } from "../auth/AuthProvider";
import { ModePill } from "./ModePill";
import { OmsModeBadge } from "./OmsModeBadge";
import { T212ModeBadge } from "./T212ModeBadge";

// Two personas drive the nav layout:
//
//   Trader        — opens MARKET / RESEARCH / PAPER all day. These
//                   sit on the left, get the primary-link styling, and
//                   are scanned in workflow order: decide → research →
//                   sim → paper-trade.
//   IT analyst    — opens SYSTEM when something looks wrong (broker
//                   disconnected, scheduled job missed, deploy issue)
//                   or when triaging via Support / IT Guide runbooks.
//                   Sits on the right with muted utility-link styling.
//
//  MARKET   — daily decision surface. Decide is the index; Portfolio
//             shows live positions; Strategies runs ad-hoc; Scanner
//             explores a single strategy across the universe.
//  RESEARCH — analysis tools. Research (signals) is the COMPASS
//             explorer; Backtest is historical simulation; Charts is
//             the data dashboard; Docs is the ETF reference library.
//  PAPER    — paper-trading lifecycle. Paper is the live session
//             monitor (Session Detail page deep-links from here);
//             PA Reports + Intraday roll up history.
//  SYSTEM   — diagnostics + config + runbooks. Health is the live API
//             status; Settings owns brokers + flags; Support is the
//             trader-facing help; IT Guide is the ops runbook.
//
// Routes haven't changed — only labels and grouping — so bookmarks
// and the deployed CI URL maps still resolve correctly.

type NavItem = { to: string; label: string; end?: boolean };

// Trader's daily surfaces — kept terse + at the top. Everything
// else is one click away inside the "More" overflow menu so the
// header doesn't sprawl across the page.
const marketNav: NavItem[] = [
  { to: "/trader",       label: "Cockpit"    },
  { to: "/scan",         label: "Scan"       },
  { to: "/oms",          label: "OMS"        },
  { to: "/portfolio",    label: "Portfolio"  },
];

// "More" overflow — grouped sub-menu surfaces secondary pages
// without taking up nav space. Sections double as section headers
// inside the dropdown.
const moreSections: { label: string; items: NavItem[] }[] = [
  { label: "Strategy", items: [
    { to: "/strategies",                  label: "Strategies catalog" },
    { to: "/strategies/ichimoku-equity",  label: "Ichimoku Equity"    },
    { to: "/strategies/ichimoku-fx",      label: "Ichimoku FX"        },
    { to: "/backtests",                   label: "Backtests"          },
  ]},
  { label: "Research", items: [
    { to: "/compare",      label: "Decide"     },
    { to: "/signals",      label: "Research"   },
    { to: "/simulations",  label: "Backtest"   },
    { to: "/scanner",      label: "Scanner"    },
    { to: "/charts",       label: "Charts"     },
    { to: "/documents",    label: "Docs"       },
  ]},
  { label: "Paper / History", items: [
    { to: "/paper-live",            label: "Paper sessions"  },
    { to: "/paper-backtest",        label: "PA Reports"      },
    { to: "/intraday/leaderboard",  label: "Intraday board"  },
  ]},
  { label: "System", items: [
    { to: "/universes",          label: "Universes" },
    { to: "/settings",           label: "Settings"  },
    { to: "/health",             label: "Health"    },
    { to: "/admin/data",         label: "IT Data"   },
    { to: "/help/trade-support", label: "Support"   },
    { to: "/help/ops-runbook",   label: "IT Guide"  },
  ]},
];


const primaryLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  padding: "6px 12px",
  borderRadius: 8,
  textDecoration: "none",
  color: isActive ? "var(--text)" : "var(--text-dim)",
  background: isActive ? "var(--bg-hover)" : "transparent",
  fontWeight: isActive ? 600 : 500,
  fontSize: 13,
  transition: "background 0.15s ease, color 0.15s ease",
});

/**
 * MoreMenu — dropdown that hides secondary nav links so the header
 * stays single-line + scannable. Click toggles open; clicks outside
 * close it. Sections inside are grouped (Strategy / Research /
 * Paper / System) so the trader still has visual structure even
 * once it's collapsed.
 */
function MoreMenu({
  sections,
}: {
  sections: { label: string; items: NavItem[] }[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  // Click-outside-to-close. The previous implementation used
  // React's stopPropagation on the wrapper which doesn't stop the
  // native DOM event from reaching this document listener — so
  // clicking "More" opened then immediately closed in the same tick.
  // Use a containment check on the native event target instead.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          padding: "6px 12px", borderRadius: 8,
          background: open ? "var(--bg-hover)" : "transparent",
          border: "none", color: "var(--text-dim)",
          fontSize: 13, fontWeight: 500, cursor: "pointer",
        }}
      >
        More ▾
      </button>
      {open && (
        <div
          style={{
            position: "absolute", top: "calc(100% + 4px)", left: 0,
            minWidth: 220,
            background: "var(--surface-1, #0b1220)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            padding: 8,
            zIndex: 20,
            display: "flex", flexDirection: "column", gap: 4,
          }}
        >
          {sections.map((s) => (
            <div key={s.label}>
              <div style={{
                fontSize: 9, color: "var(--text-muted)",
                textTransform: "uppercase", letterSpacing: "0.08em",
                padding: "6px 10px 2px",
              }}>
                {s.label}
              </div>
              {s.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={() => setOpen(false)}
                  style={({ isActive }) => ({
                    display: "block",
                    padding: "5px 10px",
                    fontSize: 12, borderRadius: 4,
                    color: isActive ? "var(--text)" : "var(--text-dim)",
                    background: isActive ? "var(--bg-hover)" : "transparent",
                    textDecoration: "none",
                  })}
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


export function Layout() {
  const { user, firebaseAvailable, error, signIn, signOut } = useAuth();

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", overflowX: "hidden" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "6px 20px",
          borderBottom: "1px solid var(--border)",
          background: "rgba(11, 18, 32, 0.85)",
          backdropFilter: "blur(8px)",
          position: "sticky",
          top: 0,
          zIndex: 10,
          // overflow: visible so the More ▾ dropdown can spill below the
          // header. Was overflowX: hidden which clipped the dropdown
          // entirely. The compact 4-link nav no longer needs the clip
          // since it doesn't horizontally overflow.
          overflow: "visible",
          minWidth: 0,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontWeight: 700,
            fontSize: 16,
            letterSpacing: "0.02em",
          }}
        >
          <span
            style={{
              width: 26,
              height: 26,
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
          TradePro
        </div>
        <nav style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
          {marketNav.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} style={primaryLinkStyle}>
              {item.label}
            </NavLink>
          ))}
          <MoreMenu sections={moreSections} />
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          {/* Top-level Intraday / Long-term switch. Persisted to
           * localStorage; pages read it via useTradingMode() to
           * adjust copy, default tabs, and (later) strategy
           * defaults / backtest windows. See DATA_ROADMAP §14. */}
          <ModePill />
          {/* Worker status — visible on every page so the user can
           * tell at-a-glance whether the Mac is mid-refresh, idle,
           * or hasn't pinged in a while. Was previously only on the
           * Decide page; moved here so a user on Portfolio / Research
           * / Backtest never has to wonder "is the worker alive?"
           * after a long gap. */}
          <WorkerStatusBadge />
          {/* T212 broker mode chip — visible on every page so a user
           * can never confuse demo with real money. Hidden when
           * T212 isn't configured. */}
          <OmsModeBadge />
          <T212ModeBadge />
          <span
            className="num"
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              padding: "4px 8px",
              border: "1px solid var(--border)",
              borderRadius: 6,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
            }}
          >
            UK · GBP
          </span>
          {firebaseAvailable ? (
            user ? (
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  {user.displayName ?? user.email}
                </span>
                <button onClick={signOut} style={{ fontSize: 12, padding: "6px 10px" }}>
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
      </header>
      <main
        style={{
          padding: "28px",
          width: "100%",
          maxWidth: 1280,
          margin: "0 auto",
          flex: 1,
        }}
      >
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
      </main>
    </div>
  );
}
