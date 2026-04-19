import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  padding: "6px 12px",
  borderRadius: 6,
  textDecoration: "none",
  color: isActive ? "#0b3d91" : "#444",
  background: isActive ? "#e8f0fe" : "transparent",
  fontWeight: isActive ? 600 : 400,
});

export function Layout() {
  const { user, firebaseAvailable, signIn, signOut } = useAuth();

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "12px 24px",
          borderBottom: "1px solid #eee",
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 18 }}>TradePro</div>
        <nav style={{ display: "flex", gap: 8 }}>
          <NavLink to="/" end style={navLinkStyle}>Scanner</NavLink>
          <NavLink to="/signals" style={navLinkStyle}>Signal detail</NavLink>
          <NavLink to="/simulations" style={navLinkStyle}>Simulations</NavLink>
          <NavLink to="/charts" style={navLinkStyle}>Charts</NavLink>
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 12, color: "#888" }}>UK · GBP</span>
          {firebaseAvailable ? (
            user ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 12, color: "#444" }}>
                  {user.displayName ?? user.email}
                </span>
                <button onClick={signOut} style={{ fontSize: 12 }}>Sign out</button>
              </div>
            ) : (
              <button onClick={signIn} style={{ fontSize: 12 }}>Sign in with Google</button>
            )
          ) : (
            <span style={{ fontSize: 11, color: "#888" }}>(local mode — no auth)</span>
          )}
        </div>
      </header>
      <main style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
        <Outlet />
      </main>
    </div>
  );
}
