/**
 * TradeCardsPanel — one "card per fired signal" that joins the
 * Strategy → OMS Order → Fill → P&L chain visually so the trader
 * doesn't have to cross-reference three tables.
 *
 * Match heuristic: order belongs to a signal if it shares strategy_id
 * + symbol + side. The OMS ClientOrderId is a hash that includes
 * bar_ts so a perfect match would need the daemon's hashing code
 * inline — strategy+symbol+side discriminates every realistic case
 * (one signal per strategy per symbol per session).
 */
import { Link } from "react-router-dom";
import { CockpitCard } from "../CockpitCard";
import type { OmsOrderRow } from "../../api/client";
import type { DecisionEntry, LatestSession, T212PosResp } from "../../types/cockpit";

type Card = {
  key: string;
  strategy: string;
  symbol: string;
  side: string;
  decision?: DecisionEntry;
  order?: OmsOrderRow;
  pnl?: { abs: number; pct: number; currency: string | null } | null;
};

export function TradeCardsPanel({
  orders, positions, latestSessions, onHide,
}: {
  orders: OmsOrderRow[];
  positions: T212PosResp | null;
  latestSessions: LatestSession[];
  onHide?: () => void;
}) {
  const cards = buildCards(orders, positions, latestSessions);
  const visible = cards.slice(0, 12);
  return (
    <CockpitCard
      id="trade-cards"
      title="Trades — signal → order → fill → P&L"
      badge={visible.length || undefined}
      defaultOpen={visible.length > 0}
      fullWidth
      onHide={onHide}
    >
      {visible.length === 0 ? (
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No fire-* signals from the latest session. When a strategy fires,
          this panel shows each signal joined to its OMS order + fill price
          + unrealised P&L from the matching T212 position.
        </span>
      ) : (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 10,
        }}>
          {visible.map((c) => (<TradeCard key={c.key} card={c} />))}
        </div>
      )}
    </CockpitCard>
  );
}

function buildCards(
  orders: OmsOrderRow[],
  positions: T212PosResp | null,
  latestSessions: LatestSession[],
): Card[] {
  const cards: Card[] = [];
  for (const s of latestSessions) {
    for (const d of s.decisions) {
      if (!d.action.startsWith("fire-")) continue;
      const sideFromDetail = (d.detail.side as string | undefined) ?? "";
      const side = sideFromDetail || (d.action.includes("entry") ? "BUY" : "SELL");
      const order = orders.find((o) =>
        o.strategyId === s.strategy &&
        o.side === side &&
        (o.symbol === d.symbol ||
         o.symbol.startsWith(d.symbol + "_") ||
         d.symbol.startsWith(o.symbol + "_")),
      );
      const pos = positions?.positions.find(
        (p) => p.ticker === d.symbol ||
               p.yahooSymbol === d.symbol ||
               p.ticker.startsWith(d.symbol + "_"),
      );
      cards.push({
        key: `${s.strategy}.${d.symbol}.${d.barTs ?? ""}`,
        strategy: s.strategy, symbol: d.symbol, side,
        decision: d,
        order,
        pnl: pos && pos.unrealisedAbs != null && pos.unrealisedPct != null
          ? { abs: pos.unrealisedAbs, pct: pos.unrealisedPct, currency: pos.currency }
          : null,
      });
    }
  }
  cards.sort((a, b) =>
    (b.decision?.barTs ?? "").localeCompare(a.decision?.barTs ?? ""),
  );
  return cards;
}

function TradeCard({ card }: { card: Card }) {
  const sideColor = card.side === "BUY" ? "#1fc16b" : "#ef4444";
  const orderStateColor =
    card.order?.state === "FILLED" ? "#1fc16b" :
    card.order?.state === "REJECTED" ? "#ef4444" :
    card.order?.state === "CANCELLED" ? "var(--text-muted)" :
    card.order ? "#4f8cff" : "var(--text-muted)";
  const pnlColor = card.pnl && card.pnl.abs >= 0 ? "#1fc16b" : "#ef4444";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: 10,
        fontSize: 12,
        background: "var(--bg-hover, rgba(255,255,255,0.02))",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ fontFamily: "monospace", fontWeight: 700, fontSize: 14 }}>
          {card.symbol}
        </span>
        <span style={{
          fontSize: 10, padding: "1px 6px",
          borderRadius: 999, color: sideColor,
          background: card.side === "BUY" ? "rgba(31,193,107,0.10)" : "rgba(239,68,68,0.10)",
          fontWeight: 700, letterSpacing: "0.04em",
        }}>
          {card.side}
        </span>
        <span style={{
          fontSize: 10, color: "var(--text-muted)",
          fontFamily: "monospace", marginLeft: "auto",
        }}>
          {card.strategy}
        </span>
      </div>

      <ChainStep
        label="Signal"
        ok={!!card.decision}
        detail={card.decision
          ? `${card.decision.action} · ${card.decision.reason || "(no reason)"}`
          : "no decision logged"}
        ts={card.decision?.barTs ?? null}
      />
      <ChainStep
        label="Order"
        ok={!!card.order}
        detail={card.order
          ? `qty=${card.order.qty} · ${card.order.state}`
          : "no matching OMS order yet"}
        tone={orderStateColor}
        ts={card.order?.createdAtUtc ?? null}
        href={card.order ? "/oms" : undefined}
      />
      <ChainStep
        label="Fill"
        ok={card.order?.state === "FILLED" || (card.order?.filledQty ?? 0) > 0}
        detail={
          card.order?.avgFillPrice != null
            ? `${card.order.filledQty} @ ${card.order.avgFillPrice.toFixed(4)}`
            : card.order?.state === "REJECTED"
              ? `rejected${card.order.cancelledReason ? `: ${card.order.cancelledReason}` : ""}`
              : "awaiting fill"
        }
        tone={card.order?.state === "REJECTED" ? "#ef4444" : undefined}
        ts={card.order?.state === "FILLED" ? card.order?.lastStateChangeAtUtc ?? null : null}
      />
      <ChainStep
        label="P&L"
        ok={card.pnl != null}
        detail={card.pnl
          ? `${card.pnl.abs >= 0 ? "+" : ""}${card.pnl.currency ?? ""} ${card.pnl.abs.toFixed(2)} (${card.pnl.pct >= 0 ? "+" : ""}${card.pnl.pct.toFixed(2)}%)`
          : "no position open"}
        tone={card.pnl ? pnlColor : undefined}
      />
    </div>
  );
}

function ChainStep({
  label, ok, detail, tone, ts, href,
}: {
  label: string;
  ok: boolean;
  detail: string;
  tone?: string;
  ts?: string | null;
  href?: string;
}) {
  const dotColor = ok ? (tone ?? "#1fc16b") : "var(--text-muted)";
  const body = (
    <div style={{
      display: "grid",
      gridTemplateColumns: "12px 60px 1fr auto",
      gap: 6, alignItems: "baseline",
      padding: "3px 0",
      fontSize: 11,
      color: ok ? "var(--text)" : "var(--text-muted)",
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: 999,
        background: dotColor, alignSelf: "center",
        opacity: ok ? 1 : 0.4,
      }} />
      <span style={{
        color: "var(--text-dim)", fontSize: 10,
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        {label}
      </span>
      <span style={{
        color: tone ?? "inherit",
        fontFamily: detail.length < 40 ? "monospace" : "inherit",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {detail}
      </span>
      <span style={{ color: "var(--text-muted)", fontSize: 9, fontFamily: "monospace" }}>
        {ts ? new Date(ts).toLocaleTimeString([], { hour12: false }) : ""}
      </span>
    </div>
  );
  if (href) {
    return (
      <Link to={href} style={{ textDecoration: "none", color: "inherit", display: "block" }}>
        {body}
      </Link>
    );
  }
  return body;
}
