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
 * View switching: Portfolio / Watchlist / Quote / Screeners / News are IN-PAGE
 * views (the page owns an `active` view-id + `onSelect` callback that this
 * shell drives), NOT routes — so /desk is a single composite cockpit you stay
 * on. Settings is the one real route link (/settings exists). Layouts stays a
 * disabled "coming soon" stub (no layout system yet). Nothing 404s. Quote is a
 * read-only Quote/Chart view (real candles + positions + watchlist; no live
 * bid/ask, so bid/ask are omitted rather than faked).
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

/** The in-page work-area views the rail can switch between. */
export type DeskView = "portfolio" | "screeners" | "news" | "watchlist" | "quote";

type NavEntry = {
  key: string;
  label: string;
  icon: string;        // emoji glyph — no icon-font dependency
  view?: DeskView;     // present → switches the in-page work-area view
  to?: string;         // present → real route link (Settings only)
  title: string;       // tooltip
};

// Rail items. Portfolio / Watchlist / Quote / Screeners / News are in-page
// views; Settings is a route (/settings exists). Layouts is a disabled stub
// (no layout system yet) — never a 404.
const NAV: NavEntry[] = [
  { key: "portfolio", label: "Portfolio", icon: "📊", view: "portfolio", title: "Portfolio" },
  { key: "watchlist", label: "Watchlist", icon: "👁",  view: "watchlist", title: "Watchlist" },
  { key: "quote",     label: "Quote",     icon: "💲",  view: "quote",     title: "Quote / Chart" },
  { key: "screeners", label: "Screeners", icon: "🔎", view: "screeners", title: "Screeners" },
  { key: "layouts",   label: "Layouts",   icon: "▦",   title: "Layouts — coming soon" },
  { key: "news",      label: "News",      icon: "📰",  view: "news",      title: "News & Daily Overview" },
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

export function DeskShell({
  children,
  active,
  onSelect,
}: {
  children: ReactNode;
  active: DeskView;
  onSelect: (view: DeskView) => void;
}) {
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
      {!mobile && <LeftRail active={active} onSelect={onSelect} />}
      <main
        style={{
          gridArea: "main",
          minWidth: 0, // let inner tables/charts shrink instead of overflowing
          padding: mobile ? "12px 12px 76px" : "16px 20px", // bottom pad clears the tab bar
        }}
      >
        {children}
      </main>
      {mobile && <BottomTabBar active={active} onSelect={onSelect} />}
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
function LeftRail({ active, onSelect }: { active: DeskView; onSelect: (v: DeskView) => void }) {
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
        <RailItem key={n.key} entry={n} active={active} onSelect={onSelect} />
      ))}
      <div style={{ marginTop: "auto", paddingBottom: 8 }}>
        <RailItem entry={SETTINGS} active={active} onSelect={onSelect} />
      </div>
    </nav>
  );
}

/** Shared visual for a rail/tab item; `accent` draws the desktop left bar. */
function navInner(entry: NavEntry, on: boolean, accent: boolean) {
  return (
    <div
      title={entry.title}
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
        padding: accent ? "10px 4px" : "8px 4px",
        flex: accent ? undefined : 1,
        borderLeft: accent ? `3px solid ${on ? "var(--accent, #4f8cff)" : "transparent"}` : undefined,
        color: on ? "var(--accent, #4f8cff)" : "var(--text-muted)",
        opacity: entry.view || entry.to ? 1 : 0.45,
        cursor: entry.view || entry.to ? "pointer" : "default",
        minWidth: 0,
      }}
    >
      <span style={{ fontSize: accent ? 18 : 17, lineHeight: 1 }}>{entry.icon}</span>
      <span style={{ fontSize: accent ? 9 : 8, textTransform: "uppercase", letterSpacing: "0.04em" }}>
        {entry.label}
      </span>
    </div>
  );
}

function RailItem({
  entry, active, onSelect,
}: { entry: NavEntry; active: DeskView; onSelect: (v: DeskView) => void }) {
  if (entry.view) {
    const on = entry.view === active;
    return (
      <button
        onClick={() => onSelect(entry.view!)}
        style={{ background: "transparent", border: "none", padding: 0, cursor: "pointer" }}
      >
        {navInner(entry, on, true)}
      </button>
    );
  }
  if (entry.to) {
    return (
      <NavLink to={entry.to} style={{ textDecoration: "none" }}>
        {({ isActive }) => navInner(entry, isActive, true)}
      </NavLink>
    );
  }
  // Disabled stub — render but don't navigate (no 404).
  return <div>{navInner(entry, false, true)}</div>;
}

/** Bottom tab bar (mobile). Same items, horizontal, fixed to viewport. */
function BottomTabBar({ active, onSelect }: { active: DeskView; onSelect: (v: DeskView) => void }) {
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
        <TabItem key={n.key} entry={n} active={active} onSelect={onSelect} />
      ))}
    </nav>
  );
}

function TabItem({
  entry, active, onSelect,
}: { entry: NavEntry; active: DeskView; onSelect: (v: DeskView) => void }) {
  if (entry.view) {
    const on = entry.view === active;
    return (
      <button
        onClick={() => onSelect(entry.view!)}
        style={{ background: "transparent", border: "none", padding: 0, flex: 1, cursor: "pointer" }}
      >
        {navInner(entry, on, false)}
      </button>
    );
  }
  if (entry.to) {
    return (
      <NavLink to={entry.to} style={{ textDecoration: "none", flex: 1 }}>
        {({ isActive }) => navInner(entry, isActive, false)}
      </NavLink>
    );
  }
  return <div style={{ flex: 1 }}>{navInner(entry, false, false)}</div>;
}
