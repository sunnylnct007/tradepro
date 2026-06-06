/**
 * deskFormat — shared number/colour helpers for the /desk cockpit, so every
 * tile, KPI and table cell formats money/percent identically and colours
 * red/green by sign consistently. Native-currency only — we never blend
 * across currencies (T212 USD vs IG GBP), matching the rest of TradePro.
 */

export const UP = "#1fc16b";
export const DOWN = "#ef4444";

/** Currency symbol for the common cases; falls back to "CCY " prefix. */
export function ccySym(c: string | null | undefined): string {
  if (!c) return "";
  if (c === "GBP") return "£";
  if (c === "USD") return "$";
  if (c === "EUR") return "€";
  return c + " ";
}

/** Colour for a signed value (green ≥ 0, red < 0). null → muted. */
export function signColour(n: number | null | undefined): string {
  if (n == null) return "var(--text-muted)";
  return n >= 0 ? UP : DOWN;
}

/** Money in native currency. `signed` adds an explicit +/− for P&L. */
export function fmtMoney(
  n: number | null | undefined,
  ccy?: string | null,
  signed = false,
): string {
  if (n == null) return "n/a";
  const abs = Math.abs(n) >= 1e6
    ? Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = signed ? (n >= 0 ? "+" : "−") : (n < 0 ? "−" : "");
  return `${sign}${ccySym(ccy)}${abs}`;
}

/** Percent with explicit sign, 2dp. null → n/a. */
export function fmtPct(n: number | null | undefined): string {
  if (n == null) return "n/a";
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}

/** Plain number (qty / price), trimmed. null → n/a. */
export function fmtNum(n: number | null | undefined, maxFrac = 4): string {
  if (n == null) return "n/a";
  return n.toLocaleString(undefined, { maximumFractionDigits: maxFrac });
}
