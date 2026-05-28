/**
 * AlgoVerdictPill — compact per-symbol annotation showing the
 * trader-algo's verdict (BUY / HOLD / FLAT / OUT_OF_UNIVERSE) for
 * one symbol.
 *
 * Designed to drop into existing Decide / Compare / portfolio cards
 * so the trader sees the algo's view alongside the multi-indicator
 * consensus, without forking the existing surfaces. Reads
 * /api/live-portfolio/by-symbol/{symbol}.
 *
 * Pill text:
 *   BUY 4.2%        — algo wants this name at 4.2% target weight
 *   HOLD            — signal positive but no weight (regime-gated)
 *   FLAT            — algo says no signal today
 *   (no pill)       — symbol is OUT_OF_UNIVERSE (not algo's job)
 *
 * Tooltip carries the WHY — sleeve, signal, cloud position, regime,
 * vol — so the operator + the MCP layer can both make sense of it.
 *
 * No pill when the symbol isn't in the algo's universe — keeps the
 * card uncluttered for ETFs / non-equity / out-of-scope names.
 */
import { useEffect, useState } from "react";
import { config } from "../config";

interface AlgoVerdict {
  symbol: string;
  inAlgoUniverse: boolean;
  verdict?: string;
  sleeve?: string;
  targetWeight?: number;
  signal?: number;
  regimePass?: boolean;
  regimeState?: string;
  vol?: number;
  riskClass?: string | null;
  asOfUtc?: string;
  detail?: {
    cloud_position?: string;
    tk_cross?: string;
    above_cloud_pct?: number;
    below_cloud_pct?: number;
  } | null;
}

interface Props {
  symbol: string;
  strategy?: string;
  compact?: boolean;
}

export function AlgoVerdictPill({ symbol, strategy = "ichimoku_equity", compact }: Props) {
  const [v, setV] = useState<AlgoVerdict | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          `${config.apiBaseUrl}/api/live-portfolio/by-symbol/${encodeURIComponent(symbol)}?strategy=${encodeURIComponent(strategy)}`,
        );
        if (!r.ok) return;
        const data = (await r.json()) as AlgoVerdict;
        if (!cancelled) setV(data);
      } catch { /* silent */ }
    })();
    return () => { cancelled = true; };
  }, [symbol, strategy]);

  if (!v) return null;
  if (!v.inAlgoUniverse) return null; // no clutter for out-of-scope names

  const verdict = v.verdict ?? "FLAT";
  const color = verdict === "BUY" ? "#1fc16b"
    : verdict === "HOLD" ? "#4f8cff"
    : verdict === "HOLD_REGIME_BLOCKED" ? "#f59e0b"
    : "#9ca3af";
  const label = verdict === "BUY" && v.targetWeight
    ? compact
      ? `BUY ${(v.targetWeight * 100).toFixed(1)}%`
      : `BUY ${(v.targetWeight * 100).toFixed(2)}%`
    : verdict === "HOLD_REGIME_BLOCKED"
      ? "HOLD (REGIME)"
      : verdict;

  const cloudPos = v.detail?.cloud_position;
  const tk = v.detail?.tk_cross;
  const reasons: string[] = [];
  if (v.sleeve) reasons.push(`sleeve: ${v.sleeve}`);
  if (cloudPos) reasons.push(`cloud: ${cloudPos}`);
  if (tk) reasons.push(`TK: ${tk}`);
  if (v.regimeState) reasons.push(`regime: ${v.regimeState}`);
  if (v.vol) reasons.push(`vol: ${v.vol.toFixed(1)}%`);
  const tooltip = `Trader-algo verdict for ${symbol}\n${reasons.join("\n")}`
    + (v.asOfUtc ? `\n\nas of ${new Date(v.asOfUtc).toLocaleString()}` : "");

  return (
    <span
      title={tooltip}
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        padding: compact ? "1px 6px" : "2px 8px",
        borderRadius: 999,
        background: `${color}22`,
        color, fontWeight: 700,
        fontSize: compact ? 9 : 10,
        letterSpacing: "0.04em", textTransform: "uppercase",
        border: `1px solid ${color}33`,
        cursor: "help",
        fontFamily: "ui-monospace, Menlo, monospace",
      }}
    >
      <span style={{ fontSize: compact ? 8 : 9, opacity: 0.7 }}>algo</span>
      {label}
    </span>
  );
}
