import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

/**
 * Open OPTION positions at the broker, with their P&L.
 *
 * Owner, 31 Aug 2026: "i need visibility on UI on this placed option with its
 * pnl ... like we see in IBKR account".
 *
 * WHY THIS DID NOT EXIST. IBKRPosition never carried assetClass, so an option
 * was indistinguishable from a stock in every surface. Two short puts sat in
 * the paper account showing as plain "SPY" and "QQQ" rows, and the conclusion
 * drawn — by me — was that no option positions existed at all. The data had
 * been there the whole time, untagged.
 *
 * AND THE PERCENTAGE LIED. It read -99.06% on a position that was UP $38.63.
 * IBKR reports an option's avgCost as premium x multiplier (600.95 for a put
 * sold at 6.01) while mktPrice stays per-share (5.62); dividing one by the
 * other compares a total against a unit price. Fixed in the API — this panel
 * shows the corrected figure and the per-share premium.
 *
 * SHORT POSITIONS ARE THE POINT HERE. Everything this desk places is short
 * premium, so a negative quantity is normal and a FALLING price is a GAIN. The
 * card says so rather than leaving the reader to work out why a red-looking
 * number is good news.
 */
type Row = {
  ticker: string;
  instrumentName: string;
  quantity: number;
  averagePricePaid: number | null;
  currentPrice: number | null;
  unrealisedAbs: number | null;
  unrealisedPct: number | null;
  currency: string | null;
  assetClass?: string | null;
  isOption?: boolean;
  multiplier?: number | null;
};

const OK = "#0f8a5f";
const BAD = "#b4232c";
const MUTED = "var(--text-muted)";

export function OptionPositionsCard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const d = await api.ibkrPositions();
      const all = ((d as { positions?: Row[] })?.positions ?? []);
      setRows(all.filter((r) => r.isOption));
      setAt(new Date().toISOString().slice(11, 16) + "Z");
      setErr(null);
    } catch (e) {
      setErr(String((e as Error)?.message || e));
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 60_000);
    return () => clearInterval(t);
  }, [load]);

  if (err) return <div style={{ padding: 12, color: BAD, fontSize: 13 }}>Options unavailable: {err}</div>;

  const total = rows.reduce((a, r) => a + (r.unrealisedAbs ?? 0), 0);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
        <span style={{ fontWeight: 600 }}>Open options — from the broker</span>
        <span style={{ fontSize: 11, color: MUTED }}>
          golden source, not the OMS · {at}
        </span>
        {rows.length > 0 && (
          <span style={{ marginLeft: "auto", fontWeight: 700,
                         color: total >= 0 ? OK : BAD, fontVariantNumeric: "tabular-nums" }}>
            {total >= 0 ? "+" : ""}{total.toFixed(2)}
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <div style={{ fontSize: 12.5, color: MUTED }}>
          No open option positions at the broker.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5,
                        fontVariantNumeric: "tabular-nums" }}>
          <thead>
            <tr style={{ color: MUTED, textAlign: "left", fontSize: 11 }}>
              <th style={{ padding: "5px 6px" }}>Contract</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Qty</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Sold at</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Now</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>P&amp;L</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>%</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const short = r.quantity < 0;
              const pnl = r.unrealisedAbs ?? 0;
              return (
                <tr key={`${r.ticker}-${i}`} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px" }}>
                    {r.instrumentName || r.ticker}
                    {short && (
                      <span style={{ fontSize: 9, marginLeft: 5, color: MUTED }}>SHORT</span>
                    )}
                  </td>
                  <td style={{ padding: "6px", textAlign: "right" }}>{r.quantity}</td>
                  <td style={{ padding: "6px", textAlign: "right" }}>
                    {r.averagePricePaid?.toFixed(2) ?? "—"}
                  </td>
                  <td style={{ padding: "6px", textAlign: "right" }}>
                    {r.currentPrice?.toFixed(2) ?? "—"}
                  </td>
                  <td style={{ padding: "6px", textAlign: "right",
                               color: pnl >= 0 ? OK : BAD, fontWeight: 600 }}>
                    {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                  </td>
                  <td style={{ padding: "6px", textAlign: "right",
                               color: (r.unrealisedPct ?? 0) >= 0 ? OK : BAD }}>
                    {r.unrealisedPct == null ? "—"
                      : `${r.unrealisedPct >= 0 ? "+" : ""}${r.unrealisedPct.toFixed(1)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div style={{ marginTop: 9, fontSize: 11, color: MUTED, lineHeight: 1.55 }}>
        Short premium: a <b>negative quantity is normal</b> and a <b>falling price is a
        gain</b> — you sold it and buy it back cheaper. &ldquo;Sold at&rdquo; is per share;
        IBKR reports the cost basis multiplied by 100, which is what previously made a
        winning position read as −99%.
      </div>
    </div>
  );
}
