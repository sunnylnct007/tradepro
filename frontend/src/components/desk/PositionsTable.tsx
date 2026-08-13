/**
 * PositionsTable — the centerpiece of the /desk Portfolio view.
 *
 * Merges open positions from every broker:
 *   - T212 equity  (api.t212Positions — ticker, qty, currentPrice, unrealised)
 *   - IG FX/CFD     (api.igPositions   — ticker, qty, currentPrice, unrealised)
 *   - IBKR equity   (api.ibkrPositions — ticker, qty, currentPrice, unrealised)
 * Each row carries a small broker tag + a LIVE/DEMO badge so the merged view
 * stays unambiguous (IBKR LIVE = real money, read-only; T212/IG DEMO = paper).
 *
 * Grouping: positions are attributed to a STRATEGY when api.omsPositions()
 * (/api/oms/positions, carrying strategyId · symbol · broker · quantity) has a
 * row matching the holding by (broker, bare-symbol). Attributed holdings are
 * rendered in per-strategy sections (ichimoku_equity, ichimoku_fx_mr,
 * intraday_*, …) each with the same columns + a subtotal. Holdings with NO
 * attribution (e.g. the IBKR LIVE account's real positions) go under a clearly
 * labelled "Unattributed / Account holdings" section — never forced under a
 * strategy. When omsPositions is empty/unavailable we fall back to grouping by
 * broker so the table never breaks.
 *
 * Columns: Instrument · Company · Position (qty) · Last · Change% · Trend ·
 * P&L · P&L%. Clicking an instrument with an honest chart symbol opens the
 * Quote/Chart view for it (onOpenSymbol); FX/CFD rows with no Yahoo symbol stay
 * non-clickable rather than opening an empty chart.
 *
 * Trend sparkline: each row's ~30 most-recent daily closes come from
 * `api.candles(symbol, 1d, ~45d→today)` via `loadSparkline`, concurrency-limited
 * and memoised per symbol. Empty/failed candles degrade to `null` so the
 * sparkline renders "—" rather than a fabricated line.
 *
 * Mobile: each section's table lives in an overflow-x:auto container with a
 * min-width, so it scrolls horizontally inside its card.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { SortTh } from "../SortTh";
import { useSort } from "../../util/useSort";
import { fmtQty } from "../../util/numbers";
import { canonicalSymbol, chartSymbolFor } from "../../util/brokerSymbols";
import { Sparkline } from "./Sparkline";
import { loadSparkline, sparklineStats } from "./sparklineCache";
import { fmtMoney, fmtNum, fmtPct, signColour, accountMode, type AccountMode } from "./deskFormat";
import { ModeBadge } from "./ModeBadge";

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
  mode: AccountMode;            // LIVE (real money) vs DEMO (paper)
  strategyId: string | null;    // strategy attribution from omsPositions, else null
};

/** A rendered section: a strategy bucket or the unattributed/broker bucket. */
type Section = {
  key: string;
  title: string;
  rows: Row[];
};

const UNATTRIBUTED = "Unattributed / Account holdings";

export function PositionsTable({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [grouping, setGrouping] = useState<"strategy" | "broker">("broker");
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

      // Strategy attribution: (broker, bare-symbol) → strategyId from the OMS
      // positions ledger. Independent of the broker reads so a failure here
      // just means "no attribution" (fall back to broker grouping), never a
      // blank table.
      const attribution = new Map<string, string>();
      const oms = await api.omsPositions().catch((e) => {
        msgs.push(`OMS positions: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (oms?.positions) {
        for (const p of oms.positions) {
          if (!p.strategyId || p.quantity === 0) continue;
          attribution.set(attrKey(p.broker, p.symbol), p.strategyId);
        }
      }

      // T212 equity (demo). Reads are independent so one failing broker doesn't
      // blank the whole table.
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
            chartSymbol: p.yahooSymbol ?? chartSymbolFor(p.ticker, "T212"),
            series: null,
            mode: accountMode("T212", "demo"),
            strategyId: attribution.get(attrKey("T212", p.ticker)) ?? null,
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
            // IG FX/CFD epics resolve to the underlying Yahoo series (FX pair →
            // "<PAIR>=X", share CFD → underlying ticker) so the row is chartable.
            chartSymbol: chartSymbolFor(p.ticker, "IG"),
            series: null,
            mode: accountMode("IG", "demo"),
            strategyId: attribution.get(attrKey("IG", p.ticker)) ?? null,
          });
        }
      } else if (ig && !ig.enabled) {
        msgs.push("IG disabled");
      }

      // IBKR equity (read-only LIVE account). When the tradepro/ibkr secret is
      // absent the backend reports enabled:false and we add nothing.
      const ibkr = await api.ibkrPositions().catch((e) => {
        msgs.push(`IBKR positions: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (ibkr?.enabled && ibkr.positions && !ibkr.error) {
        for (const p of ibkr.positions) {
          const ticker = p.ticker ?? "—";
          out.push({
            broker: "IBKR",
            ticker,
            company: p.instrumentName ?? null,
            qty: fmtQty(p.quantity),
            last: p.currentPrice,
            changePct: p.unrealisedPct,
            pnl: p.unrealisedAbs,
            pnlPct: p.unrealisedPct,
            ccy: p.currency,
            // IBKR equity tickers (e.g. "EC", "BABA", "MRVL", "APLD") ARE the
            // Yahoo symbol for US listings → resolve so the row is chartable;
            // anything non-equity/unmappable stays null ("—", not fabricated).
            chartSymbol: chartSymbolFor(ticker, "IBKR"),
            series: null,
            mode: accountMode("IBKR", "live"), // IBKR is the real-money account
            strategyId: attribution.get(attrKey("IBKR", ticker)) ?? null,
          });
        }
      } else if (ibkr && !ibkr.enabled) {
        msgs.push("IBKR disabled");
      } else if (ibkr?.error) {
        msgs.push(`IBKR: ${ibkr.error}`);
      }

      if (!live) return;
      // Group by strategy only when at least one holding is attributed; else
      // fall back to broker grouping so the table never regresses.
      setGrouping(out.some((r) => r.strategyId) ? "strategy" : "broker");
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
  // session. Symbols with no honest chart symbol (IG FX) are skipped.
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

  if (loading && rows.length === 0) {
    return <Empty>Loading positions…</Empty>;
  }
  if (err) {
    return <Empty tone="down">Positions unavailable: {err}</Empty>;
  }

  const sections = buildSections(rows, grouping);

  return (
    <div>
      {sections.length === 0 && <Empty>No open positions.</Empty>}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {sections.map((sec) => (
          <PositionSection
            key={sec.key}
            section={sec}
            series={series}
            onOpenSymbol={onOpenSymbol}
          />
        ))}
      </div>
      {notes.length > 0 && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8 }}>
          {notes.join(" · ")}
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        {grouping === "strategy"
          ? "Grouped by strategy (from OMS attribution) · unattributed = account holdings (e.g. IBKR LIVE) · "
          : "Grouped by broker (no strategy attribution available) · "}
        native currency, never blended · click an instrument to open its chart (equity only) · Trend = ~30 daily closes; "—" when no series is available (not fabricated). {(() => {
          const st = sparklineStats();
          return st.failed > 0
            ? `Series ${st.loaded}/${st.requested} loaded — last failure: ${st.lastError}`
            : `Series ${st.loaded}/${st.requested} loaded.`;
        })()}
      </div>
    </div>
  );
}

/** OMS attribution key: broker + bare ticker, case-folded, so a holding lines
 * up with its OMS-ledger row regardless of broker symbol encoding. */
function attrKey(broker: string, symbol: string): string {
  // canonicalSymbol so a renamed holding (T212 LB_US_EQ) lines up with the
  // OMS's current-ticker row (BBWI) instead of showing as unattributed.
  return `${broker.toLowerCase()}:${canonicalSymbol(symbol)}`;
}

/** Bucket rows into ordered sections. Strategy mode: one section per strategyId
 * (sorted), with the unattributed bucket last. Broker mode: one per broker. */
function buildSections(rows: Row[], grouping: "strategy" | "broker"): Section[] {
  if (rows.length === 0) return [];
  const buckets = new Map<string, Row[]>();
  for (const r of rows) {
    const key = grouping === "strategy" ? (r.strategyId ?? UNATTRIBUTED) : r.broker;
    const cur = buckets.get(key) ?? [];
    cur.push(r);
    buckets.set(key, cur);
  }
  const keys = [...buckets.keys()].sort((a, b) => {
    // In strategy mode keep the unattributed bucket pinned to the bottom.
    if (grouping === "strategy") {
      if (a === UNATTRIBUTED) return 1;
      if (b === UNATTRIBUTED) return -1;
    }
    return a.localeCompare(b);
  });
  return keys.map((k) => ({ key: k, title: k, rows: buckets.get(k)! }));
}

/** One section: header (title + LIVE/DEMO badge when uniform) + its own
 * sortable table + a per-section P&L subtotal (only when a single currency, so
 * we never blend). */
function PositionSection({
  section, series, onOpenSymbol,
}: {
  section: Section;
  series: Record<string, number[] | null>;
  onOpenSymbol?: (symbol: string) => void;
}) {
  const { sorted, sortKey, dir, toggle } = useSort<Row>(
    section.rows,
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

  // Section badge: LIVE when ANY holding in the section is live (so an account
  // section containing real money is flagged); else DEMO.
  const sectionMode: AccountMode = section.rows.some((r) => r.mode === "LIVE") ? "LIVE" : "DEMO";

  // Subtotal P&L — only when every row shares one currency (never blend).
  const ccys = new Set(section.rows.map((r) => r.ccy ?? ""));
  const subtotalCcy = ccys.size === 1 ? (section.rows[0]?.ccy ?? null) : null;
  const subtotal = ccys.size === 1
    ? section.rows.reduce<number | null>((acc, r) => r.pnl == null ? acc : (acc ?? 0) + r.pnl, null)
    : null;

  return (
    <div>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 8, marginBottom: 6,
          fontSize: 12, fontWeight: 700, color: "var(--text)",
        }}
      >
        <span style={{ fontFamily: "monospace" }}>{section.title}</span>
        <ModeBadge mode={sectionMode} />
        <span style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 400 }}>
          {section.rows.length} holding{section.rows.length === 1 ? "" : "s"}
        </span>
        {subtotal != null && (
          <span style={{ marginLeft: "auto", fontSize: 11, color: signColour(subtotal), fontFamily: "monospace" }}>
            Σ P&L {fmtMoney(subtotal, subtotalCcy, true)}
          </span>
        )}
      </div>
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
            {sorted.map((r, i) => {
              const clickable = !!onOpenSymbol && !!r.chartSymbol;
              return (
                <tr
                  key={`${r.broker}:${r.ticker}:${i}`}
                  onClick={clickable ? () => onOpenSymbol!(r.chartSymbol!) : undefined}
                  title={clickable ? `Open ${r.chartSymbol} chart` : undefined}
                  style={{
                    borderBottom: "1px solid #141b2b",
                    cursor: clickable ? "pointer" : "default",
                  }}
                  className={clickable ? "desk-row-clickable" : undefined}
                >
                  <td style={TD}>
                    <span
                      style={{
                        fontWeight: 700,
                        color: clickable ? "var(--accent, #4f8cff)" : "var(--text)",
                        textDecoration: clickable ? "underline" : undefined,
                        textUnderlineOffset: 2,
                        textDecorationColor: clickable ? "rgba(79,140,255,0.35)" : undefined,
                      }}
                    >
                      {r.ticker}
                    </span>
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
              );
            })}
          </tbody>
        </table>
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
