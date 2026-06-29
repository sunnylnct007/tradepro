/**
 * SymbolRangeRiskCard — the discretionary / risk overlay next to the systematic signal.
 *
 * The systematic engine fires BUY on strength (above cloud + bullish TK). That's
 * correct by design — and our fill-replay showed extended large_50 entries did
 * fine/better, so we do NOT veto on extension. But "systematic about RISK,
 * discretionary about ENTRY" means the human still needs the context the signal
 * doesn't carry: WHERE in the 52w range, how stretched, and what a lower-risk
 * pullback entry / stop distance looks like. This surfaces exactly that.
 *
 * Computed client-side from daily candles (same source as the chart) — no
 * fabricated data: if there's too little history, it says so.
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

const STOP_PCT = 8; // the clone's 8% hard stop — for the risk-to-stop readout

interface RangeRisk {
  close: number;
  rangePctile: number; // position within trailing 52w range (0-100)
  pctOffHigh: number;
  pctOver200: number | null;
  atrPct: number | null;
  kijun: number | null; // 32-period mid — the "pullback" reference
  riskToKijunPct: number | null; // downside from price to kijun
  stopLevel: number; // price * (1 - STOP_PCT/100)
  bars: number;
}

function compute(highs: number[], lows: number[], closes: number[]): RangeRisk | null {
  const n = closes.length;
  if (n < 30) return null;
  const close = closes[n - 1];
  const w = closes.slice(-252);
  const hi = Math.max(...w), lo = Math.min(...w);
  const rangePctile = hi > lo ? ((close - lo) / (hi - lo)) * 100 : 50;
  const pctOffHigh = (close / hi - 1) * 100;
  const sma200 = n >= 200 ? closes.slice(-200).reduce((a, b) => a + b, 0) / 200 : null;
  const pctOver200 = sma200 ? (close / sma200 - 1) * 100 : null;
  // ATR(14)
  let atrPct: number | null = null;
  if (n >= 15) {
    const trs: number[] = [];
    for (let i = n - 14; i < n; i++) {
      trs.push(Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1])));
    }
    const atr = trs.reduce((a, b) => a + b, 0) / trs.length;
    atrPct = (atr / close) * 100;
  }
  // Kijun(32) = (max high + min low) over 32
  let kijun: number | null = null, riskToKijunPct: number | null = null;
  if (n >= 32) {
    kijun = (Math.max(...highs.slice(-32)) + Math.min(...lows.slice(-32))) / 2;
    riskToKijunPct = (close / kijun - 1) * 100;
  }
  return { close, rangePctile, pctOffHigh, pctOver200, atrPct, kijun, riskToKijunPct, stopLevel: close * (1 - STOP_PCT / 100), bars: n };
}

export function SymbolRangeRiskCard({ symbol }: { symbol: string }) {
  const [rr, setRr] = useState<RangeRisk | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "thin" | "error">("loading");

  useEffect(() => {
    if (!symbol) return;
    let live = true;
    setState("loading");
    const to = new Date();
    const from = new Date(Date.now() - 820 * 24 * 3600 * 1000); // ~2.2y for 52w range + 200SMA
    api.candles({ symbol, interval: "1d", from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) })
      .then((s) => {
        if (!live) return;
        const cs = s.candles ?? [];
        const r = compute(cs.map((c) => c.high), cs.map((c) => c.low), cs.map((c) => c.close));
        if (!r) { setState("thin"); return; }
        setRr(r); setState("ok");
      })
      .catch(() => { if (live) setState("error"); });
    return () => { live = false; };
  }, [symbol]);

  const hot = rr ? rr.rangePctile >= 90 : false;

  return (
    <div style={{ border: "1px solid #1b2233", borderRadius: 6, background: "rgba(255,255,255,0.015)", padding: "10px 12px" }}>
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8, fontWeight: 600 }}>
        Range / Risk — entry context
      </div>
      {state === "loading" && <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Loading…</div>}
      {state === "error" && <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>No price history for {symbol}.</div>}
      {state === "thin" && <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>Insufficient history to compute range/risk.</div>}
      {state === "ok" && rr && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(92px, 1fr))", gap: 8 }}>
            <Stat label="52w range pos" v={`${rr.rangePctile.toFixed(0)}th`} tone={hot ? "warn" : rr.rangePctile <= 30 ? "good" : undefined} />
            <Stat label="off 52w high" v={`${rr.pctOffHigh.toFixed(1)}%`} />
            <Stat label="vs 200-SMA" v={rr.pctOver200 == null ? "—" : `${rr.pctOver200 >= 0 ? "+" : ""}${rr.pctOver200.toFixed(0)}%`} />
            <Stat label="ATR (14)" v={rr.atrPct == null ? "—" : `${rr.atrPct.toFixed(1)}%/day`} />
          </div>
          <div style={{ marginTop: 8, fontSize: 10.5, color: "var(--text-dim)", lineHeight: 1.5 }}>
            {hot
              ? `Near the 52w high (${rr.rangePctile.toFixed(0)}th pctile). The systematic signal buys strength — extended large_50 entries did fine historically, so it's not vetoed. `
              : `Mid-range (${rr.rangePctile.toFixed(0)}th pctile) — room before the high. `}
            {rr.kijun != null && (
              <>Lower-risk discretionary entry: pullback to <b>kijun ${rr.kijun.toFixed(2)}</b> ({rr.riskToKijunPct! >= 0 ? "−" : "+"}{Math.abs(rr.riskToKijunPct!).toFixed(1)}% from here). </>
            )}
            8% stop sits at <b>${rr.stopLevel.toFixed(2)}</b> — {hot ? "wide entry → size DOWN to keep risk constant." : "size to this distance."}
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, v, tone }: { label: string; v: string; tone?: "good" | "warn" }) {
  const color = tone === "good" ? "#3fb950" : tone === "warn" ? "#d29922" : "var(--text)";
  return (
    <div>
      <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color }}>{v}</div>
    </div>
  );
}
