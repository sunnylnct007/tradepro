import { createContext, useContext, useEffect, useState } from "react";

/** Top-level "what kind of trading am I doing right now" switch.
 *
 * Two modes, persisted to localStorage so the choice survives reloads:
 *   - "long_term" — daily bars, multi-week to multi-year hold.
 *     Default. Drives Compare (Decide), Portfolio, Research.
 *   - "intraday"  — 1m/5m bars, same-session in-and-out. Drives the
 *     Intraday leaderboard + (future) intraday-tuned defaults.
 *
 * Pages read this via `useTradingMode()` and adjust their:
 *   - default tab / timeframe
 *   - subtitle / explainer copy
 *   - per-mode metric column selection (later)
 *
 * Persistence intentionally lives in localStorage rather than the
 * server — this is a per-device UX preference, not a user account
 * setting. Two devices can have different defaults without sync. */

export type TradingMode = "long_term" | "intraday";

const STORAGE_KEY = "tradepro.trading_mode";

interface TradingModeContextValue {
  mode: TradingMode;
  setMode: (m: TradingMode) => void;
}

const TradingModeContext = createContext<TradingModeContextValue | null>(null);

function readStoredMode(): TradingMode {
  if (typeof window === "undefined") return "long_term";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "intraday" || raw === "long_term") return raw;
  } catch {
    // localStorage can throw in private-mode Safari; just fall through.
  }
  return "long_term";
}

export function TradingModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<TradingMode>(readStoredMode);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // Same private-mode caveat — silently best-effort.
    }
  }, [mode]);

  return (
    <TradingModeContext.Provider value={{ mode, setMode: setModeState }}>
      {children}
    </TradingModeContext.Provider>
  );
}

export function useTradingMode(): TradingModeContextValue {
  const v = useContext(TradingModeContext);
  if (!v) {
    // Failing loud — a hook outside the provider is a bug, and
    // returning a default would silently mask the misconfiguration.
    throw new Error("useTradingMode must be used inside <TradingModeProvider>");
  }
  return v;
}
