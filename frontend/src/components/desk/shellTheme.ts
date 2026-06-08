/**
 * shellTheme — shared IBKR-Desktop-style chrome tokens + responsiveness hook.
 *
 * Extracted so BOTH the new /desk cockpit (DeskShell) and the app-wide legacy
 * <Layout> render the same dark admin chrome (no drift between the two shells).
 * Plain hex (not CSS vars) for the chrome surfaces because these are the shell
 * frame itself; the work-area content still uses var(--text)/var(--accent) etc.
 */
import { useEffect, useState } from "react";

/** Outermost shell background (work-area canvas). */
export const SHELL_BG = "#0a0e17";
/** Top bar background. */
export const BAR_BG = "#0d1117";
/** Left nav rail / sidebar background. */
export const RAIL_BG = "#0d1117";
/** Hairline separator (borders between chrome zones). */
export const SEP = "#1b2233";

/** Viewport width (px) at/below which the shell switches to its mobile layout. */
export const MOBILE_BREAKPOINT = 760;

/** Hook: true when the viewport is at/below the mobile breakpoint. */
export function useIsMobile(): boolean {
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
