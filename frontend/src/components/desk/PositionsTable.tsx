/**
 * PositionsTable — the centerpiece of the /desk Portfolio view.
 *
 * One dense, dark, sortable table merging open positions from every broker:
 *   - T212 equity  (api.t212Positions — ticker, qty, currentPrice, unrealised)
 *   - IG FX/CFD     (api.igPositions   — ticker, qty, currentPrice, unrealised)
 *   - IBKR equity   (api.ibkrPositions — ticker, qty, currentPrice, unrealised)
 * Each row carries a small broker tag so the merged view stays unambiguous.
 *
 * Columns: Instrument · Company · Position (qty) · Last · Change% · Trend ·
 * P&L · P&L%. Last/Change/P&L coloured green/red by sign.
 *
 * Trend sparkline: each row's ~30 most-recent daily closes come from
 * `api.candles(symbol, 1d, ~45d→today)` via `loadSparkline`, which
 * concurrency-limits the fetches (a fixed-size pool — never dozens of parallel
 * requests) and memoises per symbol for the session. Empty/failed candles
 * degrade to `null`, so <Sparkline/> renders a subtle "—" rather than a
 * fabricated line (user rule: never invent a series).
 *
 * Mobile: the table lives in an overflow-x:auto container with a min-width, so
 * it scrolls horizontally inside its card instead of pushing the page wide.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { SortTh } from "../SortTh";
import { useSort } from "../../util/useSort";
import { fmtQty } from "../../util/numbers";
import { Sparkline } from "./Sparkline";
import { loadSparkline } from "./sparklineCache";
import { fmtMoney, fmtNum, fmtPct, signColour } from "./deskFormat";

type Row = {
  broker: string;        // "T212" | "IG" | "IBKR"
  ticker: string;
  company: string | null;
  qty: number;
  last: number | null;
  changePct: number | null;     // unrealised % stands in for position change
  pnl: number | null;
  pnlPct: number | null;
  ccy: string | null;
  chartSymbol: string | null;   // Yahoo symbol to fetch candles for (null = no honest source, e.g. IG FX)
  series: number[] | null;      // ~30 daily closes; null until loaded / when unavailable
};

export function PositionsTable() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  // Per-symbol close series for the Trend sparkline, loaded lazily +
  // concurrency-limited after positions arrive (see sparklineCache).
  const [series, setSeries] = useState<Record<string, number[] | null>>({});

  useEffect(() => {
    let live = true;
    const load = async () => {
      const msgs: string[] = [];
      const out: Row[] = [];
      // T212 equity (demo). Both reads are independent so one failing
      // broker doesn't blank the whole table.
      const t212 = await api.t212Positions("demo").catch((e) => {
        msgs.push(`T212 positions: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (t212?.enabled && t212.positions) {
        for (const p of t212.positions) {
          out.push({
            broker: "T212",
            ticker: p.ticker,
            company: p.yahooSymbol ?? null,
            qty: fmtQty(p.quantity),
            last: p.currentPrice,
            changePct: p.unrealisedPct,
            pnl: p.unrealisedAbs,
            pnlPct: p.unrealisedPct,
            ccy: p.currency,
            chartSymbol: p.yahooSymbol ?? null,
            series: null,
          });
        }
      } else if (t212 && !t212.enabled) {
        msgs.push("T212 disabled");
      }

      const ig = await api.igPositions().catch((e) => {
        msgs.push(`IG positions: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (ig?.enabled && ig.positions) {
        for (const p of ig.positions) {
          out.push({
            broker: "IG",
            ticker: p.ticker,
            company: p.instrumentName ?? null,
            qty: fmtQty(p.quantity),
            last: p.currentPrice,
            changePct: p.unrealisedPct,
            pnl: p.unrealisedAbs,
            pnlPct: p.unrealisedPct,
            ccy: null, // IG positions endpoint doesn't return per-position ccy
            // IG FX/CFD epics have no honest Yahoo candle symbol — leave the
            // sparkline as "—" rather than guessing a mapping.
            chartSymbol: null,
            series: null,
          });
        }
      } else if (ig && !ig.enabled) {
        msgs.push("IG disabled");
      }

      // IBKR equity (read-only). Same uniform shape as T212/IG. When the
      // tradepro/ibkr secret is absent the backend reports enabled:false and
      // we add nothing (graceful degrade) — no fabricated rows.
      const ibkr = await api.ibkrPositions().catch((e) => {
        msgs.push(`IBKR positions: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (ibkr?.enabled && ibkr.positions && !ibkr.error) {
        for (const p of ibkr.positions) {
          out.push({
            broker: "IBKR",
            ticker: p.ticker ?? "—",
            company: p.instrumentName ?? null,
            qty: fmtQty(p.quantity),
            last: p.currentPrice,
            changePct: p.unrealisedPct,
            pnl: p.unrealisedAbs,
            pnlPct: p.unrealisedPct,
            ccy: p.currency,
            // IBKR equity tickers may not map cleanly to a Yahoo candle
            // symbol — leave the sparkline as "—" rather than guess.
            chartSymbol: null,
            series: null,
          });
        }
      } else if (ibkr && !ibkr.enabled) {
        msgs.push("IBKR disabled");
      } else if (ibkr?.error) {
        msgs.push(`IBKR: ${ibkr.error}`);
      }

      if (!live) return;
      setRows(out);
      setNotes(msgs);
      setErr(null);
      setLoading(false);
    };
    load().catch((e) => {
      if (live) { setErr(e instanceof Error ? e.message : String(e)); setLoading(false); }
    });
    const t = setInterval(() => { void load(); }, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  // Load each position's recent-close series for the Trend sparkline. The
  // loader pools requests (max ~5 in flight) and caches per symbol for the
  // session, so the 60s refresh tick re-renders without re-hitting the
  // network. Symbols with no honest chart symbol (IG FX) are skipped.
  useEffect(() => {
    let live = true;
    const symbols = [...new Set(rows.map((r) => r.chartSymbol).filter((s): s is string => !!s))];
    for (const sym of symbols) {
      if (sym in series) continue; // already loaded / loading this session
      void loadSparkline(sym).then((data) => {
        if (live) setSeries((prev) => ({ ...prev, [sym]: data }));
      });
    }
    return () => { live = false; };
  }, [rows, series]);

  const { sorted, sortKey, dir, toggle } = useSort<Row>(
    rows,
    {
      ticker: (r) => r.ticker,
      company: (r) => r.company,
      qty: (r) => r.qty,
      last: (r) => r.last,
      change: (r) => r.changePct,
      pnl: (r) => r.pnl,
      pnlPct: (r) => r.pnlPct,
      broker: (r) => r.broker,
    },
    { key: "pnl", dir: "desc" },
  );

  const hasData = sorted.length > 0;

  if (loading && rows.length === 0) {
    return <Empty>Loading positions…</Empty>;
  }
  if (err) {
    return <Empty tone="down">Positions unavailable: {err}</Empty>;
  }

  return (
    <div>
      <div style={{ overflowX: "auto", maxWidth: "100%" }}>
        <table
          style={{
            width: "100%", borderCollapse: "collapse", fontSize: 12,
            minWidth: 720, // forces horizontal scroll on phone rather than squashing
          }}
        >
          <thead>
            <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
              <SortTh label="Financial Instrument" col="ticker" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
              <SortTh label="Company Name" col="company" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
              <SortTh label="Position" col="qty" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="Last" col="last" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="Change%" col="change" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <th style={TH_C}>Trend</th>
              <SortTh label="P&L" col="pnl" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              <SortTh label="P&L%" col="pnlPct" sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
            </tr>
          </thead>
          <tbody>
            {!hasData && (
              <tr>
                <td colSpan={8} style={{ padding: 14, color: "var(--text-muted)", textAlign: "center" }}>
                  No open positions.
                </td>
              </tr>
            )}
            {sorted.map((r, i) => (
              <tr key={`${r.broker}:${r.ticker}:${i}`} style={{ borderBottom: "1px solid #141b2b" }}>
                <td style={TD}>
                  <span style={{ fontWeight: 700, color: "var(--text)" }}>{r.ticker}</span>
                  <BrokerTag broker={r.broker} />
                </td>
                <td style={{ ...TD, color: "var(--text-muted)" }}>{r.company ?? "—"}</td>
                <td style={TD_R}>{fmtNum(r.qty)}</td>
                <td style={{ ...TD_R, color: "var(--text)" }}>{fmtMoney(r.last, r.ccy)}</td>
                <td style={{ ...TD_R, color: signColour(r.changePct) }}>{fmtPct(r.changePct)}</td>
                <td style={{ ...TD, textAlign: "center" }}>
                  <span style={{ display: "inline-flex", justifyContent: "center", width: "100%" }}>
                    <Sparkline data={r.chartSymbol ? (series[r.chartSymbol] ?? r.series) : r.series} />
                  </span>
                </td>
                <td style={{ ...TD_R, color: signColour(r.pnl) }}>{fmtMoney(r.pnl, r.ccy, true)}</td>
                <td style={{ ...TD_R, color: signColour(r.pnlPct) }}>{fmtPct(r.pnlPct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {notes.length > 0 && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8 }}>
          {notes.join(" · ")}
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        Per broker · native currency, never blended · Trend = ~30 daily closes (green up / red down); "—" when no series is available (not fabricated).
      </div>
    </div>
  );
}

function BrokerTag({ broker }: { broker: string }) {
  const colour = broker === "IG" ? "#4f8cff" : broker === "IBKR" ? "#d4793b" : "#1fc16b";
  return (
    <span
      title={broker}
      style={{
        marginLeft: 6, fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
        color: colour, border: `1px solid ${colour}`, borderRadius: 4,
        padding: "0 4px", verticalAlign: "middle",
      }}
    >
      {broker}
    </span>
  );
}

function Empty({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div style={{ fontSize: 12, padding: 14, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)" }}>
      {children}
    </div>
  );
}

const TH: React.CSSProperties = { textAlign: "left", padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TH_C: React.CSSProperties = { ...TH, textAlign: "center" };
const TD: React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };
