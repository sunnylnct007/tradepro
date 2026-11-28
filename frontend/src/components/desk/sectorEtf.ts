/** Symbol → sector ETF, the same table the python relative-strength module
 *  uses (sector_rs.py). Unknown names fall back to SPY and SAY so. */
import data from "../../data/sectorEtf.json";

const SYM: Record<string, string> = (data as any).SYMBOL_SECTOR_ETF;
const SEC: Record<string, string> = (data as any).SECTOR_ETF;

export function sectorEtfFor(symbol: string, sector?: string | null): { etf: string; fallback: boolean } {
  const s = symbol.toUpperCase();
  if (SYM[s]) return { etf: SYM[s], fallback: false };
  if (sector && SEC[sector.toLowerCase()]) return { etf: SEC[sector.toLowerCase()], fallback: false };
  return { etf: "SPY", fallback: true };
}
