/**
 * WatchlistView — the /desk "Watchlist" view (IBKR Watchlist nav).
 *
 * Deliberately light: a simple dense list of the curated UK watchlist
 * (api.ukWatchlist → symbol + label + kind). There is no live bid/ask quote
 * endpoint, so this is a names list, not a streaming quote board — we don't
 * fabricate prices. The header names the list + region so it's unambiguous.
 *
 * Mobile: the table scrolls horizontally inside its card.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { Watchlist } from "../../api/types";

export function WatchlistView() {
  const [wl, setWl] = useState<Watchlist | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    api.ukWatchlist()
      .then((r) => { if (live) setWl(r); })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  return (
    <div style={{
      border: "1px solid #1b2233", borderRadius: 8,
      background: "rgba(255,255,255,0.015)", padding: 12,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
          Watchlist{wl ? ` · ${wl.name}` : ""}
        </div>
        {wl && (
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
            {wl.items.length} symbols · {wl.region} · {wl.currency}
          </div>
        )}
      </div>

      {err ? (
        <Note tone="down">Watchlist unavailable: {err}</Note>
      ) : loading && !wl ? (
        <Note>Loading watchlist…</Note>
      ) : !wl || wl.items.length === 0 ? (
        <Note>No watchlist items.</Note>
      ) : (
        <div style={{ overflowX: "auto", maxWidth: "100%" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 420 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
                <th style={TH}>Symbol</th>
                <th style={TH}>Name</th>
                <th style={TH}>Kind</th>
              </tr>
            </thead>
            <tbody>
              {wl.items.map((it) => (
                <tr key={it.symbol} style={{ borderBottom: "1px solid #141b2b" }}>
                  <td style={{ ...TD, fontWeight: 700, color: "var(--text)" }}>{it.symbol}</td>
                  <td style={{ ...TD, color: "var(--text-muted)", fontFamily: undefined }}>{it.label || "—"}</td>
                  <td style={{ ...TD, color: "var(--text-muted)" }}>{it.kind || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8 }}>
        Names only — no live quote feed available (we don't fabricate prices).
      </div>
    </div>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{ fontSize: 12, padding: 12, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)" }}>
      {children}
    </div>
  );
}

const TH: React.CSSProperties = { textAlign: "left", padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TD: React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };
