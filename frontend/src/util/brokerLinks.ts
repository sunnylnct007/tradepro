/**
 * brokerLinks — deep-links to each broker's OWN platform, for the
 * "central orchestrator, don't reinvent the wheel" vision: operational DETAIL
 * (positions, fills, charts, per-account P&L) lives in the broker's UI, not a
 * rebuilt TradePro screen. TradePro shows the cross-broker decision/summary
 * layer and links OUT to the broker for the raw detail.
 *
 * Per-symbol deep-links aren't reliably supported across brokers (portals are
 * login/session-gated), so we link to each broker's portal home where the
 * position detail lives. URLs are config here — tweak without a code change
 * (feedback_config_driven_no_hardcoding). Verified 2026-07-02.
 */
export interface BrokerPortal {
  label: string;
  url: string;
}

// Keyed by the OMS broker code. `_DEMO`/`_PAPER` variants share the same portal.
export const BROKER_PORTALS: Record<string, BrokerPortal> = {
  IBKR: { label: "IBKR portal", url: "https://www.interactivebrokers.com/sso/Login" },
  IBKR_PAPER: { label: "IBKR portal (paper)", url: "https://www.interactivebrokers.com/sso/Login" },
  T212: { label: "Trading 212", url: "https://app.trading212.com" },
  T212_DEMO: { label: "Trading 212 (demo)", url: "https://app.trading212.com" },
  IG: { label: "IG platform", url: "https://www.ig.com/en/login" },
  IG_DEMO: { label: "IG platform (demo)", url: "https://www.ig.com/en/login" },
};

/** Resolve a broker code (exact, else prefix before `_`) to its portal, or null. */
export function brokerPortal(broker?: string | null): BrokerPortal | null {
  if (!broker) return null;
  return BROKER_PORTALS[broker] ?? BROKER_PORTALS[broker.split("_")[0]] ?? null;
}
