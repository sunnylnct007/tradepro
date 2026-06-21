import { useEffect, useState } from "react";
import { api } from "../../api/client";

/**
 * Options Desk — the wheel (cash-secured put → assignment → covered call),
 * risk-first per the BRD. v1 surface: the CANDIDATE SCREEN — for each approved
 * underlying, the regime (Ichimoku cloud), IV-Rank, liquidity, and whether it
 * passes the risk engine for a cash-secured put. Eligibility is fail-visible:
 * a name that can't be verified (missing IV-Rank / regime / chain) shows BLOCKED
 * with the reason — never a silent green light (no false positives).
 *
 * Data is produced by the Mac-side `tradepro-options-screen` job (IV-Rank from
 * IBKR, regime from the bar cache, run through quant_engine.options.risk) and
 * pushed to /api/options/candidates — same pattern as the bar-cache health feed.
 */
interface Candidate {
  symbol: string;
  regime: string | null;          // GREEN/YELLOW/ORANGE/RED
  iv_rank: number | null;         // 0-100
  iv: number | null;              // fraction
  open_interest: number | null;
  spread_usd: number | null;
  eligible: boolean;              // passes the risk engine for a CSP
  blocks: string[];               // why-not (fail-visible)
  warnings: string[];
  suggested_strike: number | null;
  suggested_delta: number | null;
  suggested_premium: number | null;
}
interface ScreenResp {
  generated_at_utc: string | null;
  market_open: boolean;
  candidates: Candidate[];
}

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)" };
const REGIME_TONE: Record<string, string> = { GREEN: TONE.ok, YELLOW: TONE.warn, ORANGE: "#E67E22", RED: TONE.bad };

export function OptionsDesk() {
  const [data, setData] = useState<ScreenResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    api.optionsCandidates()
      .then((d) => { if (live) { setData(d as ScreenResp); setErr(null); } })
      .catch((e) => { if (live) setErr(String(e?.message || e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, []);

  const cands = data?.candidates ?? [];
  const eligible = cands.filter((c) => c.eligible);

  return (
    <div style={{ padding: "8px 4px" }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>Options Desk — the Wheel</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5, marginTop: 2 }}>
          Cash-secured puts on quality names, risk-first. A candidate is <b>eligible</b> only when it
          clears <b>every</b> gate: constructive regime (in/above the Ichimoku cloud, not a falling knife),
          <b> IV-Rank &gt; 30</b> (premium rich), delta 0.20–0.35, 25–50 DTE, OI &gt; 1,000, spread ≤ $0.10,
          no earnings in the window, and within capital limits. Anything we can't verify shows
          <b> BLOCKED with the reason</b> — never a silent green light.
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Stat label="Eligible CSP" value={eligible.length} tone="ok" />
        <Stat label="Screened" value={cands.length} tone="dim" />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {data?.generated_at_utc ? `last screen ${new Date(data.generated_at_utc).toLocaleString()}` : "no screen yet"}
          {data && !data.market_open ? " · market closed (chain/Δ pending open)" : ""}
        </span>
      </div>

      {loading && <div style={{ color: "var(--text-muted)", padding: 16 }}>Loading screen…</div>}
      {err && <div style={{ color: TONE.bad, padding: 16 }}>Screen unavailable: {err}</div>}
      {!loading && !err && cands.length === 0 && (
        <div style={{ color: "var(--text-muted)", padding: 16 }}>
          No candidates screened yet. Run the Mac screen job (<code>tradepro-options-screen</code>) to populate.
        </div>
      )}

      {cands.length > 0 && (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Regime", "IV-Rank", "OI / Spread", "Eligible (CSP)", "Suggested", "Why / why-not"].map((h) => (
                  <th key={h} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cands.map((c) => (
                <tr key={c.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{c.symbol}</td>
                  <td style={{ padding: "8px 10px" }}>
                    {c.regime
                      ? <span style={{ color: REGIME_TONE[c.regime] || TONE.dim, fontWeight: 600 }}>{c.regime}</span>
                      : <span style={{ color: TONE.bad }}>n/a</span>}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>
                    {c.iv_rank == null ? <span style={{ color: TONE.bad }}>n/a</span>
                      : <span style={{ color: c.iv_rank >= 30 ? TONE.ok : TONE.warn }}>{c.iv_rank.toFixed(0)}%</span>}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {c.open_interest == null ? "—" : c.open_interest.toLocaleString()}
                    {c.spread_usd != null ? ` / $${c.spread_usd.toFixed(2)}` : ""}
                  </td>
                  <td style={{ padding: "8px 10px", fontWeight: 700, color: c.eligible ? TONE.ok : TONE.dim }}>
                    {c.eligible ? "✓ YES" : "— no"}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {c.suggested_strike != null
                      ? `$${c.suggested_strike} · Δ${(c.suggested_delta ?? 0).toFixed(2)}${c.suggested_premium != null ? ` · $${c.suggested_premium.toFixed(2)}` : ""}`
                      : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", color: c.eligible ? TONE.warn : TONE.bad, maxWidth: 360 }}>
                    {(c.blocks?.length ? c.blocks : c.warnings)?.join("; ") || (c.eligible ? "all gates pass" : "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone: keyof typeof TONE }) {
  return (
    <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 14px", minWidth: 90 }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)", color: TONE[tone] }}>{value}</div>
    </div>
  );
}
