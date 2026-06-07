/**
 * ScreenersView — the /desk "Screeners" view (IBKR Screeners nav).
 *
 * A dense, dark, sortable IBKR-style table of the latest per-symbol compare
 * results for a chosen universe (api.compareLatest → payload.rows). All data
 * is real; nothing is fabricated. Universe is chosen via PILLS (house rule:
 * no dropdowns on primary surfaces), sourced from api.compareUniverses() and
 * defaulting to the first available (the same default Compare.tsx uses).
 *
 * Columns: Symbol · Action (BUY/SELL/HOLD) · Bucket (BUY/WAIT/AVOID) · Last ·
 * RSI · Momentum (3m) · Rank · News (count) · Catalysts (count). Sorting uses
 * the shared useSort + SortTh pattern so it behaves like every other table.
 *
 * Header shows generatedAtUtc + rowCount so data freshness is always visible.
 * Mobile: table scrolls horizontally inside its card (overflow-x + min-width).
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CompareLatestResponse, CompareRow, CompareUniverseSummary } from "../../api/types";
import { SortTh } from "../SortTh";
import { useSort } from "../../util/useSort";
import { fmtMoney, fmtNum, fmtPct } from "./deskFormat";

const ACTION_COLOUR: Record<string, string> = {
  BUY: "#1fc16b",
  SELL: "#ef4444",
  HOLD: "#8693b5",
};

const BUCKET_COLOUR: Record<string, string> = {
  BUY: "#1fc16b",
  WAIT: "#e0b341",
  AVOID: "#ef4444",
};

export function ScreenersView() {
  const [universes, setUniverses] = useState<CompareUniverseSummary[]>([]);
  const [universe, setUniverse] = useState<string>("");
  const [data, setData] = useState<CompareLatestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Universe list — same source + default (first) as Compare.tsx.
  useEffect(() => {
    api.compareUniverses()
      .then((r) => {
        setUniverses(r.universes);
        if (r.universes.length > 0) setUniverse((u) => u || r.universes[0].universe);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!universe) return;
    let live = true;
    setLoading(true);
    setErr(null);
    api.compareLatest(universe)
      .then((r) => { if (live) setData(r); })
      .catch((e) => { if (live) { setData(null); setErr(e instanceof Error ? e.message : String(e)); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [universe]);

  const rows: CompareRow[] = data?.payload.rows ?? [];

  const { sorted, sortKey, dir, toggle } = useSort<CompareRow>(
    rows,
    {
      symbol: (r) => r.symbol,
      action: (r) => r.current_action,
      bucket: (r) => r.bucket ?? null,
      last: (r) => r.market_state?.last_price ?? null,
      rsi: (r) => r.market_state?.rsi_14 ?? null,
      momentum: (r) => r.market_state?.momentum_3m_pct ?? null,
      rank: (r) => r.rank,
      news: (r) => r.news?.length ?? 0,
      catalysts: (r) => r.catalysts?.length ?? 0,
    },
    { key: "rank", dir: "asc" },
  );

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)" }}>Screeners</div>
        {data && (
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
            {data.rowCount} rows · generated {fmtUtc(data.generatedAtUtc)}
          </div>
        )}
      </div>

      {/* Universe pills (no dropdown — house rule). */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
        {universes.length === 0 && !err && (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading universes…</span>
        )}
        {universes.map((u) => {
          const active = u.universe === universe;
          return (
            <button
              key={u.universe}
              onClick={() => setUniverse(u.universe)}
              title={`${u.rowCount} symbols`}
              style={{
                background: active ? "var(--accent, #4f8cff)" : "transparent",
                color: active ? "#0a0e17" : "var(--text-muted)",
                border: `1px solid ${active ? "var(--accent, #4f8cff)" : "#1b2233"}`,
                borderRadius: 4, padding: "3px 10px", fontSize: 11, fontWeight: 600, cursor: "pointer",
              }}
            >
              {u.universe}
            </button>
          );
        })}
      </div>

      {err ? (
        <Note tone="down">Screener unavailable: {err}</Note>
      ) : loading && rows.length === 0 ? (
        <Note>Loading screener…</Note>
      ) : rows.length === 0 ? (
        <Note>No rows for this universe.</Note>
      ) : (
        <div style={{ overflowX: "auto", maxWidth: "100%" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 760 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
                <SortTh label="Symbol" col="symbol" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
                <SortTh label="Action" col="action" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
                <SortTh label="Bucket" col="bucket" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
                <SortTh label="Last" col="last" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="RSI" col="rsi" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="Mom 3m" col="momentum" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="Rank" col="rank" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="News" col="news" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="Catalysts" col="catalysts" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => {
                const ms = r.market_state;
                return (
                  <tr key={`${r.symbol}:${i}`} style={{ borderBottom: "1px solid #141b2b" }}>
                    <td style={{ ...TD, fontWeight: 700, color: "var(--text)" }}>{r.symbol}</td>
                    <td style={TD}>
                      <Pill text={r.current_action} colour={ACTION_COLOUR[r.current_action] ?? "#8693b5"} />
                    </td>
                    <td style={TD}>
                      {r.bucket ? <Pill text={r.bucket} colour={BUCKET_COLOUR[r.bucket] ?? "#8693b5"} /> : <Dim>—</Dim>}
                    </td>
                    <td style={{ ...TD_R, color: "var(--text)" }}>{fmtMoney(ms?.last_price ?? null, r.currency ?? null)}</td>
                    <td style={TD_R}>{ms?.rsi_14 != null ? fmtNum(ms.rsi_14, 1) : <Dim>—</Dim>}</td>
                    <td style={{ ...TD_R, color: signCol(ms?.momentum_3m_pct) }}>
                      {ms?.momentum_3m_pct != null ? fmtPct(ms.momentum_3m_pct) : <Dim>—</Dim>}
                    </td>
                    <td style={TD_R}>{fmtNum(r.rank, 0)}</td>
                    <td style={TD_R}>{r.news?.length ?? 0}</td>
                    <td style={TD_R}>{r.catalysts?.length ?? 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function signCol(n: number | null | undefined): string {
  if (n == null) return "var(--text-muted)";
  return n >= 0 ? "#1fc16b" : "#ef4444";
}

function Pill({ text, colour }: { text: string; colour: string }) {
  return (
    <span
      style={{
        fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", color: colour,
        border: `1px solid ${colour}`, borderRadius: 4, padding: "1px 6px",
      }}
    >
      {text}
    </span>
  );
}

function Dim({ children }: { children: React.ReactNode }) {
  return <span style={{ color: "var(--text-muted)" }}>{children}</span>;
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        border: "1px solid #1b2233", borderRadius: 8,
        background: "rgba(255,255,255,0.015)", padding: 12,
      }}
    >
      {children}
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

function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const TH: React.CSSProperties = { textAlign: "left", padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD: React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };
