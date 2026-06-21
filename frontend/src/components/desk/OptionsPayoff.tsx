import { useMemo, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

/**
 * Options payoff explorer — adjust strike / premium / contracts / spot and see
 * the expiry P&L curve with max gain, max loss, breakeven, capital at risk and
 * the premium yield (raw + annualised). Covers the two wheel legs:
 *   • CASH_SECURED_PUT — short put, cash set aside to buy 100×strike.
 *   • COVERED_CALL     — long 100 shares @ cost basis, short call @ strike.
 *
 * Everything is computed locally from the inputs (no server round-trip), so the
 * trader can dry-run "what if I roll to a lower strike / further expiry" before
 * recording a paper position. £ figures use the BRD display FX (USD→GBP).
 */
const FX_GBPUSD = 1.27;
const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)", line: "#4C9AFF" };

type Structure = "CASH_SECURED_PUT" | "COVERED_CALL";

export interface PayoffSeed {
  symbol?: string;
  structure?: Structure;
  spot?: number | null;
  strike?: number | null;
  premium?: number | null;
  contracts?: number | null;
  dte?: number | null;
}

export function OptionsPayoff({ seed }: { seed?: PayoffSeed }) {
  const [structure, setStructure] = useState<Structure>(seed?.structure ?? "CASH_SECURED_PUT");
  const [symbol, setSymbol] = useState(seed?.symbol ?? "");
  const [spot, setSpot] = useState(num(seed?.spot, 100));
  const [strike, setStrike] = useState(num(seed?.strike, 95));
  const [premium, setPremium] = useState(num(seed?.premium, 1.5));
  const [contracts, setContracts] = useState(num(seed?.contracts, 1));
  const [costBasis, setCostBasis] = useState(num(seed?.strike, 95)); // CC: assigned cost basis
  const [dte, setDte] = useState(num(seed?.dte, 35));

  // re-seed when a candidate is loaded from the screen
  const seedKey = `${seed?.symbol}|${seed?.strike}|${seed?.premium}|${seed?.structure}`;
  const [appliedKey, setAppliedKey] = useState("");
  if (seed?.symbol && seedKey !== appliedKey) {
    setAppliedKey(seedKey);
    if (seed.structure) setStructure(seed.structure);
    setSymbol(seed.symbol);
    if (seed.spot != null) setSpot(seed.spot);
    if (seed.strike != null) { setStrike(seed.strike); setCostBasis(seed.strike); }
    if (seed.premium != null) setPremium(seed.premium);
    if (seed.contracts != null) setContracts(seed.contracts);
    if (seed.dte != null) setDte(seed.dte);
  }

  const m = useMemo(() => {
    const mult = 100 * Math.max(1, contracts);
    const isPut = structure === "CASH_SECURED_PUT";
    // per-share P&L at expiry
    const pnlShare = (S: number) =>
      isPut
        ? premium - Math.max(0, strike - S)
        : Math.min(S, strike) - costBasis + premium;
    const maxGainShare = isPut ? premium : strike - costBasis + premium;
    const maxLossShare = isPut ? strike - premium : costBasis - premium; // at S=0
    const breakeven = isPut ? strike - premium : costBasis - premium;
    const capitalUsd = (isPut ? strike : costBasis) * mult; // cash secured / shares cost
    const maxGainUsd = maxGainShare * mult;
    const maxLossUsd = maxLossShare * mult;
    const roc = capitalUsd > 0 ? maxGainUsd / capitalUsd : 0; // premium yield on capital
    const annualised = dte > 0 ? roc * (365 / dte) : 0;

    const lo = Math.max(0, breakeven * 0.6, strike * 0.6);
    const hi = strike * 1.4;
    const n = 61;
    const curve = Array.from({ length: n }, (_, i) => {
      const S = lo + ((hi - lo) * i) / (n - 1);
      const pnlGbp = (pnlShare(S) * mult) / FX_GBPUSD;
      return { S: round2(S), gain: pnlGbp >= 0 ? pnlGbp : 0, loss: pnlGbp < 0 ? pnlGbp : 0, pnl: pnlGbp };
    });
    return {
      isPut, mult, breakeven, capitalUsd, maxGainUsd, maxLossUsd, roc, annualised, curve, lo, hi,
      maxGainGbp: maxGainUsd / FX_GBPUSD, maxLossGbp: maxLossUsd / FX_GBPUSD, capitalGbp: capitalUsd / FX_GBPUSD,
    };
  }, [structure, spot, strike, premium, contracts, costBasis, dte]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 14, background: "var(--surface-2)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>
          Payoff explorer {symbol ? <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>· {symbol}</span> : null}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {(["CASH_SECURED_PUT", "COVERED_CALL"] as Structure[]).map((s) => (
            <button key={s} onClick={() => setStructure(s)} style={pill(structure === s)}>
              {s === "CASH_SECURED_PUT" ? "Cash-secured put" : "Covered call"}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Num label="Symbol" text v={symbol} on={(x) => setSymbol(x.toUpperCase())} w={70} />
        <Num label="Spot $" v={spot} on={(x) => setSpot(Number(x) || 0)} w={70} />
        <Num label={m.isPut ? "Strike $" : "Call strike $"} v={strike} on={(x) => setStrike(Number(x) || 0)} w={80} />
        {!m.isPut && <Num label="Cost basis $" v={costBasis} on={(x) => setCostBasis(Number(x) || 0)} w={90} />}
        <Num label="Premium $" v={premium} on={(x) => setPremium(Number(x) || 0)} w={80} />
        <Num label="Contracts" v={contracts} on={(x) => setContracts(Math.max(1, Math.round(Number(x) || 1)))} w={70} />
        <Num label="DTE" v={dte} on={(x) => setDte(Math.max(1, Math.round(Number(x) || 1)))} w={60} />
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Metric label="Max gain" v={`£${fmt(m.maxGainGbp)}`} tone="ok" sub={`$${fmt(m.maxGainUsd)} · premium`} />
        <Metric label="Max loss" v={`£${fmt(m.maxLossGbp)}`} tone="bad" sub={m.isPut ? "if stock → £0" : "if stock → £0"} />
        <Metric label="Breakeven" v={`$${round2(m.breakeven)}`} tone="dim" sub={m.isPut ? `${pct((spot - m.breakeven) / spot)} below spot` : "below cost basis"} />
        <Metric label="Capital at risk" v={`£${fmt(m.capitalGbp)}`} tone="warn" sub={m.isPut ? "cash secured" : "shares held"} />
        <Metric label="Premium yield" v={pct(m.roc)} tone="ok" sub={`${pct(m.annualised)} annualised`} />
      </div>

      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={m.curve} margin={{ top: 6, right: 12, bottom: 4, left: 0 }}>
            <defs>
              <linearGradient id="po-gain" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={TONE.ok} stopOpacity={0.5} />
                <stop offset="100%" stopColor={TONE.ok} stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="po-loss" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={TONE.bad} stopOpacity={0.05} />
                <stop offset="100%" stopColor={TONE.bad} stopOpacity={0.5} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1c2740" />
            <XAxis dataKey="S" type="number" domain={[round2(m.lo), round2(m.hi)]} tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              tickFormatter={(v) => `$${Math.round(v)}`} />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickFormatter={(v) => `£${Math.round(v)}`} width={48} />
            <Tooltip
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
              formatter={(v: number) => [`£${fmt(v)}`, "P&L at expiry"]}
              labelFormatter={(v) => `underlying $${v}`}
            />
            <ReferenceLine y={0} stroke="var(--text-muted)" />
            <ReferenceLine x={round2(m.breakeven)} stroke={TONE.warn} strokeDasharray="4 3"
              label={{ value: "BE", fill: TONE.warn, fontSize: 10, position: "top" }} />
            <ReferenceLine x={round2(spot)} stroke={TONE.line} strokeDasharray="2 2"
              label={{ value: "spot", fill: TONE.line, fontSize: 10, position: "insideTopRight" }} />
            <Area type="monotone" dataKey="gain" stroke={TONE.ok} strokeWidth={2} fill="url(#po-gain)" isAnimationActive={false} />
            <Area type="monotone" dataKey="loss" stroke={TONE.bad} strokeWidth={2} fill="url(#po-loss)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
        {m.isPut
          ? <>Short put: you keep the <b>£{fmt(m.maxGainGbp)}</b> premium if {symbol || "the stock"} stays above <b>${round2(strike)}</b> at expiry. Below breakeven <b>${round2(m.breakeven)}</b> you're assigned and start losing — worst case <b>£{fmt(m.maxLossGbp)}</b> if it goes to zero. That's the wheel's entry: get paid to set a buy limit.</>
          : <>Covered call: you own 100 shares @ <b>${round2(costBasis)}</b> and sold the <b>${round2(strike)}</b> call. Capped upside <b>£{fmt(m.maxGainGbp)}</b> (called away above strike), downside is the stock minus the <b>£{fmt(premium * m.mult / FX_GBPUSD)}</b> premium cushion.</>}
      </div>
    </div>
  );
}

function num(v: number | null | undefined, dflt: number): number {
  return v == null || Number.isNaN(v) ? dflt : v;
}
function round2(v: number) { return Math.round(v * 100) / 100; }
function fmt(v: number) { return Math.round(v).toLocaleString(); }
function pct(v: number) { return `${(v * 100).toFixed(1)}%`; }

function Num({ label, v, on, w, text }: { label: string; v: string | number; on: (x: string) => void; w: number; text?: boolean }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
      {label}
      <input
        value={v} onChange={(e) => on(e.target.value)} inputMode={text ? undefined : "decimal"}
        style={{ width: w, padding: "5px 7px", fontSize: 12, borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontFamily: text ? "inherit" : "var(--font-mono)" }}
      />
    </label>
  );
}

function Metric({ label, v, tone, sub }: { label: string; v: string; tone: keyof typeof TONE; sub?: string }) {
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "7px 12px", minWidth: 104 }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "var(--font-mono)", color: TONE[tone] }}>{v}</div>
      {sub && <div style={{ fontSize: 9.5, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function pill(active: boolean): React.CSSProperties {
  return {
    padding: "5px 10px", fontSize: 11, fontWeight: 600, borderRadius: 6,
    border: `1px solid ${active ? TONE.line : "var(--border)"}`,
    background: active ? `${TONE.line}1f` : "transparent",
    color: active ? TONE.line : "var(--text-muted)", cursor: "pointer", whiteSpace: "nowrap",
  };
}
