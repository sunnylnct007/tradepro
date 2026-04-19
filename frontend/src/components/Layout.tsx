import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  padding: "6px 12px",
  borderRadius: 8,
  textDecoration: "none",
  color: isActive ? "var(--text)" : "var(--text-dim)",
  background: isActive ? "var(--bg-hover)" : "transparent",
  fontWeight: isActive ? 600 : 500,
  fontSize: 13,
  transition: "background 0.15s ease, color 0.15s ease",
});

export function Layout() {
  const { user, firebaseAvailable, signIn, signOut } = useAuth();

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
        <nav style={{ display: "flex", gap: 4 }}>
          <NavLink to="/" end style={navLinkStyle}>Scanner</NavLink>
          <NavLink to="/signals" style={navLinkStyle}>Signal</NavLink>
          <NavLink to="/simulations" style={navLinkStyle}>Simulations</NavLink>
          <NavLink to="/charts" style={navLinkStyle}>Charts</NavLink>
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
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
        <Outlet />
      </main>
    </div>
  );
}
