/**
 * StrategyComparePanel — rank the strategy sleeves side-by-side.
 *
 * One row per strategy (intraday_flat · ichimoku_equity · ichimoku_fx_mr),
 * sortable on every money column, so the trader can answer "which sleeve is
 * actually making money?" at a glance. It reads GET /api/pnl/by-strategy, which
 * already does the honest work: each field is null when the broker genuinely
 * can't supply it (T212 has no realised feed → derived from the OMS fill ledger
 * via FIFO; an IG position with no mark is excluded), with the reason in
 * `notes`. We render those nulls as a plain "n/a" with the note as a tooltip.
 *
 * Currencies are NEVER blended — each row shows its broker's native currency
 * (T212 → USD $, IG → GBP £) and labels it. There is deliberately no grand
 * total across rows, because adding GBP to USD would be a lie.
 */
import { useEffect, useState } from "react";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";
import { useSort } from "../../util/useSort";
import { SortTh } from "../SortTh";

type Row = {
  strategyId: string;
  broker: string;
  currency: string;
  openPnl: number | null;
  realisedToday: number | null;
  realisedLtd: number | null;
  realisedPnl: number | null;
  totalPnl: number | null;
  trades: number;
  winRatePct: number | null;
  notes: string;
};

const UP = "#1fc16b";
const DOWN = "#ef4444";
const ccySym = (c?: string | null) => (c === "GBP" ? "£" : c === "USD" ? "$" : c ? c + " " : "");

const STRAT_LABEL: Record<string, string> = {
  ichimoku_equity: "Ichimoku Equity",
  ichimoku_fx_mr: "Ichimoku FX (MR)",
  intraday_flat: "Intraday Flat",
};
const sLabel = (id: string) => STRAT_LABEL[id] ?? id;

// A nullable money cell: coloured signed value, or a plain "n/a" carrying the
// row's note as a tooltip so the trader knows WHY it's missing.
function Money({ v, c, note, bold = false }: { v: number | null; c: string; note?: string; bold?: boolean }) {
  if (v == null)
    return (
      <span title={note || undefined} style={{ color: "var(--text-muted)", cursor: note ? "help" : "default" }}>
        n/a
      </span>
    );
  return (
    <span style={{ fontFamily: "monospace", fontWeight: bold ? 700 : 500, color: v >= 0 ? UP : DOWN }}>
      {v >= 0 ? "+" : "−"}
      {ccySym(c)}
      {Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}
    </span>
  );
}

export function StrategyComparePanel({ onHide }: { onHide?: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [basis, setBasis] = useState<string | null>(null);

  const { sorted, sortKey, dir, toggle } = useSort<Row>(
    rows,
    {
      strategy: (r) => sLabel(r.strategyId),
      broker: (r) => r.broker,
      realisedToday: (r) => r.realisedToday,
      realisedLtd: (r) => r.realisedLtd,
      open: (r) => r.openPnl,
      trades: (r) => r.trades,
      win: (r) => r.winRatePct,
    },
    { key: "realisedToday", dir: "desc" },
  );

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const res = await api.pnlByStrategy();
        if (!live) return;
        if (res.error) {
          setErr(res.error);
          setRows([]);
        } else {
          setRows(res.rows ?? []);
          setBasis(res.window?.basis ?? null);
          setErr(null);
        }
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : "failed to load");
      }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  return (
    <CockpitCard id="strategy-compare" title="Strategy P&L comparison" fullWidth onHide={onHide}>
      <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginBottom: 8 }}>
        Realised = banked (closed trades). Open = unrealized mark-to-market, now. Per native currency — not blended.{" "}
        <em>n/a</em> = the broker can't supply that component — hover it for why.
      </div>

      {err && <div style={{ fontSize: 12, color: DOWN, padding: "6px 0" }}>Comparison unavailable: {err}</div>}
      {!err && rows.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "12px 0" }}>No mapped strategies to compare.</div>
      )}

      {rows.length > 0 && (
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "var(--text-muted)", textAlign: "right" }}>
              <SortTh label="Strategy" col="strategy" sortKey={sortKey} dir={dir} onSort={toggle} style={{ textAlign: "left" }} />
              <SortTh label="Broker" col="broker" sortKey={sortKey} dir={dir} onSort={toggle} style={{ textAlign: "left" }} />
              <SortTh label="Realised today" col="realisedToday" sortKey={sortKey} dir={dir} onSort={toggle} />
              <SortTh label="Realised LTD" col="realisedLtd" sortKey={sortKey} dir={dir} onSort={toggle} />
              <SortTh label="Open (now)" col="open" sortKey={sortKey} dir={dir} onSort={toggle} />
              <SortTh label="Trades" col="trades" sortKey={sortKey} dir={dir} onSort={toggle} />
              <SortTh label="Win%" col="win" sortKey={sortKey} dir={dir} onSort={toggle} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.strategyId} style={{ borderTop: "1px solid var(--border)", textAlign: "right" }}>
                <td style={{ textAlign: "left", fontWeight: 600 }}>{sLabel(r.strategyId)}</td>
                <td style={{ textAlign: "left", color: "var(--text-muted)", fontFamily: "monospace" }}>
                  {r.broker} <span style={{ fontSize: 10 }}>({r.currency})</span>
                </td>
                <td><Money v={r.realisedToday} c={r.currency} note={r.notes} bold /></td>
                <td><Money v={r.realisedLtd} c={r.currency} note={r.notes} /></td>
                <td><Money v={r.openPnl} c={r.currency} note={r.notes} /></td>
                <td style={{ fontFamily: "monospace" }}>{r.trades > 0 ? r.trades : <span style={{ color: "var(--text-muted)" }}>0</span>}</td>
                <td style={{ fontFamily: "monospace" }}>
                  {r.winRatePct != null ? (
                    `${r.winRatePct.toFixed(0)}%`
                  ) : (
                    <span title={r.notes || undefined} style={{ color: "var(--text-muted)", cursor: r.notes ? "help" : "default" }}>n/a</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rows.length > 0 && (
        <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
          <strong>Realised today</strong> = banked on today's (UTC) closed trades.{" "}
          <strong>Realised LTD</strong> = banked life-to-date: IG = closed-deal history (golden); T212 = replayed from the
          OMS fill ledger (FIFO — T212 has no realised feed). <strong>Open (now)</strong> = unrealised mark-to-market from
          the broker's own price. <strong>Win%</strong> is over closed trades. Hover any <em>n/a</em> for the per-strategy
          reason.
          {basis && <div style={{ marginTop: 3, opacity: 0.8 }}>{basis}</div>}
        </div>
      )}
    </CockpitCard>
  );
}
