import { useEffect, useState } from "react";
import { config } from "../config";

interface T212Status {
  configured: boolean;
  mode: string;
  reachable: boolean;
  authenticated: boolean;
}

/**
 * Top-of-page chip showing the Trading 212 broker mode. Demo
 * (paper trading) renders amber; live (real money) renders red so
 * the user can never confuse "I'm just simulating" with "this is
 * about to spend real money on the next button click".
 *
 * Polls /api/integrations/trading212/status on mount only — the
 * mode rarely changes mid-session and this isn't worth a
 * websocket. Hidden when T212 is unconfigured (mode=disabled).
 */
export function T212ModeBadge() {
  const [status, setStatus] = useState<T212Status | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${config.apiBaseUrl}/api/integrations/trading212/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) setStatus(data as T212Status);
      })
      .catch(() => {
        // Silent fail — chip just doesn't render.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status || !status.configured) return null;
  const isLive = status.mode === "live";
  const isDemo = status.mode === "demo";
  const colour = isLive
    ? "var(--down)"
    : isDemo
      ? "var(--neutral)"
      : "var(--text-muted)";
  const bg = isLive
    ? "rgba(255, 80, 80, 0.12)"
    : isDemo
      ? "rgba(255, 200, 80, 0.10)"
      : "rgba(255, 255, 255, 0.04)";
  const label = isLive
    ? "T212 · LIVE · EQUITY ONLY"
    : isDemo
      ? "T212 · DEMO · EQUITY ONLY"
      : `T212 · ${status.mode}`;
  // T212's public API only covers the Invest product (equities + ETFs).
  // Even with CFD enabled on the account, FX/CFD has no public REST
  // endpoint — TradePro routes FX through PAPER. Surfacing this in
  // the always-visible header chip + tooltip so a trader who's just
  // enabled CFD on the T212 site doesn't expect FX orders to land
  // there.
  const title = isLive
    ? (
        "Trading 212 LIVE — REAL MONEY. T212 public API covers ONLY "
        + "the Invest product (equities + ETFs). FX / CFD have no "
        + "public API even when enabled on your account, so FX orders "
        + "route to PAPER, not T212. Position values you see are real."
      )
    : isDemo
      ? (
          "Trading 212 demo (paper trading). T212 public API covers "
          + "ONLY the Invest product (equities + ETFs). FX / CFD have "
          + "no public API even when enabled on your account, so FX "
          + "orders route to PAPER, not T212."
        )
      : `Trading 212 mode: ${status.mode}`;
  return (
    <span
      title={title}
      style={{
        fontSize: 11,
        color: colour,
        background: bg,
        padding: "4px 8px",
        border: `1px solid ${colour}`,
        borderRadius: 6,
        letterSpacing: "0.04em",
        fontWeight: 600,
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}
