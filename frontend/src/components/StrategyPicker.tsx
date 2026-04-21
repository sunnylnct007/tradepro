import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { StrategyMetadata } from "../api/types";

interface Props {
  value: string;
  onChange: (next: string) => void;
}

/** Dropdown that uses the backend strategy catalog so horizon + description
 * never drift between backend and UI. Falls back to a hardcoded list if the
 * catalog can't be fetched (during dev when the API's cold-starting). */
export function StrategyPicker({ value, onChange }: Props) {
  const [catalog, setCatalog] = useState<StrategyMetadata[] | null>(null);

  useEffect(() => {
    api.strategies().then((r) => setCatalog(r.catalog)).catch(() => setCatalog(null));
  }, []);

  const items = catalog ?? FALLBACK;
  const active = items.find((i) => i.name === value);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {items.map((s) => (
          <option key={s.name} value={s.name}>
            {s.displayName} · {horizonLabel(s.horizon)}
          </option>
        ))}
      </select>
      {active?.oneLiner && (
        <span style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4 }}>
          {active.oneLiner}
        </span>
      )}
    </div>
  );
}

function horizonLabel(h: StrategyMetadata["horizon"]): string {
  switch (h) {
    case "Intraday": return "intraday";
    case "Short": return "short";
    case "Mid": return "mid";
    case "Long": return "long";
    default: return "any";
  }
}

const FALLBACK: StrategyMetadata[] = [
  { name: "sma_crossover", displayName: "SMA crossover", oneLiner: "", bestIn: "", worstIn: "", horizon: "Mid", horizonText: "", defaultParams: null, paramKeys: null },
  { name: "rsi_mean_reversion", displayName: "RSI mean-reversion", oneLiner: "", bestIn: "", worstIn: "", horizon: "Short", horizonText: "", defaultParams: null, paramKeys: null },
  { name: "macd_signal_cross", displayName: "MACD signal-cross", oneLiner: "", bestIn: "", worstIn: "", horizon: "Mid", horizonText: "", defaultParams: null, paramKeys: null },
  { name: "donchian_breakout", displayName: "Donchian breakout", oneLiner: "", bestIn: "", worstIn: "", horizon: "Mid", horizonText: "", defaultParams: null, paramKeys: null },
  { name: "buy_and_hold", displayName: "Buy & Hold", oneLiner: "", bestIn: "", worstIn: "", horizon: "Long", horizonText: "", defaultParams: null, paramKeys: null },
];
