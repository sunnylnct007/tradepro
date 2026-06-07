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
 *   LSE ETFs     "SWDA" / "VWRL"       → "SWDA.L" / "VWRL.L" (GBP-quoted, London Stock Exchange)
 * Returns null for:
 *   IG FX/CFD epics ("CS.D.EURUSD.MINI.IP", "*.CASH.IP") — no clean Yahoo symbol
 *   options/futures/crypto epics, and anything empty.
 *
 * `broker` is a hint ("T212" | "IG" | "IBKR"); when a broker already hands us a
 * Yahoo symbol (T212's `yahooSymbol`) prefer that and skip this.
 */

/**
 * Known LSE-listed ETF tickers (GBP, London Stock Exchange) that require a
 * ".L" suffix in Yahoo Finance. Extend this map as more ETFs are traded.
 * Source: these tickers are held in IBKR/IG as plain-alpha names but Yahoo
 * needs the exchange suffix to resolve OHLC candles.
 */
const LSE_ETF_MAP: Record<string, string> = {
  SWDA: "SWDA.L",  // iShares Core MSCI World UCITS ETF (GBP, LSE)
  VWRL: "VWRL.L",  // Vanguard FTSE All-World UCITS ETF (GBP, LSE)
  VUSA: "VUSA.L",  // Vanguard S&P 500 UCITS ETF (GBP, LSE)
  VUAG: "VUAG.L",  // Vanguard S&P 500 UCITS ETF Acc (GBP, LSE)
  CSPX: "CSPX.L",  // iShares Core S&P 500 UCITS ETF (GBP, LSE)
  IWDG: "IWDG.L",  // iShares Edge MSCI World Value Factor UCITS ETF (GBP, LSE)
  HMWO: "HMWO.L",  // HSBC MSCI World UCITS ETF (GBP, LSE)
  VHYL: "VHYL.L",  // Vanguard FTSE All-World High Dividend Yield UCITS ETF (GBP, LSE)
  VAPX: "VAPX.L",  // Vanguard FTSE Developed Asia Pacific ex Japan UCITS ETF (GBP, LSE)
  VERX: "VERX.L",  // Vanguard FTSE Developed Europe ex UK UCITS ETF (GBP, LSE)
};

/**
 * Heuristic: a plain-alpha (letters only, no dots/underscores) ticker held
 * in IBKR or T212-GB that is 4–5 chars long is very likely a GBP-quoted LSE
 * ETF. We check the known map first; if not in the map, apply this heuristic
 * so new ETFs get a ".L" suffix rather than being silently dropped.
 *
 * Heuristic is intentionally narrow — IBKR US equities are 1–6 chars, so a
 * 4-char ticker without an exchange suffix could be either. The presence of
 * the broker hint "IBKR" + a GBP currency context (checked by caller) is the
 * cleaner signal; here we err on the side of attempting ".L" (if Yahoo has no
 * data for a US ticker with ".L" it returns empty candles → renders "no data"
 * cleanly rather than fabricating something).
 */
function lseYahooSymbol(upper: string): string | null {
  // Explicit map first (highest confidence).
  if (LSE_ETF_MAP[upper]) return LSE_ETF_MAP[upper];
  // Heuristic: 4-5 uppercase letters, no dots/digits → likely LSE ETF.
  // Excluded: 1–3 chars (US blue-chips like IBM, GE) and 6 chars (US micro-caps).
  if (/^[A-Z]{4,5}$/.test(upper)) return `${upper}.L`;
  return null;
}

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
    // Non-US T212 suffix (e.g. _UK_EQ) — check LSE ETF map.
    // null → non-clickable row (honest: no map for unknown UK equities)
    return lseYahooSymbol(t212[1]);
  }

  // Plain equity ticker (IBKR "EC", "BABA", "MRVL", "APLD", or "SWDA"/"VWRL"):
  // Try the LSE ETF map first (covers IBKR GBP ETF holdings). If not in the map,
  // fall back to treating it as a US-listed ticker (1–6 letters = Yahoo symbol).
  if (/^[A-Z]{1,6}(\.[A-Z])?$/.test(upper)) {
    // Check the explicit LSE map (highest confidence for known ETFs).
    if (LSE_ETF_MAP[upper]) return LSE_ETF_MAP[upper];
    // Plain ticker with a dot-class suffix (e.g. "BRK.B") is unambiguously US.
    if (/\.[A-Z]$/.test(upper)) return upper;
    // 1–3 letters: almost certainly US (IBM, GE, etc.).
    if (upper.length <= 3) return upper;
    // 6 letters: US micro-cap (IBKR format; 6-char LSE tickers are rare).
    if (upper.length === 6) return upper;
    // 4–5 letters: ambiguous US vs LSE. Prefer US (more common in our universe);
    // the LSE heuristic is only applied when broker context suggests LSE.
    return upper;
  }

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
