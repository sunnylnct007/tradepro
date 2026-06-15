/**
 * PositionsByStrategy — positions table grouped by strategy desk.
 *
 * One job: merge broker positions (T212/IG/IBKR) with OMS strategy attribution,
 * render per-strategy sections with P&L subtotals; unattributed IBKR LIVE
 * holdings land under "IBKR LIVE · account holdings (unattributed)".
 *
 * Attribution join: (broker_lower : bare_ticker) from omsPositions rows matches
 * each broker position. IBKR real holdings with no attribution are sectioned as
 * "IBKR LIVE · account holdings (unattributed)".
 *
 * Sorting: per-section sortable table (useSort). Mobile: table scrolls
 * horizontally inside its overflow:auto container.
 *
 * The `onSelectSymbol` prop wires master-detail: clicking an equity row with a
 * chart symbol calls onSelectSymbol(chartSymbol), never navigating away.
 *
 * Also exports the PositionRow type (re-used by SymbolPositionCard).
 *
 * Collapsible sections: each section header is a toggle button. Collapsed state
 * is persisted in localStorage keyed as "desk-section-collapsed:<sectionKey>"
 * so it survives page refresh. Default: expanded.
 */
import { useEffect, useState, useCallback } from "react";
import { api } from "../../api/client";
import { SortTh } from "../SortTh";
import { useSort } from "../../util/useSort";
import { fmtQty } from "../../util/numbers";
import { bareSymbol, chartSymbolFor } from "../../util/brokerSymbols";
import { Sparkline } from "./Sparkline";
import { loadSparkline } from "./sparklineCache";
import { fmtMoney, fmtNum, fmtPct, signColour, accountMode, type AccountMode } from "./deskFormat";
import { ModeBadge } from "./ModeBadge";
import type { PositionRow } from "./SymbolPositionCard";

// Re-export so callers can import the type from here directly.
export type { PositionRow };

const IBKR_UNATTR = "IBKR LIVE · account holdings (unattributed)";
const UNATTRIBUTED = "Unattributed / Account holdings";
const LS_PREFIX    = "desk-section-collapsed:";

type Section = { key: string; title: string; rows: InternalRow[] };

type InternalRow = PositionRow & {
  company: string | null;
  changePct: number | null;
  strategyId: string | null;
  series: number[] | null;
  avgPrice: number | null;
};

/** Broker family from either an OMS label ("T212_DEMO") or a position-row
 *  family ("T212"/"IG"/"IBKR"). */
function brokerFamily(label: string): string {
  const u = (label || "").toUpperCase();
  if (u.startsWith("T212")) return "t212";
  if (u.startsWith("IG"))   return "ig";
  if (u.startsWith("IBKR")) return "ibkr";
  return u.toLowerCase().split("_")[0];
}

/** DEMO unless the label explicitly says LIVE (PAPER counts as DEMO). Mode-aware
 *  keys stop the personal T212 LIVE book (which has no OMS rows) from stealing
 *  the algo T212 DEMO attribution — the bug behind the earlier family revert. */
function brokerModeOf(label: string): AccountMode {
  return /LIVE/i.test(label || "") ? "LIVE" : "DEMO";
}

// Attribution join key: family + mode + bare ticker. The OMS labels positions
// T212_DEMO / IG_DEMO / IBKR_PAPER while the broker position rows are labelled
// T212 / IG / IBKR; normalising both to family+mode lets them match across the
// demo/live split. bareSymbol unifies the symbol formats (AAPL_US_EQ → AAPL,
// CS.D.GBPUSD.MINI.IP → GBPUSD) — verified identical on both sources.
function attrKey(family: string, mode: AccountMode, symbol: string): string {
  return `${family}:${mode}:${bareSymbol(symbol)}`;
}

export function PositionsByStrategy({
  onSelectSymbol,
  onRowsChange,
}: {
  onSelectSymbol?: (sym: string) => void;
  /** Called whenever the rows list changes, so the parent can pass them to SymbolPositionCard. */
  onRowsChange?: (rows: PositionRow[]) => void;
}) {
  const [rows,     setRows]    = useState<InternalRow[]>([]);
  const [grouping, setGrouping] = useState<"strategy" | "broker">("broker");
  const [loading,  setLoading] = useState(true);
  const [err,      setErr]     = useState<string | null>(null);
  const [notes,    setNotes]   = useState<string[]>([]);
  const [series,   setSeries]  = useState<Record<string, number[] | null>>({});

  useEffect(() => {
    let live = true;
    const load = async () => {
      const msgs: string[] = [];
      const out: InternalRow[] = [];

      // Configured desks (strategy_broker_map) — attribute ONLY to these so the
      // intraday-engine's per-symbol shadow rows (intraday-<algo>-<SYM>) can't
      // hijack a configured desk's symbol on the shared T212_DEMO account.
      // Config-driven: no hardcoded strategy names.
      const cfg = await api.strategyBrokerMap().catch(() => null);
      const configured = new Set((cfg?.mappings ?? []).map((m) => m.strategy_id));

      // Strategy attribution from OMS positions (family+mode keyed).
      const attribution = new Map<string, string>();
      const oms = await api.omsPositions().catch((e) => {
        msgs.push(`OMS: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (oms?.positions) {
        for (const p of oms.positions) {
          if (!p.strategyId || p.quantity === 0) continue;
          if (configured.size > 0 && !configured.has(p.strategyId)) continue;
          attribution.set(
            attrKey(brokerFamily(p.broker), brokerModeOf(p.broker), p.symbol),
            p.strategyId);
        }
      }

      // T212
      const t212 = await api.t212Positions("demo").catch((e) => {
        msgs.push(`T212: ${e instanceof Error ? e.message : e}`);
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
            mode: accountMode("T212", "demo") as AccountMode,
            strategyId: attribution.get(attrKey("t212", "DEMO", p.ticker)) ?? null,
            series: null,
            avgPrice: p.averagePricePaid ?? null,
            entryDate: p.createdAt ?? null,
          });
        }
      } else if (t212 && !t212.enabled) msgs.push("T212 disabled");

      // T212 LIVE — REMOVED. The personal live account is no longer surfaced
      // in the cockpit: the live integration is disabled (Trading212:Mode=
      // disabled) because it was unreachable from the app. Only the algo
      // DEMO book renders. Re-enable by restoring the live key + this block.

      // IG
      const ig = await api.igPositions().catch((e) => {
        msgs.push(`IG: ${e instanceof Error ? e.message : e}`);
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
            ccy: null,
            // IG FX/CFD epics resolve to the underlying Yahoo series (FX pair →
            // "<PAIR>=X", share CFD → underlying ticker) so the row is chartable.
            chartSymbol: chartSymbolFor(p.ticker, "IG"),
            mode: accountMode("IG", "demo") as AccountMode,
            strategyId: attribution.get(attrKey("ig", "DEMO", p.ticker)) ?? null,
            series: null,
            avgPrice: p.averagePricePaid ?? null,
          });
        }
      } else if (ig && !ig.enabled) msgs.push("IG disabled");

      // IBKR (live)
      const ibkr = await api.ibkrPositions().catch((e) => {
        msgs.push(`IBKR: ${e instanceof Error ? e.message : e}`);
        return null;
      });
      if (ibkr?.enabled && ibkr.positions && !ibkr.error) {
        for (const p of ibkr.positions) {
          const ticker = p.ticker ?? "—";
          const stratId = attribution.get(attrKey("ibkr", "LIVE", ticker)) ?? null;
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
            chartSymbol: chartSymbolFor(ticker, "IBKR"),
            mode: accountMode("IBKR", "live") as AccountMode,
            strategyId: stratId,
            series: null,
            avgPrice: p.averagePricePaid ?? null,
          });
        }
      } else if (ibkr && !ibkr.enabled) msgs.push("IBKR disabled");
      else if (ibkr?.error)             msgs.push(`IBKR: ${ibkr.error}`);

      // IBKR DEMO (paper clone, DUP656969): the live IBKRClient only sees the
      // personal IBKR_LIVE account, so the clone's golden source is the account
      // state its daemon pushes (broker_account_state) — real mark + unrealised
      // P&L per position. Falls back to OMS qty-only if no push has landed yet.
      const acctState = await api.accountState().catch(() => null);
      const paper = acctState?.accounts.find((a) => a.broker === "IBKR_PAPER");
      // The clone's desk id, from its OMS rows (config desk ichimoku_equity_ibkr).
      const ibkrDemoStrat =
        (oms?.positions ?? []).find((p) => (p.broker || "") === "IBKR_PAPER")?.strategyId
        ?? "ichimoku_equity_ibkr";
      if (paper?.positions?.length) {
        for (const p of paper.positions) {
          if (!p.qty) continue;
          const pnlPct = p.avgCost != null && p.avgCost !== 0 && p.mark != null
            ? ((p.mark - p.avgCost) / p.avgCost) * 100 : null;
          out.push({
            broker: "IBKR",
            ticker: p.symbol,
            company: null,
            qty: fmtQty(p.qty),
            last: p.mark,
            changePct: null,
            pnl: p.unrealisedPnl,
            pnlPct,
            ccy: p.currency ?? "USD",
            chartSymbol: chartSymbolFor(p.symbol, "IBKR"),
            mode: accountMode("IBKR", "demo") as AccountMode,
            strategyId: ibkrDemoStrat,
            series: null,
            avgPrice: p.avgCost,
          });
        }
      } else {
        // No account-state push yet — OMS qty-only fallback (no live mark/P&L).
        for (const p of oms?.positions ?? []) {
          if ((p.broker || "") !== "IBKR_PAPER" || !p.quantity) continue;
          out.push({
            broker: "IBKR",
            ticker: p.symbol,
            company: null,
            qty: fmtQty(p.quantity),
            last: null,
            changePct: null,
            pnl: null,
            pnlPct: null,
            ccy: "USD",
            chartSymbol: chartSymbolFor(p.symbol, "IBKR"),
            mode: accountMode("IBKR", "demo") as AccountMode,
            strategyId: p.strategyId ?? "ichimoku_equity_ibkr",
            series: null,
            avgPrice: (p as { avgPrice?: number }).avgPrice ?? null,
          });
        }
      }

      if (!live) return;
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

  // Notify parent of row changes so it can pass them to the right-rail.
  useEffect(() => {
    onRowsChange?.(rows);
  }, [rows, onRowsChange]);

  // Load sparklines concurrently, cached per session.
  useEffect(() => {
    let live = true;
    const syms = [...new Set(rows.map((r) => r.chartSymbol).filter((s): s is string => !!s))];
    for (const sym of syms) {
      if (sym in series) continue;
      void loadSparkline(sym).then((data) => {
        if (live) setSeries((prev) => ({ ...prev, [sym]: data }));
      });
    }
    return () => { live = false; };
  }, [rows, series]);

  // Generation counter to force PositionSection re-mount (re-reads localStorage) after toggleAll.
  const [collapseGen, setCollapseGen] = useState(0);

  // Collapse-all / expand-all: derives sections inline so it can be declared before early returns.
  const toggleAll = useCallback(() => {
    const secs = buildSections(rows, grouping);
    const allCollapsed = secs.every(
      (sec) => localStorage.getItem(`${LS_PREFIX}${sec.key}`) === "1",
    );
    for (const sec of secs) {
      if (allCollapsed) localStorage.removeItem(`${LS_PREFIX}${sec.key}`);
      else localStorage.setItem(`${LS_PREFIX}${sec.key}`, "1");
    }
    setCollapseGen((g) => g + 1);
  }, [rows, grouping]);

  if (loading && rows.length === 0) return <Empty>Loading positions…</Empty>;
  if (err)                           return <Empty tone="down">Positions unavailable: {err}</Empty>;

  const sections = buildSections(rows, grouping);

  return (
    <div>
      {sections.length === 0 && <Empty>No open positions.</Empty>}
      {sections.length > 1 && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
          <button
            onClick={toggleAll}
            style={{
              background: "none", border: "none", cursor: "pointer",
              fontSize: 10, color: "var(--text-muted)", padding: "2px 4px",
              textDecoration: "underline", textUnderlineOffset: 2,
            }}
          >
            collapse / expand all
          </button>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {sections.map((sec) => (
          <PositionSection
            key={`${sec.key}:${collapseGen}`}
            section={sec}
            series={series}
            onSelectSymbol={onSelectSymbol}
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
          ? "Grouped by strategy · unattributed = IBKR LIVE account holdings · "
          : "Grouped by broker (no OMS attribution) · "}
        native currency · click a row to open symbol detail (equity only) ·
        Trend = ~30 daily closes (not fabricated)
      </div>
    </div>
  );
}

function buildSections(rows: InternalRow[], grouping: "strategy" | "broker"): Section[] {
  if (!rows.length) return [];
  const buckets = new Map<string, InternalRow[]>();
  for (const r of rows) {
    let key: string;
    if (grouping === "strategy") {
      // IBKR LIVE unattributed gets its own section label
      if (!r.strategyId && r.broker === "IBKR" && r.mode === "LIVE") key = IBKR_UNATTR;
      else key = r.strategyId ?? UNATTRIBUTED;
    } else {
      key = r.broker;
    }
    const cur = buckets.get(key) ?? [];
    cur.push(r);
    buckets.set(key, cur);
  }
  const keys = [...buckets.keys()].sort((a, b) => {
    if (grouping === "strategy") {
      if (a === IBKR_UNATTR || a === UNATTRIBUTED) return 1;
      if (b === IBKR_UNATTR || b === UNATTRIBUTED) return -1;
    }
    return a.localeCompare(b);
  });
  return keys.map((k) => ({ key: k, title: k, rows: buckets.get(k)! }));
}

function PositionSection({
  section,
  series,
  onSelectSymbol,
}: {
  section: Section;
  series: Record<string, number[] | null>;
  onSelectSymbol?: (sym: string) => void;
}) {
  // Collapsed state: initialise from localStorage (default = expanded).
  const lsKey = `${LS_PREFIX}${section.key}`;
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(lsKey) === "1",
  );

  const handleToggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      if (next) localStorage.setItem(lsKey, "1");
      else localStorage.removeItem(lsKey);
      return next;
    });
  }, [lsKey]);

  const { sorted, sortKey, dir, toggle } = useSort<InternalRow>(
    section.rows,
    {
      ticker:   (r) => r.ticker,
      company:  (r) => r.company,
      qty:      (r) => r.qty,
      last:     (r) => r.last,
      change:   (r) => r.changePct,
      pnl:      (r) => r.pnl,
      pnlPct:   (r) => r.pnlPct,
      broker:   (r) => r.broker,
    },
    { key: "pnl", dir: "desc" },
  );

  const sectionMode: AccountMode = section.rows.some((r) => r.mode === "LIVE") ? "LIVE" : "DEMO";
  const ccys     = new Set(section.rows.map((r) => r.ccy ?? ""));
  const oneCcy   = ccys.size === 1 ? (section.rows[0]?.ccy ?? null) : null;
  const subtotal = oneCcy
    ? section.rows.reduce<number | null>((acc, r) => r.pnl == null ? acc : (acc ?? 0) + r.pnl, null)
    : null;

  return (
    <div>
      {/* Clickable section header — acts as a toggle button */}
      <button
        onClick={handleToggle}
        aria-expanded={!collapsed}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          width: "100%", background: "none", border: "none",
          padding: "4px 0", marginBottom: collapsed ? 0 : 6,
          cursor: "pointer", textAlign: "left",
          borderRadius: 4,
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.8"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
      >
        {/* Chevron indicator */}
        <span
          aria-hidden="true"
          style={{
            fontSize: 10, color: "var(--text-muted)", lineHeight: 1,
            transition: "transform 0.15s",
            display: "inline-block",
            transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
            userSelect: "none",
          }}
        >
          ▾
        </span>
        <span style={{ fontFamily: "monospace", color: "var(--text)", fontSize: 12, fontWeight: 700 }}>
          {section.title}
        </span>
        <ModeBadge mode={sectionMode} />
        <span style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 400 }}>
          {section.rows.length} holding{section.rows.length === 1 ? "" : "s"}
        </span>
        {subtotal != null && (
          <span style={{ marginLeft: "auto", fontSize: 11, color: signColour(subtotal), fontFamily: "monospace" }}>
            Σ P&amp;L {fmtMoney(subtotal, oneCcy, true)}
          </span>
        )}
      </button>

      {!collapsed && (
        <div style={{ overflowX: "auto", maxWidth: "100%" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 720 }}>
            <thead>
              <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
                <SortTh label="Instrument"  col="ticker"  sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
                <SortTh label="Company"     col="company" sortKey={sortKey} dir={dir} onSort={toggle} style={TH} />
                <SortTh label="Position"    col="qty"     sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="Last"        col="last"    sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="Change%"     col="change"  sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <th style={TH_C}>Trend</th>
                <SortTh label="P&amp;L"     col="pnl"     sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
                <SortTh label="P&amp;L%"    col="pnlPct"  sortKey={sortKey} dir={dir} onSort={toggle} style={TH_R} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => {
                const clickable = !!onSelectSymbol && !!r.chartSymbol;
                return (
                  <tr
                    key={`${r.broker}:${r.ticker}:${i}`}
                    onClick={clickable ? () => onSelectSymbol!(r.chartSymbol!) : undefined}
                    title={clickable ? `Open ${r.chartSymbol} detail` : undefined}
                    style={{ borderBottom: "1px solid #141b2b", cursor: clickable ? "pointer" : "default" }}
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
      )}
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
        color: colour, border: `1px solid ${colour}`, borderRadius: 4, padding: "0 4px",
        verticalAlign: "middle",
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

const TH:   React.CSSProperties = { textAlign: "left",   padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TH_C: React.CSSProperties = { ...TH, textAlign: "center" };
const TD:   React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };
