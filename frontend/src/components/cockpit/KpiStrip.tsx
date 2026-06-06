/**
 * KpiStrip — IBKR-Desktop-style KPI strip pinned at the very top of the
 * cockpit. A dense horizontal row of self-labelling tiles, grouped per
 * broker, so the trader's first read is "where do I stand right now".
 *
 * Why per-broker and never blended: we run two accounts in different
 * currencies (T212 = USD equity book, IG = GBP FX + intraday). Summing
 * USD + GBP would fabricate a meaningless total, so each broker gets its
 * own group and every tile carries its native currency in the label.
 *
 * Tiles per broker (TYPE · WINDOW · SOURCE · CURRENCY discipline so a
 * newcomer can decode every number from the UI alone):
 *   - VALUE     — account value now (T212 `total`, IG `balance`).
 *   - OPEN      — unrealised P&L now (broker openPnl / Ppl).
 *   - REAL·TDY  — realised P&L banked today (sum of per-strategy rows).
 *   - REAL·LTD  — realised P&L banked life-to-date (sum of per-strategy).
 *
 * Data: reuses the cockpit's existing endpoints only — no new backend.
 *   - api.cashSummary()   → per-broker value + openPnl + currency + status
 *   - api.pnlByStrategy()  → per-strategy realisedToday / realisedLtd;
 *                            aggregated here per (broker, currency).
 * Polls every 60s like the other cockpit cards. Degrades gracefully:
 * loading / error / per-tile "n/a" — never throws, never invents a number.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { InlineHint } from "../InlineHint";

type CashRow = {
  broker: string;
  label: string;
  status: "ok" | "degraded" | "down" | "disabled";
  currency?: string | null;
  total?: number | null;
  balance?: number | null;
  openPnl?: number | null;
};

type PnlRow = {
  broker: string;
  currency: string;
  realisedToday: number | null;
  realisedLtd: number | null;
};

/** One broker's aggregated KPIs, ready to render. */
type BrokerKpis = {
  broker: string;          // short code used in the label (e.g. T212 / IG)
  label: string;           // human label from cash-summary
  currency: string;        // native currency code
  status: CashRow["status"];
  value: number | null;    // account value now
  openPnl: number | null;  // unrealised now
  realisedToday: number | null;
  realisedLtd: number | null;
};

export function KpiStrip({ onHide }: { onHide?: () => void } = {}) {
  const [groups, setGroups] = useState<BrokerKpis[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        // Two independent reads; both already polled elsewhere in the
        // cockpit. Settle together so one failing doesn't blank the strip.
        const [cash, pnl] = await Promise.all([
          api.cashSummary(),
          api.pnlByStrategy().catch(() => null), // realised tiles degrade to n/a
        ]);
        if (cancelled) return;
        setGroups(merge(cash.brokers as CashRow[], (pnl?.rows ?? []) as PnlRow[]));
        setErr(null);
      } catch (e) {
        if (cancelled) return;
        setErr(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (loading && groups.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 12 }}>
        Loading KPIs…
      </div>
    );
  }
  if (err) {
    return (
      <div style={{ fontSize: 11, color: "var(--down)", marginBottom: 12 }}>
        KPI strip unavailable: {err}
      </div>
    );
  }

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--bg-hover, rgba(255,255,255,0.03))",
        padding: "10px 12px",
        marginBottom: 12,
      }}
    >
      {onHide && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
          <button
            onClick={onHide}
            title="Hide KPI strip"
            aria-label="Hide KPI strip"
            style={{
              background: "transparent", border: "none", cursor: "pointer",
              color: "var(--text-muted)", fontSize: 14, lineHeight: 1, padding: 2,
            }}
          >
            ×
          </button>
        </div>
      )}
      {/* The tiles. auto-fit + minmax makes this inherently responsive:
          one strip on desktop, wraps to 3-4/row on tablet, 2/row on
          phone, and 1-col on the narrowest screens — no media queries,
          no horizontal overflow. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(150px, 100%), 1fr))",
          gap: 8,
        }}
      >
        {groups.map((g) => (
          <BrokerGroup key={g.broker} g={g} />
        ))}
      </div>

      {/* One-line legend so the labels self-explain (user rule: never
          ship a number a newcomer can't decode from the UI alone). */}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8 }}>
        Open = unrealized now · Realised = banked (today / life-to-date) · per broker, native currency
      </div>
    </div>
  );
}

/** A broker's four tiles, visually grouped (left border = status). */
function BrokerGroup({ g }: { g: BrokerKpis }) {
  const colour = statusColour(g.status);
  const ccy = g.currency || "";
  return (
    <>
      <Tile
        label={`VALUE · ${g.broker} · ${ccy}`}
        value={g.value}
        ccy={ccy}
        accent={colour}
        signed={false}
        hint={`Account value now for ${g.label} — T212 total / IG balance, native currency (${ccy || "n/a"}). Status: ${g.status}.`}
      />
      <Tile
        label={`OPEN · ${g.broker} · ${ccy}`}
        value={g.openPnl}
        ccy={ccy}
        accent={colour}
        signed
        hint={`Open (unrealised) P&L right now across ${g.label} positions, native currency (${ccy || "n/a"}). Excludes anything already banked.`}
      />
      <Tile
        label={`REAL·TDY · ${g.broker} · ${ccy}`}
        value={g.realisedToday}
        ccy={ccy}
        accent={colour}
        signed
        hint={`Realised P&L banked TODAY across ${g.label} strategies (UTC session), native currency (${ccy || "n/a"}). n/a when realised P&L isn't available.`}
      />
      <Tile
        label={`REAL·LTD · ${g.broker} · ${ccy}`}
        value={g.realisedLtd}
        ccy={ccy}
        accent={colour}
        signed
        hint={`Realised P&L banked life-to-date across ${g.label} strategies, native currency (${ccy || "n/a"}). n/a when realised P&L isn't available.`}
      />
    </>
  );
}

function Tile({
  label, value, ccy, accent, signed, hint,
}: {
  label: string;
  value: number | null;
  ccy: string;
  accent: string;
  signed: boolean;
  hint: string;
}) {
  const fg = value == null
    ? "var(--text-muted)"
    : signed
      ? value >= 0 ? "#1fc16b" : "#ef4444"
      : "var(--text)";
  return (
    <div style={{
      padding: "8px 10px",
      borderLeft: `3px solid ${accent}`,
      border: "1px solid var(--border)",
      borderRadius: 6,
      background: "rgba(0,0,0,0.10)",
      minWidth: 0, // let long values shrink instead of overflowing the grid cell
    }}>
      <div style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.05em",
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
      }}>
        {label}
        <InlineHint text={hint} />
      </div>
      <div style={{
        fontSize: 17, fontWeight: 700, fontFamily: "monospace",
        color: fg, marginTop: 3,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
      }}>
        {value == null ? "n/a" : fmtSigned(value, ccy, signed)}
      </div>
    </div>
  );
}

/** Merge cash-summary (value + openPnl) with per-strategy realised P&L,
 *  aggregating realised per (broker, currency). Drops disabled brokers
 *  that report no usable figures so the strip stays clean. */
function merge(cash: CashRow[], pnl: PnlRow[]): BrokerKpis[] {
  // Sum realised today / LTD per broker (case-insensitive broker key).
  const realised = new Map<string, { today: number | null; ltd: number | null; ccy: string }>();
  for (const r of pnl) {
    const key = (r.broker || "").toLowerCase();
    if (!key) continue;
    const cur = realised.get(key) ?? { today: null, ltd: null, ccy: r.currency };
    if (r.realisedToday != null) cur.today = (cur.today ?? 0) + r.realisedToday;
    if (r.realisedLtd != null) cur.ltd = (cur.ltd ?? 0) + r.realisedLtd;
    realised.set(key, cur);
  }

  return cash
    .filter((c) => c.status !== "disabled")
    .map((c) => {
      const key = (c.broker || "").toLowerCase();
      const rl = realised.get(key);
      return {
        broker: brokerCode(c.broker, c.label),
        label: c.label || c.broker,
        currency: c.currency ?? rl?.ccy ?? "",
        status: c.status,
        value: c.total ?? c.balance ?? null,
        openPnl: c.openPnl ?? null,
        realisedToday: rl?.today ?? null,
        realisedLtd: rl?.ltd ?? null,
      };
    });
}

/** Short, uppercase broker code for the tile label (T212 / IG / IBKR). */
function brokerCode(broker: string, label: string): string {
  const b = (broker || label || "").toLowerCase();
  if (b.includes("212") || b.includes("t212") || b.includes("trading")) return "T212";
  if (b.includes("ibkr") || b.includes("interactive")) return "IBKR";
  if (b.includes("ig")) return "IG";
  return (broker || label || "?").toUpperCase().slice(0, 6);
}

function statusColour(s: CashRow["status"]): string {
  switch (s) {
    case "ok": return "var(--up, #1fc16b)";
    case "degraded": return "var(--neutral, #f59e0b)";
    case "down": return "var(--down, #ef4444)";
    case "disabled": return "var(--text-muted)";
    default: return "var(--border)";
  }
}

function fmtSigned(n: number, ccy: string, signed: boolean): string {
  const abs = Math.abs(n) >= 1e6
    ? Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = signed ? (n >= 0 ? "+" : "−") : (n < 0 ? "−" : "");
  return `${sign}${ccy ? ccy + " " : ""}${abs}`;
}
