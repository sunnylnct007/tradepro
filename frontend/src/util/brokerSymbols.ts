/**
 * Broker symbol helpers — one place to normalise the different symbol
 * encodings brokers use so the cockpit can reconcile + display them
 * consistently:
 *   T212 equity : "AAPL_US_EQ"          → bare "AAPL"     → pretty "AAPL"
 *   IG FX epic  : "CS.D.EURUSD.MINI.IP" → bare "EURUSD"   → pretty "EUR/USD"
 */

/** Reconciliation key: bare ticker / currency pair, no broker cruft. */
export function bareSymbol(raw: string): string {
  const s = (raw || "").toUpperCase();
  if (s.startsWith("CS.D.") || s.startsWith("IX.D.")) {
    const parts = s.split(".");
    if (parts.length >= 4) return parts[2];
  }
  if (s.includes("_")) return s.split("_")[0];
  return s;
}

/** Human-readable label for a UI cell (FX pairs get a slash). */
export function prettySymbol(raw: string): string {
  const bare = bareSymbol(raw);
  if (/^[A-Z]{6}$/.test(bare)) return `${bare.slice(0, 3)}/${bare.slice(3)}`;
  return bare;
}

/** Product / asset classes the cockpit can segregate by. Extend here as
 * we add brokers/instruments — Options, Futures and Crypto are planned
 * (the positions view groups by this, so a new value = a new card). */
export type ProductType = "Equity" | "FX" | "Option" | "Future" | "Crypto";

/** Product class inferred from the symbol/epic. Best-effort heuristics
 * over the encodings we see today; refine per broker as real Option /
 * Future / Crypto instruments start flowing through. */
export function productOf(raw: string): ProductType {
  const s = (raw || "").toUpperCase();
  const bare = bareSymbol(raw);
  // OCC-style option symbol: ROOT + YYMMDD + C/P + strike (e.g. AAPL230616C00150000)
  if (/\d{6}[CP]\d{5,}$/.test(s)) return "Option";
  if (s.includes("OPT") || s.includes(".OPT.")) return "Option";
  if (/(BTC|ETH|USDT|USDC)/.test(bare)) return "Crypto";
  if (s.startsWith("IX.D.") || s.includes("FUT")) return "Future";
  if (/^[A-Z]{6}$/.test(bare)) return "FX";
  return "Equity";
}

/** Short broker label for a chip, e.g. "T212_DEMO" → "T212 · demo". */
export function brokerLabel(broker: string | null | undefined): string {
  if (!broker) return "—";
  const m = broker.match(/^([A-Z0-9]+)_(\w+)$/);
  return m ? `${m[1]} · ${m[2].toLowerCase()}` : broker;
}
