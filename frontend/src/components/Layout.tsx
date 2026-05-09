import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { T212ModeBadge } from "./T212ModeBadge";

// Primary nav reflects the actual decision flow: pick what to invest in
// (Decide), drill into a single symbol (Research), test a strategy
// (Backtest), or read the underlying research notes (Docs). Utility
// pages (Scanner, Charts, Health, Settings, Help) sit behind a thinner
// visual treatment so they don't compete with the four primary entry
// points. Routes themselves haven't changed — only the labels and
// grouping — so bookmarks and the deployed CI URL maps still resolve.
const primaryNav: { to: string; label: string; end?: boolean }[] = [
  { to: "/compare", label: "Decide" },
  { to: "/portfolio", label: "Portfolio" },
  { to: "/signals", label: "Research" },
  { to: "/simulations", label: "Backtest" },
  { to: "/documents", label: "Docs" },
];

const utilityNav: { to: string; label: string; end?: boolean }[] = [
  // Single-strategy scanner — kept for power-user exploration, but
  // it's no longer the index. The default workflow runs through
  // Decide which already aggregates the full 5-strategy vote.
  { to: "/scanner", label: "Scanner" },
  { to: "/charts", label: "Charts" },
  { to: "/health", label: "Health" },
  { to: "/settings", label: "Settings" },
  { to: "/help", label: "Help" },
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

const utilityLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  padding: "5px 9px",
  borderRadius: 6,
  textDecoration: "none",
  color: isActive ? "var(--text)" : "var(--text-muted)",
  background: isActive ? "var(--bg-hover)" : "transparent",
  fontWeight: isActive ? 600 : 400,
  fontSize: 11,
  letterSpacing: "0.02em",
  transition: "background 0.15s ease, color 0.15s ease",
});

export function Layout() {
  const { user, firebaseAvailable, error, signIn, signOut } = useAuth();

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 20,
          padding: "14px 28px",
          borderBottom: "1px solid var(--border)",
          background: "rgba(11, 18, 32, 0.85)",
          backdropFilter: "blur(8px)",
          position: "sticky",
          top: 0,
          zIndex: 10,
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
        <nav style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {primaryNav.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} style={primaryLinkStyle}>
              {item.label}
            </NavLink>
          ))}
          <span
            aria-hidden
            style={{
              width: 1,
              height: 18,
              background: "var(--border)",
              margin: "0 8px",
            }}
          />
          {utilityNav.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} style={utilityLinkStyle}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          {/* T212 broker mode chip — visible on every page so a user
           * can never confuse demo with real money. Hidden when
           * T212 isn't configured. */}
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
