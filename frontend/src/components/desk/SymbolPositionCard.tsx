/**
 * SymbolPositionCard — position summary for ONE symbol in the right rail.
 *
 * One job: show shares · avg price · market value · unrealized P&L · % for the
 * currently-selected symbol, drawn from the merged broker positions already
 * loaded by the parent. "Not held" when the symbol isn't in the book.
 *
 * Props:
 *   symbol     — Yahoo-style chart symbol (must match a held row's chartSymbol)
 *   positions  — merged broker position rows (from PositionsTable's data model)
 *
 * No extra fetch — reads from props so the right rail doesn't trigger another
 * positions call independently.
 */
import { fmtEntryDate, fmtMoney, fmtNum, fmtPct, signColour } from "./deskFormat";
import { ModeBadge } from "./ModeBadge";
import type { AccountMode } from "./deskFormat";

export type PositionRow = {
  broker: string;
  ticker: string;
  chartSymbol: string | null;
  qty: number;
  last: number | null;
  pnl: number | null;
  pnlPct: number | null;
  ccy: string | null;
  mode: AccountMode;
  avgPrice?: number | null;
  /** Position open date (broker-reported, e.g. T212 createdAt) — when the
   *  entry was first taken. Shown as "Opened" + on the chart's Entry line. */
  entryDate?: string | null;
};

export function SymbolPositionCard({
  symbol,
  positions,
}: {
  symbol: string;
  positions: PositionRow[];
}) {
  const held = positions.find(
    (r) => r.chartSymbol === symbol || r.ticker === symbol,
  );

  return (
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: "rgba(255,255,255,0.015)",
        padding: "10px 12px",
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 8,
          fontWeight: 600,
        }}
      >
        Position
      </div>

      {!held ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          Not held in any account.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <StatRow label="Broker">
            <span style={{ fontWeight: 700 }}>{held.broker}</span>
            {" "}
            <ModeBadge mode={held.mode} />
          </StatRow>
          <StatRow label="Shares">{fmtNum(held.qty)}</StatRow>
          {held.avgPrice != null && (
            <StatRow label="Avg Price">{fmtMoney(held.avgPrice, held.ccy)}</StatRow>
          )}
          {held.entryDate && (
            <StatRow label="Opened">{fmtEntryDate(held.entryDate)}</StatRow>
          )}
          <StatRow label="Last Price">{fmtMoney(held.last, held.ccy)}</StatRow>
          {held.last != null && held.qty != null && (
            <StatRow label="Mkt Value">
              {fmtMoney(held.last * held.qty, held.ccy)}
            </StatRow>
          )}
          <StatRow label="Unrealized P&L">
            <span style={{ color: signColour(held.pnl) }}>
              {fmtMoney(held.pnl, held.ccy, true)}
            </span>
          </StatRow>
          <StatRow label="P&L %">
            <span style={{ color: signColour(held.pnlPct) }}>
              {fmtPct(held.pnlPct)}
            </span>
          </StatRow>
        </div>
      )}
    </div>
  );
}

function StatRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        fontSize: 12,
        gap: 8,
      }}
    >
      <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>{label}</span>
      <span style={{ fontFamily: "monospace", textAlign: "right" }}>{children}</span>
    </div>
  );
}
