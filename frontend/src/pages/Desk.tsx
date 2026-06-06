/**
 * Desk — the new IBKR-Desktop-style northstar cockpit, mounted at /desk.
 *
 * Standalone from the legacy /trader cockpit and the app-wide <Layout> nav:
 * it brings its own three-zone shell (DeskShell) so we can switch the default
 * to it later without disturbing anything that exists today.
 *
 * Work-area composition (this build = the Portfolio view):
 *   - DeskKpiRow     — per-broker account KPI tiles
 *   - DeskTabs       — Positions (centerpiece) · Orders · Trades · Balances
 *   - DeskRightRail  — account-value chart + news stub
 *
 * Layout: a 2-column grid [work-area | right-rail] on wide screens that
 * collapses to a single column (rail stacked BELOW) under WIDE_BREAKPOINT —
 * a `minmax(0, …)` first column lets the dense tables shrink/scroll instead
 * of forcing page-level horizontal overflow.
 */
import { useEffect, useState } from "react";
import { DeskShell } from "../components/desk/DeskShell";
import { DeskKpiRow } from "../components/desk/DeskKpiRow";
import { DeskTabs } from "../components/desk/DeskTabs";
import { DeskRightRail } from "../components/desk/DeskRightRail";

const WIDE_BREAKPOINT = 1024;

function useWide(): boolean {
  const [wide, setWide] = useState(
    typeof window !== "undefined" ? window.innerWidth > WIDE_BREAKPOINT : true,
  );
  useEffect(() => {
    const onResize = () => setWide(window.innerWidth > WIDE_BREAKPOINT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return wide;
}

export function Desk() {
  const wide = useWide();
  return (
    <DeskShell>
      <DeskKpiRow />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: wide ? "minmax(0, 1fr) 320px" : "1fr",
          gap: 14,
          alignItems: "start",
        }}
      >
        <DeskTabs />
        <DeskRightRail />
      </div>
    </DeskShell>
  );
}
