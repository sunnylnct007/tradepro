/**
 * LiveSignalFeed — chronological feed of the chain happening NOW:
 *   strategy decision → OMS order → broker fill
 *
 * Closes the "I haven't seen a proper signal flowing yet" gap. The
 * existing surfaces show pieces (cockpit banner counts, OMS list,
 * snapshot decisions) but no single panel renders the full chain
 * unfolding in real time. This is that panel.
 *
 * Polls every 10s, shows last 25 events newest-first. Each row is
 * one of:
 *   • SIGNAL  — strategy decided fire-buy / fire-sell / fire-moo-exit
 *   • ORDER   — OMS row created with broker label + state
 *   • FILL    — terminal state reached (FILLED / REJECTED / CANCELLED)
 *
 * Each event carries the time / strategy / symbol / context so the
 * trader can trace cause→effect at a glance.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { fmtWhen } from "../../util/time";

type FeedItem = {
  ts: string;              // ISO
  kind: "SIGNAL" | "ORDER" | "FILL";
  strategy: string;
  symbol: string;
  detail: string;
  colour: string;
};

export function LiveSignalFeed() {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const next: FeedItem[] = [];
        // Pull recent orders (OMS state machine events surface the
        // order + fill side). 50 covers 1-2 hours of activity in
        // current volumes.
        const { orders } = await api.omsOrders(undefined, 50);
        for (const o of orders) {
          next.push({
            ts: o.createdAtUtc,
            kind: "ORDER",
            strategy: o.strategyId ?? "",
            symbol: o.symbol,
            detail: `${o.side} ${o.qty} → ${o.broker}`,
            colour: o.side === "BUY" ? "var(--up)" : "var(--down)",
          });
          // Terminal-state row (FILLED / REJECTED / CANCELLED) sits
          // above the order to read newest-first naturally.
          const terminal = ["FILLED", "REJECTED", "CANCELLED"].includes(o.state);
          if (terminal) {
            next.push({
              ts: o.lastStateChangeAtUtc,
              kind: "FILL",
              strategy: o.strategyId ?? "",
              symbol: o.symbol,
              detail: o.state === "FILLED"
                ? `FILLED ${o.filledQty} @ ${(o.avgFillPrice ?? 0).toFixed(4)}`
                : `${o.state}${o.cancelledReason ? `: ${o.cancelledReason.slice(0, 50)}` : ""}`,
              colour: o.state === "FILLED" ? "#1fc16b"
                : o.state === "REJECTED" ? "var(--down)"
                : "var(--text-muted)",
            });
          }
        }
        // Pull latest paper-session decisions for the SIGNAL kind.
        try {
          const snapshots = await api.paperSnapshots();
          const today = new Date().toISOString().slice(0, 10);
          for (const snap of snapshots.slice(0, 5)) {
            if (!snap.sessionLabel.endsWith(today)) continue;
            const detail = await api.paperSnapshot(snap.sessionLabel) as {
              strategies?: Array<{
                strategy_id?: string;
                decisions?: Array<{
                  bar_ts?: string; symbol?: string; action?: string; reason?: string;
                }>;
              }>;
            };
            for (const st of detail.strategies ?? []) {
              for (const dec of st.decisions ?? []) {
                if (!dec.action?.startsWith("fire-")) continue;
                next.push({
                  ts: dec.bar_ts ?? snap.asOfUtc,
                  kind: "SIGNAL",
                  strategy: st.strategy_id ?? snap.sessionLabel,
                  symbol: dec.symbol ?? "",
                  detail: `${dec.action} · ${dec.reason ?? ""}`.slice(0, 110),
                  colour: dec.action.includes("buy") ? "var(--up)"
                    : dec.action.includes("sell") || dec.action.includes("exit") ? "var(--down)"
                    : "var(--neutral)",
                });
              }
            }
          }
        } catch {/* ignore — orders still surface */}

        next.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
        if (!cancelled) {
          setItems(next.slice(0, 25));
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    };
    void load();
    const t = setInterval(load, 10_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (err) return <div style={{ fontSize: 11, color: "var(--down)" }}>signal feed: {err}</div>;
  if (items.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
        No recent activity. Strategies fire on their schedule (intraday
        every minute during session hours; paper-fx every cycle). The
        feed updates every 10s.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {items.map((it, i) => (
        <FeedRow key={`${it.ts}-${it.kind}-${i}`} item={it} />
      ))}
    </div>
  );
}

function FeedRow({ item }: { item: FeedItem }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "60px 70px 110px 90px 1fr",
      gap: 8, alignItems: "baseline",
      padding: "3px 6px",
      borderLeft: `2px solid ${item.colour}`,
      background: "rgba(0,0,0,0.10)",
      borderRadius: 3,
      fontSize: 11,
    }}>
      <span style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: 10, whiteSpace: "nowrap" }}>
        {fmtWhen(item.ts)}
      </span>
      <span style={{
        color: item.colour,
        fontWeight: 700,
        fontSize: 10,
        letterSpacing: "0.05em",
      }}>
        {item.kind}
      </span>
      <span style={{
        color: "var(--text)",
        fontFamily: "monospace",
        fontSize: 10,
      }}>
        {item.symbol.slice(0, 18)}
      </span>
      <span style={{ color: "var(--text-dim)", fontSize: 10 }}>
        {item.strategy.slice(0, 14)}
      </span>
      <span style={{ color: "var(--text)" }}>
        {item.detail}
      </span>
    </div>
  );
}
