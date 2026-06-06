/**
 * DeskShell — the IBKR-Desktop-style app shell for the new /desk cockpit.
 *
 * Three zones, mounted as a CSS grid:
 *   1. Top bar (full width): search placeholder · TradePro wordmark ·
 *      compact NET LIQ + DAILY P&L summary + account chip.
 *   2. Left icon-nav rail (desktop): Portfolio · Watchlist · Quote ·
 *      Screeners · Layouts · News, Settings pinned at the bottom. On narrow
 *      screens the rail is hidden and the same items render as a bottom tab
 *      bar so the cockpit is usable one-thumb on a phone.
 *   3. Main work-area: whatever the page passes as children.
 *
 * This is a standalone shell (NOT the app-wide <Layout>) because /desk is the
 * new northstar look and is mounted as a sibling route — the legacy /trader
 * cockpit and its nav stay completely untouched.
 *
 * Nav links only point at routes that actually exist in App.tsx; anything we
 * don't have yet (Watchlist / Quote / News / Layouts) is a disabled "coming
 * soon" item rather than a 404.
 *
 * Responsiveness is driven by a width observer (no browser-only media-query
 * reasoning needed for tsc): below MOBILE_BREAKPOINT the layout switches to a
 * single column with a bottom tab bar; the right rail (passed by the page)
 * stacks below the main area via the page's own grid.
 */
import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../../api/client";
import { fmtMoney, signColour } from "./deskFormat";

const MOBILE_BREAKPOINT = 760;

const SHELL_BG = "#0a0e17";
const BAR_BG = "#0d1117";
const RAIL_BG = "#0d1117";
const SEP = "#1b2233";

type NavEntry = {
  key: string;
  label: string;
  icon: string;      // emoji glyph — no icon-font dependency
  to?: string;       // present → real link; absent → disabled stub
  title: string;     // tooltip (route or "coming soon")
};

// Map each rail item to a REAL route or a disabled stub. Verified against
// App.tsx: /desk (this build), /scan (Screeners), /settings exist; there is
// no /watchlist, /quote, /news or /layouts route, so those are stubs.
const NAV: NavEntry[] = [
  { key: "portfolio", label: "Portfolio", icon: "📊", to: "/desk",     title: "Portfolio" },
  { key: "watchlist", label: "Watchlist", icon: "👁",  title: "Watchlist — coming soon" },
  { key: "quote",     label: "Quote",     icon: "💲",  title: "Quote — coming soon" },
  { key: "screeners", label: "Screeners", icon: "🔎", to: "/scan",     title: "Universe scan" },
  { key: "layouts",   label: "Layouts",   icon: "▦",   title: "Layouts — coming soon" },
  { key: "news",      label: "News",      icon: "📰",  title: "News — coming soon" },
];

const SETTINGS: NavEntry = {
  key: "settings", label: "Settings", icon: "⚙", to: "/settings", title: "Settings",
};

/** Hook: true when the viewport is at/below the mobile breakpoint. */
function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    typeof window !== "undefined" ? window.innerWidth <= MOBILE_BREAKPOINT : false,
  );
  useEffect(() => {
    const onResize = () => setMobile(window.innerWidth <= MOBILE_BREAKPOINT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return mobile;
}

export function DeskShell({ children }: { children: ReactNode }) {
  const mobile = useIsMobile();

  return (
    <div
      style={{
        minHeight: "100vh",
        background: SHELL_BG,
        color: "var(--text)",
        display: "grid",
        // Desktop: [rail | main]. Mobile: single column; rail becomes a
        // fixed bottom tab bar (rendered outside the grid flow).
        gridTemplateColumns: mobile ? "1fr" : "64px 1fr",
        gridTemplateRows: "52px 1fr",
        gridTemplateAreas: mobile
          ? `"topbar" "main"`
          : `"topbar topbar" "rail main"`,
      }}
    >
      <TopBar />
      {!mobile && <LeftRail />}
      <main
        style={{
          gridArea: "main",
          minWidth: 0, // let inner tables/charts shrink instead of overflowing
          padding: mobile ? "12px 12px 76px" : "16px 20px", // bottom pad clears the tab bar
        }}
      >
        {children}
      </main>
      {mobile && <BottomTabBar />}
    </div>
  );
}

/** Top bar: search · wordmark · NET LIQ + DAILY P&L summary · account chip. */
function TopBar() {
  return (
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
      {/* Search placeholder — non-functional shell affordance for now. */}
      <div
        style={{
          display: "flex", alignItems: "center", gap: 8,
          background: "#0a0e17", border: `1px solid ${SEP}`, borderRadius: 6,
          padding: "6px 10px", color: "var(--text-muted)", fontSize: 12,
          width: 220, maxWidth: "32vw", flexShrink: 1, minWidth: 0,
        }}
        title="Search (not yet wired)"
      >
        <span>🔍</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Search
        </span>
        <span style={{ marginLeft: "auto", opacity: 0.7 }}>⌘K</span>
      </div>

      <div
        style={{
          fontWeight: 800, fontSize: 16, letterSpacing: "0.02em",
          color: "var(--text)", flexShrink: 0,
        }}
      >
        Trade<span style={{ color: "var(--accent, #4f8cff)" }}>Pro</span>
      </div>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
        <TopSummary />
        <AccountChip />
      </div>
    </header>
  );
}

/**
 * TopSummary — compact NET LIQ + DAILY P&L read pulled from the same
 * endpoints KpiStrip uses (cashSummary + pnlByStrategy). Per broker, native
 * currency: we never blend USD + GBP into one number, so when more than one
 * broker is connected we show the primary (first ok) broker here and leave
 * the full per-broker breakdown to the KPI row in the work-area.
 */
function TopSummary() {
  const [netLiq, setNetLiq] = useState<{ v: number | null; ccy: string } | null>(null);
  const [daily, setDaily] = useState<{ v: number | null; ccy: string } | null>(null);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [cash, pnl] = await Promise.all([
          api.cashSummary(),
          api.pnlByStrategy().catch(() => null),
        ]);
        if (!live) return;
        const primary = cash.brokers.find((b) => b.status === "ok") ?? cash.brokers[0];
        if (primary) {
          setNetLiq({ v: primary.total ?? primary.balance ?? null, ccy: primary.currency ?? "" });
          // Daily P&L = open + realised-today for the primary broker, summed
          // across its strategies (same broker/currency only — never blended).
          const rows = (pnl?.rows ?? []).filter(
            (r) => (r.broker || "").toLowerCase() === (primary.broker || "").toLowerCase(),
          );
          let d: number | null = primary.openPnl ?? null;
          for (const r of rows) {
            if (r.realisedToday != null) d = (d ?? 0) + r.realisedToday;
          }
          setDaily({ v: d, ccy: primary.currency ?? "" });
        }
      } catch {
        /* top summary degrades silently; KPI row shows the detail/error */
      }
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  return (
    <div style={{ display: "flex", gap: 16, minWidth: 0 }}>
      <SummaryItem label="NET LIQ" value={fmtMoney(netLiq?.v, netLiq?.ccy)} colour="var(--text)" />
      <SummaryItem
        label="DAILY P&L"
        value={fmtMoney(daily?.v, daily?.ccy, true)}
        colour={signColour(daily?.v)}
      />
    </div>
  );
}

function SummaryItem({ label, value, colour }: { label: string; value: string; colour: string }) {
  return (
    <div style={{ textAlign: "right", minWidth: 0 }}>
      <div
        style={{
          fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase",
          letterSpacing: "0.06em", whiteSpace: "nowrap",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: colour,
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function AccountChip() {
  return (
    <div
      title="Account"
      style={{
        display: "flex", alignItems: "center", gap: 8, flexShrink: 0,
        background: "#0a0e17", border: `1px solid ${SEP}`, borderRadius: 20,
        padding: "4px 10px 4px 4px",
      }}
    >
      <span
        style={{
          width: 26, height: 26, borderRadius: "50%", background: "var(--accent, #4f8cff)",
          color: "#0a0e17", display: "inline-flex", alignItems: "center",
          justifyContent: "center", fontWeight: 800, fontSize: 12,
        }}
      >
        TP
      </span>
      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Demo</span>
    </div>
  );
}

/** Left icon-nav rail (desktop). Active item: blue + left accent bar. */
function LeftRail() {
  return (
    <nav
      style={{
        gridArea: "rail",
        background: RAIL_BG,
        borderRight: `1px solid ${SEP}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        paddingTop: 8,
      }}
    >
      {NAV.map((n) => (
        <RailItem key={n.key} entry={n} />
      ))}
      <div style={{ marginTop: "auto", paddingBottom: 8 }}>
        <RailItem entry={SETTINGS} />
      </div>
    </nav>
  );
}

function RailItem({ entry }: { entry: NavEntry }) {
  const inner = (active: boolean) => (
    <div
      title={entry.title}
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
        padding: "10px 4px",
        borderLeft: `3px solid ${active ? "var(--accent, #4f8cff)" : "transparent"}`,
        color: active ? "var(--accent, #4f8cff)" : "var(--text-muted)",
        opacity: entry.to ? 1 : 0.45,
        cursor: entry.to ? "pointer" : "default",
      }}
    >
      <span style={{ fontSize: 18, lineHeight: 1 }}>{entry.icon}</span>
      <span style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.04em" }}>
        {entry.label}
      </span>
    </div>
  );

  if (!entry.to) {
    // Disabled stub — render but don't navigate (no 404).
    return <div>{inner(false)}</div>;
  }
  return (
    <NavLink to={entry.to} style={{ textDecoration: "none" }}>
      {({ isActive }) => inner(isActive)}
    </NavLink>
  );
}

/** Bottom tab bar (mobile). Same items, horizontal, fixed to viewport. */
function BottomTabBar() {
  const items = [...NAV, SETTINGS];
  return (
    <nav
      style={{
        position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 20,
        background: BAR_BG, borderTop: `1px solid ${SEP}`,
        display: "flex", justifyContent: "space-around", alignItems: "stretch",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
      }}
    >
      {items.map((n) => (
        <TabItem key={n.key} entry={n} />
      ))}
    </nav>
  );
}

function TabItem({ entry }: { entry: NavEntry }) {
  const inner = (active: boolean) => (
    <div
      title={entry.title}
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
        padding: "8px 4px", flex: 1,
        color: active ? "var(--accent, #4f8cff)" : "var(--text-muted)",
        opacity: entry.to ? 1 : 0.45,
        minWidth: 0,
      }}
    >
      <span style={{ fontSize: 17, lineHeight: 1 }}>{entry.icon}</span>
      <span style={{ fontSize: 8, textTransform: "uppercase", letterSpacing: "0.03em" }}>
        {entry.label}
      </span>
    </div>
  );
  if (!entry.to) return <div style={{ flex: 1 }}>{inner(false)}</div>;
  return (
    <NavLink to={entry.to} style={{ textDecoration: "none", flex: 1 }}>
      {({ isActive }) => inner(isActive)}
    </NavLink>
  );
}
