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

/**
 * Yahoo-style candle symbol for a broker holding, or `null` when there is no
 * honest mapping (so the caller renders "No data" / a non-clickable row rather
 * than fetching candles for a symbol that can't resolve).
 *
 * Resolves:
 *   T212 equity  "AAPL_US_EQ"          → "AAPL"   (strip the _<REGION>_EQ suffix)
 *   IBKR equity  "EC" / "BABA" / "MRVL"→ "EC"     (US-listed tickers ARE the Yahoo symbol)
 * Returns null for:
 *   IG FX/CFD epics ("CS.D.EURUSD.MINI.IP", "*.CASH.IP") — no clean Yahoo symbol
 *   options/futures/crypto epics, and anything empty.
 *
 * `broker` is a hint ("T212" | "IG" | "IBKR"); when a broker already hands us a
 * Yahoo symbol (T212's `yahooSymbol`) prefer that and skip this.
 */
export function chartSymbolFor(raw: string | null | undefined, broker?: string): string | null {
  const s = (raw || "").trim();
  if (!s) return null;
  const upper = s.toUpperCase();

  // IG epics + FX pairs + non-equity products have no honest Yahoo candle symbol.
  if (broker === "IG") return null;
  const product = productOf(upper);
  if (product !== "Equity") return null;
  if (upper.startsWith("CS.D.") || upper.startsWith("IX.D.") || upper.startsWith("OD.D.")) return null;
  if (upper.endsWith(".CASH.IP") || upper.endsWith(".IP")) return null;

  // T212 "AAPL_US_EQ" → bare ticker (US listings map 1:1 to the Yahoo symbol;
  // non-US suffixes would need an exchange-suffix map we don't have, so only
  // accept US/unsuffixed here to stay honest).
  const t212 = upper.match(/^([A-Z0-9.]+)_([A-Z]{2,3})_EQ$/);
  if (t212) {
    if (t212[2] === "US") return t212[1];
    return null; // e.g. _UK_EQ would need ".L"; no honest map → leave non-clickable
  }

  // Plain US-style equity ticker (IBKR "EC", "BABA", "MRVL", "APLD"): 1–6
  // letters, optional dot class (e.g. "BRK.B"). This IS the Yahoo symbol.
  if (/^[A-Z]{1,6}(\.[A-Z])?$/.test(upper)) return upper;

  return null;
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
  // IG option epics ("OD.D.WK2EURO.32.IP" = Weekly EUR option) + the
  // friendly IG names ("Weekly EURUSD 11650 CALL/PUT"). These were
  // falling through to the Equity default and showing on the equity desk.
  if (s.startsWith("OD.D.")) return "Option";
  if (/\b(CALL|PUT)\b/.test(s)) return "Option";
  if (/(BTC|ETH|USDT|USDC)/.test(bare)) return "Crypto";
  if (s.startsWith("IX.D.") || s.includes("FUT")) return "Future";
  // IG spot-FX epics ("CS.D.EURUSD.MINI.IP") reduce to a 6-letter pair.
  if (/^[A-Z]{6}$/.test(bare)) return "FX";
  return "Equity";
}

/** Exchange / venue label so the trader sees WHICH market (and session) an
 * instrument trades on:
 *   "AAPL_US_EQ"          → "US"   (T212 cash equity — region from the suffix)
 *   "VOD_UK_EQ"           → "UK"
 *   "CS.D.EURUSD.MINI.IP" → "FX"   (IG OTC spot FX — no single exchange)
 *   "UA.D.AVGO.CASH.IP"   → "CFD"  (IG share CFD — OTC vs the underlying)
 * Empty string when unknown so the caller can omit the tag. */
export function exchangeOf(raw: string): string {
  const s = (raw || "").toUpperCase();
  if (productOf(s) === "Option") return "OPT";
  const m = s.match(/^[A-Z0-9.]+_([A-Z]{2,3})_EQ$/);
  if (m) return m[1];
  if (s.startsWith("CS.D.") || /^[A-Z]{6}$/.test(bareSymbol(s))) return "FX";
  if (s.endsWith(".CASH.IP")) return "CFD";
  return "";
}

/** Short broker label for a chip, e.g. "T212_DEMO" → "T212 · demo". */
export function brokerLabel(broker: string | null | undefined): string {
  if (!broker) return "—";
  const m = broker.match(/^([A-Z0-9]+)_(\w+)$/);
  return m ? `${m[1]} · ${m[2].toLowerCase()}` : broker;
}
