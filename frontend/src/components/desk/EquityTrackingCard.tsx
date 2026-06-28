/**
 * EquityTrackingCard — "is the systematic Ichimoku clone proving itself?"
 *
 * The clone (ichimoku_equity_ibkr) IS configured to be the backtested strategy
 * (clean large_50, gate off, 8% stop, parity-locked). The only thing left is
 * confirming the LIVE book tracks the backtest expectation. This turns
 * "eyeball it" into an honest verdict, on the cockpit, with a refresh button.
 *
 * It computes the SAME verdict as `tradepro-equity-track` (CLI), from the SAME
 * source (/api/pnl/by-strategy), via the shared assessTracking() mirror — so the
 * lab's behaviour is always visible, not a script you have to remember to run.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { assessTracking, type TrackingStatus, type TrackingVerdict } from "../../util/equityTracking";

// Mirrors strategies/.../cli/equity_track.py — keep in lock-step. These are the
// clone's clean-large_50 config facts; ideally move to config later
// (feedback_config_driven_no_hardcoding) but pinned here so the card is honest now.
const STRATEGY_ID = "ichimoku_equity_ibkr";
const CLEAN_CONFIG_START = "2026-06-26"; // when large_50-only went live
const DEPLOYED_CAPITAL = 50_000; // USD — clone capital_usd (shown transparently below)
const EXPECTED_CAGR_PCT = 8.9; // through-cycle backtest (2022→2026)
const DRAWDOWN_BOUND_PCT = -13.0; // backtest max DD — the line not to cross

const STATUS_STYLE: Record<TrackingStatus, { dot: string; label: string; color: string }> = {
  TOO_EARLY: { dot: "⚪", label: "Too early", color: "var(--text-muted)" },
  ON_TRACK: { dot: "🟢", label: "On track", color: "#3fb950" },
  AHEAD: { dot: "🟢", label: "Ahead", color: "#3fb950" },
  BEHIND: { dot: "🟡", label: "Behind", color: "#d29922" },
  DRAWDOWN_BREACH: { dot: "🔴", label: "Drawdown breach", color: "#f85149" },
};

type State = "loading" | "ok" | "nodata" | "error";

function daysSince(iso: string): number {
  const start = new Date(iso + "T00:00:00Z").getTime();
  return Math.max(0, Math.floor((Date.now() - start) / 86_400_000));
}

export function EquityTrackingCard() {
  const [state, setState] = useState<State>("loading");
  const [verdict, setVerdict] = useState<TrackingVerdict | null>(null);
  const [pnl, setPnl] = useState<{ realised: number; open: number; ccy: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const d = await api.pnlByStrategy();
      const row = d.rows.find((r) => r.strategyId === STRATEGY_ID);
      if (!row) {
        setState("nodata");
        setErr(null);
        return;
      }
      const realised = row.realisedLtd ?? 0;
      const open = row.openPnl ?? 0;
      const actualPct = DEPLOYED_CAPITAL ? ((realised + open) / DEPLOYED_CAPITAL) * 100 : 0;
      const v = assessTracking(actualPct, daysSince(CLEAN_CONFIG_START), {
        expectedCagrPct: EXPECTED_CAGR_PCT,
        drawdownBoundPct: DRAWDOWN_BOUND_PCT,
      });
      setVerdict(v);
      setPnl({ realised, open, ccy: row.currency || "USD" });
      setState("ok");
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setState("error");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const s = verdict ? STATUS_STYLE[verdict.status] : null;
  const ccySym = pnl?.ccy === "GBP" ? "£" : pnl?.ccy === "USD" ? "$" : "";

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>ICH equity — live vs backtest</span>
        <span
          title="What is this? Click to explain"
          onClick={() => setShowHelp((h) => !h)}
          style={{ cursor: "pointer", color: "var(--text-muted)", border: "1px solid #1b2233", borderRadius: 9, width: 15, height: 15, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 9 }}
        >
          ?
        </span>
        {state === "ok" && s && (
          <span style={{ color: s.color, fontWeight: 700, marginLeft: 2 }}>{s.dot} {s.label}</span>
        )}
        <button
          onClick={() => void load()}
          disabled={refreshing}
          title="Re-run the verdict against the latest live P&L"
          style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px", borderRadius: 5, border: "1px solid #1b2233", background: "rgba(255,255,255,0.03)", color: "var(--text-dim)", cursor: refreshing ? "default" : "pointer", opacity: refreshing ? 0.5 : 1 }}
        >
          {refreshing ? "…" : "↻ refresh"}
        </button>
      </div>

      {showHelp && (
        <div style={{ padding: "6px 10px", fontSize: 10, color: "var(--text-muted)", borderBottom: "1px solid #1b2233", lineHeight: 1.5 }}>
          The clone is configured to BE the backtested strategy (clean large_50, 8% stop, parity-locked).
          This compares its live return-on-capital to the backtest's pro-rated expectation
          ({EXPECTED_CAGR_PCT}% CAGR). <b>Too early</b> = &lt;20 sessions, too noisy to judge.
          <b> On track / Ahead / Behind</b> = within / above / below a ±6pp band.
          <b> Drawdown breach</b> = live worse than the backtest max-DD ({DRAWDOWN_BOUND_PCT}%) — the one hard flag.
        </div>
      )}

      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Loading verdict…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Verdict unavailable: {err} (EC2 may be asleep)</div>}
      {state === "nodata" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>No P&L yet for {STRATEGY_ID}.</div>}

      {state === "ok" && verdict && pnl && (
        <div style={{ padding: "8px 10px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 8 }}>
            <Stat label="actual return" v={`${fmtPct(verdict.actualReturnPct)}`} good={verdict.actualReturnPct >= 0} />
            <Stat label="expected (pro-rated)" v={`${fmtPct(verdict.expectedReturnPct)}`} />
            <Stat label="realised + open" v={`${ccySym}${Math.round(pnl.realised)} ${pnl.open >= 0 ? "+" : "−"} ${ccySym}${Math.abs(Math.round(pnl.open))}`} good={pnl.realised + pnl.open >= 0} />
            <Stat label="elapsed" v={`${verdict.elapsedDays}d`} />
          </div>
          <div style={{ marginTop: 7, fontSize: 10.5, color: "var(--text-dim)", lineHeight: 1.45 }}>{verdict.note}</div>
          <div style={{ marginTop: 5, fontSize: 9.5, color: "var(--text-muted)" }}>
            on {ccySym}{DEPLOYED_CAPITAL.toLocaleString()} deployed · since {CLEAN_CONFIG_START} · DD bound {DRAWDOWN_BOUND_PCT}%
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, v, good }: { label: string; v: string; good?: boolean }) {
  const color = good === undefined ? "var(--text-dim)" : good ? "#3fb950" : "#f85149";
  return (
    <div>
      <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color }}>{v}</div>
    </div>
  );
}

function fmtPct(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}
