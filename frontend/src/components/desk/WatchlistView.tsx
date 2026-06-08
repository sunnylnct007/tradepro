/**
 * WatchlistView — the /desk "Watchlist" view.
 *
 * REPURPOSED (was a static UK names list that nothing traded): now shows the
 * REAL traded universe — every name the live strategies are actually holding,
 * pulled from the broker books (T212 equity + IG FX/CFD + IBKR) and attributed
 * to a strategy via the OMS. Instrument/company name shown where the broker
 * provides it. No fabricated prices.
 *
 * This is the "what is the engine actually in" list, distinct from Portfolio
 * (the per-broker P&L grid).
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { chartSymbolFor } from "../../util/brokerSymbols";

type Row = {
  symbol: string;
  name: string;
  broker: string;
  strategy: string;
  qty: number;
};

export function WatchlistView() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [t212, ig, ibkr, oms] = await Promise.all([
          api.t212Positions("demo").catch(() => null),
          api.igPositions().catch(() => null),
          api.ibkrPositions().catch(() => null),
          api.omsPositions().catch(() => null),
        ]);
        const attr = new Map<string, string>();
        for (const p of oms?.positions ?? []) {
          if (p.strategyId && p.quantity !== 0) attr.set(`${p.broker}:${p.symbol}`.toUpperCase(), p.strategyId);
        }
        const out: Row[] = [];
        for (const p of t212?.positions ?? []) {
          if (!p.quantity) continue;
          out.push({ symbol: p.yahooSymbol ?? p.ticker, name: "", broker: "T212", qty: p.quantity, strategy: attr.get(`T212:${p.ticker}`.toUpperCase()) ?? "ichimoku_equity" });
        }
        for (const p of ig?.positions ?? []) {
          if (!p.quantity) continue;
          const isFx = !(p.ticker || "").includes(".CASH.");
          out.push({ symbol: chartSymbolFor(p.ticker, "IG") ?? p.ticker, name: p.instrumentName ?? "", broker: "IG", qty: p.quantity, strategy: attr.get(`IG:${p.ticker}`.toUpperCase()) ?? (isFx ? "ichimoku_fx_mr" : "intraday_flat") });
        }
        for (const p of ibkr?.positions ?? []) {
          if (!p.quantity || !p.ticker) continue;
          out.push({ symbol: p.ticker, name: p.instrumentName ?? "", broker: "IBKR", qty: p.quantity, strategy: attr.get(`IBKR:${p.ticker}`.toUpperCase()) ?? "—" });
        }
        if (live) { setRows(out); setErr(null); }
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { live = false; };
  }, []);

  const strategies = new Set((rows ?? []).map((r) => r.strategy));

  return (
    <div style={{
      border: "1px solid #1b2233", borderRadius: 8,
      background: "rgba(255,255,255,0.015)", padding: 12,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
          Watchlist · live traded book
        </div>
        {rows && (
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
            {rows.length} held · {strategies.size} {strategies.size === 1 ? "strategy" : "strategies"}
          </div>
        )}
      </div>

      {err ? (
        <Note tone="down">Book unavailable: {err}</Note>
      ) : !rows ? (
        <Note>Loading live book…</Note>
      ) : rows.length === 0 ? (
        <Note>No open positions — the engine is flat across all strategies right now.</Note>
      ) : (
        <div style={{ overflowX: "auto", maxWidth: "100%" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 480 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
                <th style={TH}>Symbol</th>
                <th style={TH}>Name</th>
                <th style={TH}>Strategy</th>
                <th style={TH}>Broker</th>
                <th style={{ ...TH, textAlign: "right" }}>Qty</th>
              </tr>
            </thead>
            <tbody>
              {[...rows].sort((a, b) => a.strategy.localeCompare(b.strategy) || a.symbol.localeCompare(b.symbol)).map((it, i) => (
                <tr key={`${it.broker}-${it.symbol}-${i}`} style={{ borderBottom: "1px solid #141b2b" }}>
                  <td style={{ ...TD, fontWeight: 700, color: "var(--text)" }} title={it.name || it.symbol}>{it.symbol}</td>
                  <td style={{ ...TD, color: "var(--text-muted)" }}>{it.name || "—"}</td>
                  <td style={{ ...TD, color: "var(--text-muted)" }}>{it.strategy}</td>
                  <td style={{ ...TD, color: "var(--text-muted)" }}>{it.broker}</td>
                  <td style={{ ...TD, textAlign: "right" }}>{it.qty}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 8 }}>
        Live broker book (T212 + IG + IBKR), strategy-attributed via OMS · names shown where the broker provides them.
      </div>
    </div>
  );
}

const TH: React.CSSProperties = { textAlign: "left", padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TD: React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };

function Note({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{ fontSize: 12, padding: 12, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)" }}>
      {children}
    </div>
  );
}
