import { NavLink, Outlet } from "react-router-dom";

const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  padding: "6px 12px",
  borderRadius: 6,
  textDecoration: "none",
  color: isActive ? "#0b3d91" : "#444",
  background: isActive ? "#e8f0fe" : "transparent",
  fontWeight: isActive ? 600 : 400,
});

export function Layout() {
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
          <NavLink to="/" end style={navLinkStyle}>Dashboard</NavLink>
          <NavLink to="/simulations" style={navLinkStyle}>Simulations</NavLink>
        </nav>
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#888" }}>UK · GBP</div>
      </header>
      <main style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
        <Outlet />
      </main>
    </div>
  );
}
