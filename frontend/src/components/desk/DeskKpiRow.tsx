/**
 * DeskKpiRow — the account-KPI summary row under the top bar.
 *
 * IBKR shows: Account · Daily P&L · Daily P&L% · Unrealized P&L · Realized
 * P&L · Net Liquidity · Available Funds · (Excess Liq / Maintenance / Initial
 * Margin / Buying Power). We OMIT the margin/buying-power tiles — T212/IG
 * don't expose them and we never fabricate a number.
 *
 * One group of tiles PER BROKER, native currency, never blended (T212 USD vs
 * IG GBP). Data reuses the cockpit's existing endpoints only:
 *   - api.cashSummary()    → value / available / openPnl / currency / status
 *   - api.pnlByStrategy()  → realisedToday (summed per broker) for Daily P&L
 *
 * Mobile: the tile grid is auto-fit + minmax, so it collapses from one strip
 * to 2-per-row on a phone with no media queries and no horizontal overflow.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { fmtMoney, fmtPct, signColour, accountMode, type AccountMode } from "./deskFormat";
import { ModeBadge } from "./ModeBadge";

type Group = {
  broker: string;
  code: string;
  label: string;
  ccy: string;
  status: "ok" | "degraded" | "down" | "disabled";
  mode: AccountMode;
  netLiq: number | null;
  available: number | null;
  openPnl: number | null;   // unrealized now
  realisedToday: number | null;
  realisedLtd: number | null;
};

export function DeskKpiRow() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [cash, pnl] = await Promise.all([
          api.cashSummary(),
          api.pnlByStrategy().catch(() => null),
        ]);
        if (!live) return;
        setGroups(merge(cash.brokers, pnl?.rows ?? []));
        setErr(null);
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

  if (loading && groups.length === 0) {
    return <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 12 }}>Loading account KPIs…</div>;
  }
  if (err) {
    return <div style={{ fontSize: 11, color: "var(--down, #ef4444)", marginBottom: 12 }}>KPIs unavailable: {err}</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
      {groups.map((g) => (
        <BrokerKpis key={g.broker} g={g} />
      ))}
    </div>
  );
}

function BrokerKpis({ g }: { g: Group }) {
  // Daily P&L% relative to net-liq (best available denominator we have
  // honestly; null when either side is missing — never a guessed base).
  const dailyPnl = sumNullable(g.openPnl, g.realisedToday);
  const dailyPct =
    dailyPnl != null && g.netLiq != null && g.netLiq !== 0
      ? (dailyPnl / g.netLiq) * 100
      : null;

  return (
    <div>
      <div
        style={{
          fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase",
          letterSpacing: "0.05em", marginBottom: 6, display: "flex", gap: 8, alignItems: "center",
        }}
      >
        <span
          style={{
            width: 7, height: 7, borderRadius: "50%",
            background: statusColour(g.status), display: "inline-block",
          }}
        />
        {g.label} · {g.ccy || "—"}
        <ModeBadge mode={g.mode} />
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(140px, 100%), 1fr))",
          gap: 8,
        }}
      >
        <Tile label="Account" value={g.label} mono={false} />
        <Tile label="Daily P&L" value={fmtMoney(dailyPnl, g.ccy, true)} colour={signColour(dailyPnl)} />
        <Tile label="Daily P&L %" value={fmtPct(dailyPct)} colour={signColour(dailyPct)} />
        <Tile label="Unrealized P&L" value={fmtMoney(g.openPnl, g.ccy, true)} colour={signColour(g.openPnl)} />
        <Tile label="Realized P&L (LTD)" value={fmtMoney(g.realisedLtd, g.ccy, true)} colour={signColour(g.realisedLtd)} />
        <Tile label="Net Liquidity" value={fmtMoney(g.netLiq, g.ccy)} />
        <Tile label="Available Funds" value={fmtMoney(g.available, g.ccy)} />
      </div>
    </div>
  );
}

function Tile({
  label, value, colour = "var(--text)", mono = true,
}: {
  label: string;
  value: string;
  colour?: string;
  mono?: boolean;
}) {
  return (
    <div
      style={{
        border: "1px solid #1b2233",
        borderRadius: 6,
        background: "rgba(255,255,255,0.02)",
        padding: "8px 10px",
        minWidth: 0,
      }}
    >
      <div
        style={{
          fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase",
          letterSpacing: "0.05em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 16, fontWeight: 700, marginTop: 3, color: colour,
          fontFamily: mono ? "monospace" : undefined,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}
      >
        {value}
      </div>
    </div>
  );
}

type CashBroker = Awaited<ReturnType<typeof api.cashSummary>>["brokers"][number];
type PnlRow = Awaited<ReturnType<typeof api.pnlByStrategy>>["rows"][number];

function merge(cash: CashBroker[], pnl: PnlRow[]): Group[] {
  const realised = new Map<string, { today: number | null; ltd: number | null }>();
  for (const r of pnl) {
    const key = (r.broker || "").toLowerCase();
    if (!key) continue;
    const cur = realised.get(key) ?? { today: null, ltd: null };
    if (r.realisedToday != null) cur.today = (cur.today ?? 0) + r.realisedToday;
    if (r.realisedLtd != null) cur.ltd = (cur.ltd ?? 0) + r.realisedLtd;
    realised.set(key, cur);
  }
  return cash
    .filter((c) => c.status !== "disabled")
    .map((c) => {
      const rl = realised.get((c.broker || "").toLowerCase());
      return {
        broker: c.broker,
        code: brokerCode(c.broker, c.label),
        label: c.label || c.broker,
        ccy: c.currency ?? "",
        status: c.status,
        mode: accountMode(c.label, c.broker, c.mode),
        netLiq: c.total ?? c.balance ?? null,
        available: c.available ?? c.free ?? null,
        openPnl: c.openPnl ?? null,
        realisedToday: rl?.today ?? null,
        realisedLtd: rl?.ltd ?? null,
      };
    });
}

function brokerCode(broker: string, label: string): string {
  const b = (broker || label || "").toLowerCase();
  if (b.includes("212") || b.includes("trading")) return "T212";
  if (b.includes("ibkr") || b.includes("interactive")) return "IBKR";
  if (b.includes("ig")) return "IG";
  return (broker || label || "?").toUpperCase().slice(0, 6);
}

function statusColour(s: Group["status"]): string {
  switch (s) {
    case "ok": return "var(--up, #1fc16b)";
    case "degraded": return "#f59e0b";
    case "down": return "var(--down, #ef4444)";
    default: return "var(--text-muted)";
  }
}

function sumNullable(a: number | null, b: number | null): number | null {
  if (a == null && b == null) return null;
  return (a ?? 0) + (b ?? 0);
}
