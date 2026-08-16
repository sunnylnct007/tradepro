import { useMemo, useState } from "react";
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { bsPrice, greeks, impliedVol, probAbove, type OptType } from "../../util/optionsMath";

/**
 * Options payoff explorer (Sensibull-style). Adjust structure / spot / strike /
 * premium / contracts / DTE and read the full trade picture:
 *   • dual payoff curves — solid "at expiry" (green/red) + dashed "now (T+0)";
 *   • max gain / max loss / breakeven / capital / premium yield;
 *   • probability of profit (risk-neutral) + position greeks (Δ Θ Γ vega);
 *   • a draggable TARGET price → projected P&L now and at expiry.
 * Covers both wheel legs (cash-secured put, covered call). All client-side, so
 * the trader can dry-run a roll before recording. £ figures use the BRD FX.
 */
const FX_GBPUSD = 1.27;
const R = 0.04; // flat risk-free for BS
const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)", line: "#4C9AFF", now: "#B98CFF" };

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

export interface PayoffPlacement {
  symbol: string;
  structure: Structure;
  strike: number;
  premium: number;
  contracts: number;
  dte: number;
  breakevenUsd: number;
  maxGainGbp: number;
  maxLossGbp: number;
  capitalGbp: number;
  pop: number;
  iv: number | null;
}

export function OptionsPayoff({ seed, onPlace, placing, chartHeight = 240, shareSymbols = [] }: {
  seed?: PayoffSeed; onPlace?: (p: PayoffPlacement) => void; placing?: boolean;
  // Taller chart when hosted in the analyze modal — same math, more zoom.
  chartHeight?: number;
  /** Symbols where the ledger says we actually HOLD SHARES — i.e. positions in
   *  state ASSIGNED or COVERED_CALL_OPEN. A covered call is only a real trade
   *  against stock you own; without this the explorer happily models one on a
   *  name you have no position in, which is not an order you could place.
   *  An open SHORT_PUT does NOT count — that is the obligation to buy, not the
   *  stock. */
  shareSymbols?: string[];
}) {
  const [structure, setStructure] = useState<Structure>(seed?.structure ?? "CASH_SECURED_PUT");
  const [symbol, setSymbol] = useState(seed?.symbol ?? "");
  const [spot, setSpot] = useState(num(seed?.spot, 100));
  const [strike, setStrike] = useState(num(seed?.strike, 95));
  const [premium, setPremium] = useState(num(seed?.premium, 1.5));
  const [contracts, setContracts] = useState(num(seed?.contracts, 1));
  const [costBasis, setCostBasis] = useState(num(seed?.strike, 95)); // CC: assigned cost basis
  // Tracks the EDITED symbol field, not just the seed — the explorer lets you
  // retype the ticker, and the covered-call prerequisite has to follow it.
  const holdsShares = useMemo(
    () => shareSymbols.some((s) => s.toUpperCase() === symbol.toUpperCase()),
    [shareSymbols, symbol]);
  const [dte, setDte] = useState(num(seed?.dte, 35));
  const [target, setTarget] = useState<number | null>(null);

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
    setTarget(null);
  }

  const m = useMemo(() => {
    const mult = 100 * Math.max(1, contracts);
    const isPut = structure === "CASH_SECURED_PUT";
    const optType: OptType = isPut ? "put" : "call";
    const t = Math.max(dte, 0) / 365;
    const K = strike;

    // Implied vol from the quoted premium (falls back to 30% so curves still draw).
    const ivSolved = impliedVol(optType, premium, spot, K, t, R);
    const vol = ivSolved ?? 0.30;

    // per-share expiry P&L
    const pnlShareExp = (S: number) =>
      isPut ? premium - Math.max(0, K - S) : Math.min(S, K) - costBasis + premium;
    // per-share P&L "now" (full time left): premium kept − cost to buy back option
    const pnlShareNow = (S: number) =>
      isPut
        ? premium - bsPrice("put", S, K, t, vol, R)
        : (S - costBasis) + (premium - bsPrice("call", S, K, t, vol, R));

    const maxGainShare = isPut ? premium : K - costBasis + premium;
    const maxLossShare = isPut ? K - premium : costBasis - premium; // at S=0
    const breakeven = isPut ? K - premium : costBasis - premium;
    const capitalUsd = (isPut ? K : costBasis) * mult;
    const maxGainUsd = maxGainShare * mult;
    const maxLossUsd = maxLossShare * mult;
    const roc = capitalUsd > 0 ? maxGainUsd / capitalUsd : 0;
    const annualised = dte > 0 ? roc * (365 / dte) : 0;
    const pop = probAbove(spot, breakeven, t, vol, R); // P(profit at expiry)

    // Position greeks (signs for THIS position; ×100×contracts; £).
    const g = greeks(optType, spot, K, t, vol, R);
    const posDelta = isPut ? -g.delta * mult : (1 - g.delta) * mult; // shares-equivalent
    const posTheta = (isPut ? -g.theta : -g.theta) * mult / FX_GBPUSD; // £/day (short option earns)
    const posVega = (isPut ? -g.vega : -g.vega) * mult / FX_GBPUSD;    // £ per 1 vol-pt (short vega)
    const posGamma = (isPut ? -g.gamma : -g.gamma) * mult;             // Δ per $1

    const lo = Math.max(0, Math.min(breakeven, strike, spot) * 0.7);
    const hi = Math.max(strike, spot) * 1.3;
    const n = 73;
    const curve = Array.from({ length: n }, (_, i) => {
      const S = lo + ((hi - lo) * i) / (n - 1);
      const exp = (pnlShareExp(S) * mult) / FX_GBPUSD;
      const now = (pnlShareNow(S) * mult) / FX_GBPUSD;
      return { S: round2(S), gain: exp >= 0 ? exp : 0, loss: exp < 0 ? exp : 0, exp, now };
    });

    const tgt = target ?? spot;
    const tgtExp = (pnlShareExp(tgt) * mult) / FX_GBPUSD;
    const tgtNow = (pnlShareNow(tgt) * mult) / FX_GBPUSD;

    return {
      isPut, mult, breakeven, capitalUsd, maxGainUsd, maxLossUsd, roc, annualised, pop, vol, ivSolved,
      curve, lo, hi, posDelta, posTheta, posVega, posGamma, tgt, tgtExp, tgtNow,
      maxGainGbp: maxGainUsd / FX_GBPUSD, maxLossGbp: maxLossUsd / FX_GBPUSD, capitalGbp: capitalUsd / FX_GBPUSD,
    };
  }, [structure, spot, strike, premium, contracts, costBasis, dte, target]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 14, background: "var(--surface-2)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>
          Payoff explorer {symbol ? <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>· {symbol}</span> : null}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {(["CASH_SECURED_PUT", "COVERED_CALL"] as Structure[]).map((s) => (
            <button key={s} onClick={() => setStructure(s)} style={pill(structure === s)}
              title={s === "COVERED_CALL"
                ? "Selling calls against shares you ALREADY OWN. In the wheel you only reach this after a cash-secured put is assigned — so it is not available on a name you have no stock in."
                : "Selling a put with the cash to buy the shares set aside. This is the wheel's entry: you get paid to set a buy limit."}
            >
              {s === "CASH_SECURED_PUT" ? "Cash-secured put" : "Covered call"}
            </button>
          ))}
        </div>
      </div>

      {/* A covered call on stock you do not own is not a trade — it is a naked
          call, which is a different (and unbounded-risk) position entirely. Say
          so plainly rather than rendering a payoff that implies otherwise. */}
      {!m.isPut && symbol && !holdsShares && (
        <div style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 6, fontSize: 12,
                      border: "1px solid var(--warn)", color: "var(--warn)",
                      background: "color-mix(in srgb, var(--warn) 10%, transparent)" }}>
          <b>The PAPER LEDGER shows no {symbol} shares.</b> A covered call requires owning
          100 shares per contract — sold without them it is a <b>naked call</b>, unbounded
          loss, and not what this payoff draws.
          {shareSymbols.length > 0
            ? <> Recorded here: <b>{shareSymbols.join(", ")}</b>.</>
            : <> Nothing is recorded here as assigned.</>}
          {" "}
          {/* Corrected 16 Aug 2026: this used to assert "you do not hold X" as
              fact. It reads the PAPER LEDGER, and the ledger is not the book —
              the live IBKR account that day held 100 APLD and 100 PG with calls
              already written against both, none of it recorded here. Asserting
              a broker fact from a paper record is exactly the false-positive
              class the standing rule forbids. */}
          <b>This reflects the paper ledger, not your broker account</b> — if you hold the
          shares there, this warning does not apply.
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Num label="Symbol" text v={symbol} on={(x) => setSymbol(x.toUpperCase())} w={70} />
        <Num label="Spot $" v={spot} on={(x) => setSpot(Number(x) || 0)} w={70} />
        <Num label={m.isPut ? "Strike $" : "Call strike $"} v={strike} on={(x) => setStrike(Number(x) || 0)} w={80} />
        {!m.isPut && <Num label="Cost basis $" v={costBasis} on={(x) => setCostBasis(Number(x) || 0)} w={90} />}
        <Num label="Premium $" v={premium} on={(x) => setPremium(Number(x) || 0)} w={80} />
        <Num label="Contracts" v={contracts} on={(x) => setContracts(Math.max(1, Math.round(Number(x) || 1)))} w={70} />
        <Num label="DTE" v={dte} on={(x) => setDte(Math.max(1, Math.round(Number(x) || 1)))} w={60} />
      </div>

      {/* No symbol = no chart. Rendering the default placeholder numbers
          ($100/$95/$1.5) as a full payoff read as REAL analysis of nothing —
          the exact fabricated-output pattern the desk bans. */}
      {!symbol.trim() ? (
      <div style={{ padding: "28px 16px", textAlign: "center", color: "var(--text-muted)", fontSize: 12, lineHeight: 1.6 }}>
        Nothing to chart yet — click <b>Analyze</b> on a candidate row above to load its real strike,
        premium and DTE here, or type a symbol and the actual quote values by hand.
        No payoff is drawn until the numbers are real.
      </div>
      ) : (<>
      {/* headline stats */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        <Metric label="Max gain" v={`£${fmt(m.maxGainGbp)}`} tone="ok" sub="premium kept" tip="Most you can make: the full premium (CSP) / strike−cost+premium (CC)." />
        <Metric label="Max loss" v={`£${fmt(m.maxLossGbp)}`} tone="bad" sub="if stock → £0" tip="Worst case if the underlying goes to zero (cash-secured, so it's capped at this)." />
        <Metric label="Breakeven" v={`$${round2(m.breakeven)}`} tone="dim" sub={`${pct((spot - m.breakeven) / spot)} below spot`} tip="Underlying price at expiry where P&L = 0 (strike − premium)." />
        <Metric label="Prob. of profit" v={pct(m.pop)} tone={m.pop >= 0.7 ? "ok" : "warn"} sub="risk-neutral" tip="Risk-neutral probability the underlying finishes above breakeven at expiry. Approximate; ignores dividends." />
        <Metric label="Premium yield" v={pct(m.roc)} tone="ok" sub={`${pct(m.annualised)} annualised`} tip="Premium ÷ capital at risk, then scaled to a year by DTE." />
        <Metric label="Capital" v={`£${fmt(m.capitalGbp)}`} tone="warn" sub={m.isPut ? "cash secured" : "shares held"} tip="Cash you set aside (CSP = strike×100) or shares value (CC)." />
      </div>

      {/* greeks strip */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
        <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginRight: 2 }}>Position greeks</span>
        <Greek label="Δ delta" v={signed(m.posDelta, 0)} tip="Share-equivalent exposure: how the position value moves per $1 in the underlying. Positive = bullish." />
        <Greek label="Θ theta" v={`£${signed(m.posTheta, 1)}/day`} tone={m.posTheta >= 0 ? "ok" : "bad"} tip="Daily time-decay P&L. Selling premium, this should be positive — you earn as time passes." />
        <Greek label="vega" v={`£${signed(m.posVega, 0)}`} tip="P&L per 1-point change in implied volatility. Short premium = negative vega (you want IV to fall)." />
        <Greek label="Γ gamma" v={signed(m.posGamma, 2)} tip="How fast delta changes per $1 move. Short options = negative gamma." />
        <Greek label="IV" v={m.ivSolved != null ? pct(m.ivSolved) : "n/a"} tone={m.ivSolved != null ? "dim" : "warn"} tip={m.ivSolved != null ? "Implied vol solved from the premium you entered." : "Couldn't solve IV from this premium; curves use a 30% fallback."} />
      </div>

      {/* dual payoff chart */}
      <div style={{ height: chartHeight }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={m.curve} margin={{ top: 6, right: 12, bottom: 4, left: 0 }}>
            <defs>
              <linearGradient id="po-gain" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={TONE.ok} stopOpacity={0.45} />
                <stop offset="100%" stopColor={TONE.ok} stopOpacity={0.04} />
              </linearGradient>
              <linearGradient id="po-loss" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={TONE.bad} stopOpacity={0.04} />
                <stop offset="100%" stopColor={TONE.bad} stopOpacity={0.45} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1c2740" />
            <XAxis dataKey="S" type="number" domain={[round2(m.lo), round2(m.hi)]} tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              tickFormatter={(v) => `$${Math.round(v)}`} />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickFormatter={(v) => `£${Math.round(v)}`} width={48} />
            <Tooltip
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
              // The payoff is split into `gain`/`loss` so the fill can be two
              // colours. Both used to render as "P&L at expiry", so a single
              // point showed that label TWICE with different numbers (the MRVL
              // report: £717 and £0) and read as a contradiction. One curve, two
              // halves — name them accordingly.
              formatter={(v: number, name: string) => [
                `£${fmt(v)}`,
                name === "now" ? "P&L now (T+0)"
                  : name === "gain" ? "profit at expiry"
                  : "loss at expiry",
              ]}
              labelFormatter={(v) => `underlying $${v}`}
            />
            <ReferenceLine y={0} stroke="var(--text-muted)" />
            <ReferenceLine x={round2(m.breakeven)} stroke={TONE.warn} strokeDasharray="4 3"
              label={{ value: "BE", fill: TONE.warn, fontSize: 10, position: "top" }} />
            <ReferenceLine x={round2(spot)} stroke={TONE.line} strokeDasharray="2 2"
              label={{ value: "spot", fill: TONE.line, fontSize: 10, position: "insideTopRight" }} />
            {target != null && (
              <ReferenceLine x={round2(target)} stroke={TONE.now}
                label={{ value: "target", fill: TONE.now, fontSize: 10, position: "insideTopLeft" }} />
            )}
            <Area type="monotone" dataKey="gain" stroke={TONE.ok} strokeWidth={2} fill="url(#po-gain)" isAnimationActive={false} />
            <Area type="monotone" dataKey="loss" stroke={TONE.bad} strokeWidth={2} fill="url(#po-loss)" isAnimationActive={false} />
            <Line type="monotone" dataKey="now" stroke={TONE.now} strokeWidth={1.6} strokeDasharray="5 3" dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div style={{ display: "flex", gap: 14, fontSize: 10, color: "var(--text-muted)", marginTop: 2, marginBottom: 8 }}>
        <Legend color={TONE.ok} label="profit at expiry" />
        <Legend color={TONE.bad} label="loss at expiry" />
        <Legend color={TONE.now} label="P&L now (T+0)" dashed />
      </div>

      {/* target slider */}
      <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6, flexWrap: "wrap", gap: 8 }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            Target underlying <b style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>${round2(m.tgt)}</b>
            {target == null ? <span style={{ color: TONE.dim }}> (drag to project)</span> : null}
          </span>
          <span style={{ fontSize: 12 }}>
            <span style={{ color: "var(--text-muted)" }}>at expiry </span>
            <b style={{ color: m.tgtExp >= 0 ? TONE.ok : TONE.bad, fontFamily: "var(--font-mono)" }}>£{fmt(m.tgtExp)}</b>
            <span style={{ color: "var(--text-muted)" }}> · now </span>
            <b style={{ color: m.tgtNow >= 0 ? TONE.ok : TONE.bad, fontFamily: "var(--font-mono)" }}>£{fmt(m.tgtNow)}</b>
          </span>
        </div>
        <input
          type="range" min={round2(m.lo)} max={round2(m.hi)} step={Math.max(0.5, round2((m.hi - m.lo) / 100))}
          value={m.tgt} onChange={(e) => setTarget(Number(e.target.value))}
          style={{ width: "100%", accentColor: TONE.now }}
        />
      </div>

      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 10, lineHeight: 1.5 }}>
        {m.isPut
          ? <>Short put: keep the <b>£{fmt(m.maxGainGbp)}</b> premium if {symbol || "the stock"} stays above <b>${round2(strike)}</b> at expiry (<b>{pct(m.pop)}</b> chance). Below breakeven <b>${round2(m.breakeven)}</b> you're assigned and start losing — worst case <b>£{fmt(m.maxLossGbp)}</b> at zero. The wheel's entry: get paid to set a buy limit.</>
          : <>Covered call: own 100 shares @ <b>${round2(costBasis)}</b>, sold the <b>${round2(strike)}</b> call. Upside capped at <b>£{fmt(m.maxGainGbp)}</b> (called away above strike); downside is the stock minus the <b>£{fmt(premium * m.mult / FX_GBPUSD)}</b> premium cushion.</>}
      </div>
      </>)}

      {onPlace && (
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 10, marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {symbol && strike > 0 && premium > 0
              ? `Place this ${m.isPut ? "CSP" : "covered call"} on the paper ledger`
              : "Enter symbol, strike and premium to place"}
          </span>
          <button
            disabled={placing || !symbol || !(strike > 0) || !(premium > 0)}
            onClick={() => onPlace({
              symbol, structure, strike, premium, contracts: Math.max(1, contracts), dte,
              breakevenUsd: round2(m.breakeven), maxGainGbp: Math.round(m.maxGainGbp),
              maxLossGbp: Math.round(m.maxLossGbp), capitalGbp: Math.round(m.capitalGbp),
              pop: Math.round(m.pop * 1000) / 1000, iv: m.ivSolved,
            })}
            style={placeBtn(!placing && !!symbol && strike > 0 && premium > 0)}
          >📌 Place paper trade</button>
        </div>
      )}
    </div>
  );
}

function num(v: number | null | undefined, dflt: number): number {
  return v == null || Number.isNaN(v) ? dflt : v;
}
function round2(v: number) { return Math.round(v * 100) / 100; }
function fmt(v: number) { return Math.round(v).toLocaleString(); }
function pct(v: number) { return `${(v * 100).toFixed(1)}%`; }
function signed(v: number, dp: number) { return `${v >= 0 ? "+" : ""}${v.toFixed(dp)}`; }

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

function Metric({ label, v, tone, sub, tip }: { label: string; v: string; tone: keyof typeof TONE; sub?: string; tip?: string }) {
  return (
    <div title={tip} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "7px 12px", minWidth: 104, cursor: tip ? "help" : "default" }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "var(--font-mono)", color: TONE[tone] }}>{v}</div>
      {sub && <div style={{ fontSize: 9.5, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function Greek({ label, v, tone = "dim", tip }: { label: string; v: string; tone?: keyof typeof TONE; tip?: string }) {
  return (
    <span title={tip} style={{ display: "inline-flex", gap: 5, alignItems: "baseline", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, padding: "3px 8px", cursor: tip ? "help" : "default" }}>
      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{label}</span>
      <b style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: TONE[tone] }}>{v}</b>
    </span>
  );
}

function Legend({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
      <span style={{ width: 14, height: 0, borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}` }} />
      {label}
    </span>
  );
}

function placeBtn(enabled: boolean): React.CSSProperties {
  return {
    padding: "7px 16px", fontSize: 12, fontWeight: 700, borderRadius: 6,
    border: `1px solid ${enabled ? TONE.ok : "var(--border)"}`,
    background: enabled ? TONE.ok : "transparent",
    color: enabled ? "#04140d" : "var(--text-muted)",
    cursor: enabled ? "pointer" : "not-allowed", whiteSpace: "nowrap",
  };
}

function pill(active: boolean): React.CSSProperties {
  return {
    padding: "5px 10px", fontSize: 11, fontWeight: 600, borderRadius: 6,
    border: `1px solid ${active ? TONE.line : "var(--border)"}`,
    background: active ? `${TONE.line}1f` : "transparent",
    color: active ? TONE.line : "var(--text-muted)", cursor: "pointer", whiteSpace: "nowrap",
  };
}
