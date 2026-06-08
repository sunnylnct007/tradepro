/**
 * AccountSummaryGrid — compact IBKR-style account summary table.
 *
 * Replaces the bulky per-broker KPI tile cards (DeskKpiRow) with a single
 * dense table: one row per active broker, columns:
 *   Account (+ LIVE/DEMO badge) · Daily P&L · Daily% · Unrealized · Realized(LTD)
 *   · Net Liq · Avail Funds
 *
 * Native currency per row — never blended. Nulls show "n/a" or "—", never a
 * fabricated value. Data from cashSummary + pnlByStrategy (same as DeskKpiRow).
 * Refreshes every 60 s.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { fmtMoney, fmtPct, signColour, accountMode, type AccountMode } from "./deskFormat";
import { ModeBadge } from "./ModeBadge";

type AccountRow = {
  broker: string;
  label: string;
  ccy: string;
  status: "ok" | "degraded" | "down" | "disabled";
  mode: AccountMode;
  netLiq: number | null;
  available: number | null;
  openPnl: number | null;
  realisedToday: number | null;
  realisedLtd: number | null;
};

type CashBroker = Awaited<ReturnType<typeof api.cashSummary>>["brokers"][number];
type PnlRow     = Awaited<ReturnType<typeof api.pnlByStrategy>>["rows"][number];

function buildRows(cash: CashBroker[], pnl: PnlRow[]): AccountRow[] {
  const realised = new Map<string, { today: number | null; ltd: number | null }>();
  for (const r of pnl) {
    const key = (r.broker || "").toLowerCase();
    if (!key) continue;
    const cur = realised.get(key) ?? { today: null, ltd: null };
    if (r.realisedToday != null) cur.today = (cur.today ?? 0) + r.realisedToday;
    if (r.realisedLtd   != null) cur.ltd   = (cur.ltd   ?? 0) + r.realisedLtd;
    realised.set(key, cur);
  }
  return cash
    .filter((c) => c.status !== "disabled")
    .map((c) => {
      const rl = realised.get((c.broker || "").toLowerCase());
      return {
        broker:       c.broker,
        label:        c.label || c.broker,
        ccy:          c.currency ?? "",
        status:       c.status,
        mode:         accountMode(c.label, c.broker, c.mode),
        netLiq:       c.total ?? c.balance ?? null,
        available:    c.available ?? c.free ?? null,
        openPnl:      c.openPnl ?? null,
        realisedToday: rl?.today ?? null,
        realisedLtd:  rl?.ltd   ?? null,
      };
    });
}

function statusDot(s: AccountRow["status"]) {
  const colour =
    s === "ok"       ? "#1fc16b" :
    s === "degraded" ? "#f59e0b" :
    s === "down"     ? "#ef4444" :
    "var(--text-muted)";
  return (
    <span
      style={{
        display: "inline-block", width: 7, height: 7,
        borderRadius: "50%", background: colour, marginRight: 6,
      }}
    />
  );
}

export function AccountSummaryGrid() {
  const [rows, setRows]     = useState<AccountRow[]>([]);
  const [strat, setStrat]   = useState<PnlRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr]       = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [cash, pnl] = await Promise.all([
          api.cashSummary(),
          api.pnlByStrategy().catch(() => null),
        ]);
        if (live) {
          setRows(buildRows(cash.brokers, pnl?.rows ?? []));
          setStrat(pnl?.rows ?? []);
          setErr(null);
        }
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : String(e));
      } finally {
        if (live) setLoading(false);
      }
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  if (loading && rows.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10 }}>
        Loading account summary…
      </div>
    );
  }
  if (err) {
    return (
      <div style={{ fontSize: 11, color: "var(--down, #ef4444)", marginBottom: 10 }}>
        Account summary unavailable: {err}
      </div>
    );
  }

  return (
    <>
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: "rgba(255,255,255,0.015)",
        marginBottom: 14,
        overflowX: "auto",
      }}
    >
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 680 }}>
        <thead>
          <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
            <th style={TH}>Account</th>
            <th style={TH_R}>Daily P&amp;L</th>
            <th style={TH_R}>Daily %</th>
            <th style={TH_R}>Unrealized</th>
            <th style={TH_R}>Realized (LTD)</th>
            <th style={TH_R}>Net Liq</th>
            <th style={TH_R}>Avail Funds</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const dailyPnl = sumNullable(r.openPnl, r.realisedToday);
            const dailyPct =
              dailyPnl != null && r.netLiq != null && r.netLiq !== 0
                ? (dailyPnl / r.netLiq) * 100
                : null;
            return (
              <tr key={r.broker} style={{ borderBottom: "1px solid #141b2b" }}>
                <td style={TD}>
                  {statusDot(r.status)}
                  <span style={{ fontWeight: 700 }}>{r.label}</span>
                  {" "}
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{r.ccy}</span>
                  {" "}
                  <ModeBadge mode={r.mode} />
                </td>
                <td style={{ ...TD_R, color: signColour(dailyPnl) }}>
                  {fmtMoney(dailyPnl, r.ccy, true)}
                </td>
                <td style={{ ...TD_R, color: signColour(dailyPct) }}>
                  {fmtPct(dailyPct)}
                </td>
                <td style={{ ...TD_R, color: signColour(r.openPnl) }}>
                  {fmtMoney(r.openPnl, r.ccy, true)}
                </td>
                <td style={{ ...TD_R, color: signColour(r.realisedLtd) }}>
                  {fmtMoney(r.realisedLtd, r.ccy, true)}
                </td>
                <td style={TD_R}>{fmtMoney(r.netLiq, r.ccy)}</td>
                <td style={TD_R}>{fmtMoney(r.available, r.ccy)}</td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={7} style={{ ...TD, color: "var(--text-muted)", textAlign: "center" }}>
                No connected brokers.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <div style={{ padding: "4px 10px 5px", fontSize: 9, color: "var(--text-dim)" }}>
        Native currency per broker · never blended · Daily P&amp;L = Unrealized + Realised today ·
        refreshes every 60 s
      </div>
    </div>
    <StrategyPnlTable rows={strat} />
    </>
  );
}

/**
 * Per-strategy P&L — breaks the per-broker totals down to each desk so e.g.
 * intraday_flat's IG-equity result is visible separately from the IG FX desk
 * (they share the IG account). Unrealized shows "flat" (not blank) when the
 * desk holds nothing right now but has traded — so a £0 open book reads as
 * intentional, not missing data.
 */
function StrategyPnlTable({ rows }: { rows: PnlRow[] }) {
  const active = rows.filter((r) => r.strategyId);
  if (active.length === 0) return null;
  return (
    <div
      style={{
        border: "1px solid #1b2233", borderRadius: 6,
        background: "rgba(255,255,255,0.015)", marginBottom: 14, overflowX: "auto",
      }}
    >
      <div style={{ padding: "7px 10px 2px", fontSize: 11, fontWeight: 700, color: "var(--text-dim)" }}>
        By strategy / desk
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 640 }}>
        <thead>
          <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid #1b2233" }}>
            <th style={TH}>Strategy</th>
            <th style={TH}>Broker</th>
            <th style={TH_R}>Unrealized</th>
            <th style={TH_R}>Realised (today)</th>
            <th style={TH_R}>Realised (LTD)</th>
            <th style={TH_R}>Trades</th>
          </tr>
        </thead>
        <tbody>
          {active.map((r) => {
            // open=null but the desk has traded → it's flat right now, not "no data".
            const openCell = r.openPnl == null
              ? (r.trades > 0
                  ? <span style={{ color: "var(--text-muted)" }}>flat</span>
                  : <span style={{ color: "var(--text-muted)" }}>—</span>)
              : <span style={{ color: signColour(r.openPnl) }}>{fmtMoney(r.openPnl, r.currency, true)}</span>;
            return (
              <tr key={r.strategyId} style={{ borderBottom: "1px solid #141b2b" }}>
                <td style={{ ...TD, fontWeight: 700 }}>{r.strategyId}</td>
                <td style={{ ...TD, color: "var(--text-muted)", fontSize: 10 }}>{r.broker}</td>
                <td style={TD_R}>{openCell}</td>
                <td style={{ ...TD_R, color: signColour(r.realisedToday) }}>{fmtMoney(r.realisedToday, r.currency, true)}</td>
                <td style={{ ...TD_R, color: signColour(r.realisedLtd) }}>{fmtMoney(r.realisedLtd, r.currency, true)}</td>
                <td style={{ ...TD_R, color: "var(--text-muted)" }}>{r.trades}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ padding: "4px 10px 5px", fontSize: 9, color: "var(--text-dim)" }}>
        Per-strategy realised from oms fills (golden source) · "flat" = desk holds nothing now ·
        IG FX + intraday equity share the IG account but are shown separately here
      </div>
    </div>
  );
}

function sumNullable(a: number | null, b: number | null): number | null {
  if (a == null && b == null) return null;
  return (a ?? 0) + (b ?? 0);
}

const TH:   React.CSSProperties = { textAlign: "left",  padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const TH_R: React.CSSProperties = { ...TH, textAlign: "right" };
const TD:   React.CSSProperties = { padding: "7px 10px", fontFamily: "monospace" };
const TD_R: React.CSSProperties = { ...TD, textAlign: "right" };
