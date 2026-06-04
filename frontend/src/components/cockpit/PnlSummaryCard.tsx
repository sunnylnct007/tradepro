/**
 * PnlSummaryCard — the bottom-line P&L headline a trader reads first.
 *
 * The old surfaces stacked an open-MTM curve (green) next to a realised strip
 * (red) with no reconciliation, so "am I up or down?" wasn't answerable at a
 * glance. This card states it plainly, with the three numbers kept DISTINCT
 * (they mean different things) and a one-line explainer:
 *   • Realised  — banked from CLOSED trades (golden source). The truth.
 *   • Open      — mark-to-market on holdings (unrealized; can vanish).
 *   • Account   — total broker value now.
 * Per broker, in native currency (T212 USD, IG GBP) — we don't fake a single
 * blended number across currencies + partial realised coverage.
 */
import { useEffect, useState } from "react";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";

type Cash = Awaited<ReturnType<typeof api.cashSummary>>;
const UP = "#1fc16b";
const DOWN = "#ef4444";
const ccy = (c?: string | null) => (c === "GBP" ? "£" : c === "USD" ? "$" : (c ? c + " " : ""));

function Money({ v, c, bold }: { v: number | null | undefined; c?: string | null; bold?: boolean }) {
  if (v == null) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  return (
    <span style={{ fontFamily: "monospace", fontWeight: bold ? 700 : 500, color: v >= 0 ? UP : DOWN }}>
      {v >= 0 ? "+" : "−"}{ccy(c)}{Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}
    </span>
  );
}

export function PnlSummaryCard({ onHide }: { onHide?: () => void }) {
  const [cash, setCash] = useState<Cash | null>(null);
  const [realisedToday, setRealisedToday] = useState<number | null>(null);
  const [realised7d, setRealised7d] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    const load = () => {
      api.cashSummary().then((c) => { if (live) setCash(c); }).catch(() => {});
      api.igHistory(7).then((h) => {
        if (!live || !h.enabled) return;
        setRealised7d(h.totalRealised ?? null);
        const today = new Date().toISOString().slice(0, 10);
        const t = (h.byDay ?? []).find((d) => d.date === today);
        setRealisedToday(t ? t.realised : 0);
      }).catch(() => {});
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const brokers = (cash?.brokers ?? []).filter((b) => b.status === "ok");
  const t212 = brokers.find((b) => b.broker === "T212_DEMO");
  const ig = brokers.find((b) => b.broker.startsWith("IG"));

  const Block = ({ title, children, sub }: { title: string; sub?: string; children: React.ReactNode }) => (
    <div style={{ minWidth: 150 }}>
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{title}</div>
      <div style={{ fontSize: 15, marginTop: 2 }}>{children}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
    </div>
  );

  return (
    <CockpitCard id="pnl-summary" title="P&L — bottom line" fullWidth onHide={onHide}>
      <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "flex-start" }}>
        <Block title="Realised · banked" sub="IG closed deals incl. fees (golden)">
          today <Money v={realisedToday} c="GBP" bold />
          <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>·</span>
          7d <Money v={realised7d} c="GBP" />
        </Block>
        <Block title="Open · mark-to-market" sub="unrealized — can still move">
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>T212 </span><Money v={t212?.openPnl} c={t212?.currency} bold />
          <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>·</span>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>IG </span><Money v={ig?.openPnl} c={ig?.currency} bold />
        </Block>
        <Block title="Account value" sub="total broker value now">
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>T212 </span>
          <strong style={{ fontFamily: "monospace" }}>{ccy(t212?.currency)}{(t212?.total ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
          <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>·</span>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>IG </span>
          <strong style={{ fontFamily: "monospace" }}>{ccy(ig?.currency)}{(ig?.balance ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
        </Block>
      </div>
      <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 10, lineHeight: 1.5 }}>
        <strong>Realised</strong> = money banked from closed trades (the truth). <strong>Open</strong> = current mark on holdings
        (unrealized — it moves and can vanish). <strong>Account value</strong> = what each broker account is worth now.
        Kept per broker / native currency — not blended, since realised is IG-only and the currencies differ.
      </div>
    </CockpitCard>
  );
}
