/**
 * TradePlanCard — "what trades would ship if I approved today's plan."
 *
 * Reads /api/trade-plan/{strategy} which derives the diff live from:
 *   • Latest live-portfolio run (slow loop's output)
 *   • Current broker positions (T212 cached)
 *   • Portfolio cash
 *
 * Today-only by design — there's no historical trade-plan endpoint;
 * past plans are reconstructible from oms_orders + strategy_decisions
 * if needed.
 *
 * Renders:
 *   - Summary band: regime · portfolio value · n buys/sells · gross flow %
 *   - Trade table: per-intent { sleeve, symbol, side, qty, target vs
 *     current, $diff, reason }. Sorted by |diff| descending so the
 *     biggest reshuffles surface first.
 *   - Risk class column will fill in once RiskGate's multi-indicator
 *     vetoes are wired (risk module Phase 2 follow-up).
 *
 * No approve button yet — orders flow through the existing OMS
 * PENDING_APPROVAL path; the trader approves each on /oms. Two-layer
 * safety stays in place.
 */
import { useCallback, useEffect, useState } from "react";
import { CockpitCard } from "./CockpitCard";
import { config } from "../config";

interface Intent {
  sleeve: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  targetNotional: number;
  currentNotional: number;
  diffNotional: number;
  riskClass: string | null;
  reason: string;
  priceUnavailable: boolean;
}

interface PlanEnvelope {
  strategy: string;
  hasPlan: boolean;
  noPlanReason?: string;
  runId?: string;
  asOfUtc?: string;
  regimeState?: string;
  portfolioValueUsd?: number;
  summary?: {
    nBuys: number;
    nSells: number;
    nIntents: number;
    nSkipped: number;
    netFlow: number;
    grossFlow: number;
    grossFlowPct: number;
  };
  intents?: Intent[];
}

interface Props {
  strategy: string;
}

export function TradePlanCard({ strategy }: Props) {
  const [env, setEnv] = useState<PlanEnvelope | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const resp = await fetch(
        `${config.apiBaseUrl}/api/trade-plan/${encodeURIComponent(strategy)}`,
      );
      if (!resp.ok) {
        setErr(`HTTP ${resp.status}`);
        setEnv(null);
        return;
      }
      const data = (await resp.json()) as PlanEnvelope;
      setEnv(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [strategy]);

  useEffect(() => { void load(); }, [load]);

  if (loading) {
    return (
      <CockpitCard id="trade-plan" title="Today's trade plan">
        <div style={{ padding: 12, color: "var(--text-muted)", fontSize: 13 }}>
          Loading…
        </div>
      </CockpitCard>
    );
  }
  if (err) {
    return (
      <CockpitCard id="trade-plan" title="Today's trade plan">
        <div style={{ padding: 12, color: "var(--down)", fontSize: 12 }}>
          {err}
        </div>
      </CockpitCard>
    );
  }
  if (!env || !env.hasPlan) {
    return (
      <CockpitCard id="trade-plan" title="Today's trade plan">
        <div style={{
          padding: 14, fontSize: 13, color: "var(--text-dim)",
          background: "rgba(245,158,11,0.06)",
          border: "1px solid rgba(245,158,11,0.20)",
          borderRadius: 8,
        }}>
          <strong style={{ color: "#f59e0b" }}>No plan available.</strong>
          <span style={{ marginLeft: 8 }}>{env?.noPlanReason ?? "Run the slow loop on the worker host: "}</span>
          {env?.noPlanReason ? null : <code>tradepro-live-portfolio --push</code>}
        </div>
      </CockpitCard>
    );
  }

  const s = env.summary!;
  const intents = env.intents ?? [];
  const buys = intents.filter((i) => i.side === "BUY");
  const sells = intents.filter((i) => i.side === "SELL");

  return (
    <CockpitCard
      id="trade-plan"
      title="Today's trade plan"
      badge={intents.length || undefined}
      fullWidth
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {/* Summary band */}
        <div style={{
          display: "flex", flexWrap: "wrap", gap: 14,
          fontSize: 12, color: "var(--text-dim)",
          paddingBottom: 8, borderBottom: "1px solid var(--border)",
        }}>
          <Stat label="run" value={env.asOfUtc ? new Date(env.asOfUtc).toLocaleString() : "—"} mono />
          <Stat label="regime" value={env.regimeState ?? "?"}
            valueColor={env.regimeState === "bull" ? "#1fc16b" : env.regimeState === "bear" ? "#ef4444" : "var(--text)"} />
          <Stat label="portfolio" value={env.portfolioValueUsd?.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}
            valuePrefix="$" mono />
          <Stat label="BUYs" value={s.nBuys.toString()} valueColor="#1fc16b" />
          <Stat label="SELLs" value={s.nSells.toString()} valueColor="#ef4444" />
          <Stat label="gross flow" value={`${s.grossFlowPct.toFixed(1)}%`} mono />
          <Stat label="net flow" value={`$${s.netFlow.toFixed(0)}`} mono
            valueColor={s.netFlow >= 0 ? "#1fc16b" : "#ef4444"} />
          <Stat label="skipped (<$50)" value={s.nSkipped.toString()} valueColor="var(--text-muted)" />
        </div>

        {intents.length === 0 ? (
          <div style={{ padding: 14, fontSize: 13, color: "var(--text-dim)" }}>
            No trades required — current holdings already match the algo's target.
          </div>
        ) : (
          <>
            <IntentSection title={`BUY (${buys.length})`} color="#1fc16b" intents={buys} />
            <IntentSection title={`SELL (${sells.length})`} color="#ef4444" intents={sells} />
          </>
        )}
      </div>
    </CockpitCard>
  );
}

function Stat({
  label, value, mono, valueColor, valuePrefix,
}: {
  label: string;
  value?: string;
  mono?: boolean;
  valueColor?: string;
  valuePrefix?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <span style={{
        fontSize: 9, textTransform: "uppercase", letterSpacing: "0.06em",
        color: "var(--text-muted)",
      }}>{label}</span>
      <span style={{
        fontSize: 13, fontWeight: 600,
        fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
        color: valueColor ?? "var(--text)",
      }}>
        {valuePrefix}{value ?? "—"}
      </span>
    </div>
  );
}

function IntentSection({
  title, color, intents,
}: {
  title: string;
  color: string;
  intents: Intent[];
}) {
  if (intents.length === 0) return null;
  const sorted = [...intents].sort((a, b) => Math.abs(b.diffNotional) - Math.abs(a.diffNotional));
  return (
    <div>
      <div style={{
        fontSize: 11, fontWeight: 700, color, textTransform: "uppercase",
        letterSpacing: "0.06em", marginBottom: 4,
      }}>{title}</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ color: "var(--text-muted)" }}>
            <Th>Symbol</Th>
            <Th>Sleeve</Th>
            <Th right>Qty</Th>
            <Th right>Price</Th>
            <Th right>Current $</Th>
            <Th right>Target $</Th>
            <Th right>Diff $</Th>
            <Th>Risk</Th>
            <Th>Reason</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((i) => (
            <tr key={`${i.symbol}.${i.sleeve}`} style={{ borderTop: "1px solid var(--border)" }}>
              <Td><strong>{i.symbol}</strong></Td>
              <Td small>{i.sleeve}</Td>
              <Td right mono>{i.qty.toFixed(2)}</Td>
              <Td right mono>{i.priceUnavailable ? "—" : `$${i.price.toFixed(2)}`}</Td>
              <Td right mono>${Math.round(i.currentNotional).toLocaleString()}</Td>
              <Td right mono>${Math.round(i.targetNotional).toLocaleString()}</Td>
              <Td right mono style={{ color }}>${Math.round(i.diffNotional).toLocaleString()}</Td>
              <Td>{i.riskClass ? <RiskPill cls={i.riskClass} /> : <span style={{ color: "var(--text-muted)" }}>—</span>}</Td>
              <Td small>{i.reason}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th style={{
      padding: "4px 8px", textAlign: right ? "right" : "left",
      fontWeight: 600, fontSize: 10,
      textTransform: "uppercase", letterSpacing: "0.06em",
    }}>{children}</th>
  );
}

function Td({
  children, right, mono, small, style,
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  small?: boolean;
  style?: React.CSSProperties;
}) {
  return (
    <td style={{
      padding: "4px 8px",
      textAlign: right ? "right" : "left",
      fontFamily: mono ? "ui-monospace, Menlo, monospace" : undefined,
      fontSize: small ? 11 : 12,
      color: small ? "var(--text-dim)" : "var(--text)",
      ...style,
    }}>{children}</td>
  );
}

function RiskPill({ cls }: { cls: string }) {
  const color = cls === "LOW" ? "#1fc16b"
    : cls === "MEDIUM" ? "#f59e0b"
    : cls === "HIGH" ? "#ef4444"
    : cls === "EXTREME" ? "#7f1d1d"
    : "var(--text-muted)";
  return (
    <span style={{
      display: "inline-block", padding: "1px 6px", borderRadius: 999,
      background: `${color}22`, color, fontSize: 10, fontWeight: 700,
      letterSpacing: "0.04em",
    }}>{cls}</span>
  );
}
