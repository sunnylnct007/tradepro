/**
 * StrategyChartsCard — embed any Plotly figure the strategy emitted
 * via recent_charts() directly on the cockpit. Today that's the
 * per-symbol Ichimoku cloud + fill markers from ichimoku_equity.
 *
 * Per-symbol CLICK-TO-EXPAND: previously this stacked EVERY symbol's chart
 * vertically — a 100-symbol universe meant scrolling a mile of heavy plotly
 * figures no one reads. Now it's a compact grid of symbol chips; clicking one
 * renders just that chart (and only then does its plotly bundle mount).
 * Defaults closed so plotly.js lazy-loads only on first open.
 */
import { useMemo, useState } from "react";
import { CockpitCard } from "../CockpitCard";
import { PlotlyChart } from "../PlotlyChart";
import type { LatestSession } from "../../types/cockpit";

type Entry = {
  key: string;
  title: string;
  symbol: string;
  strategy: string;
  figure: unknown;
};

// "ichimoku_cloud:AAPL" → "AAPL"; falls back to the whole title.
function symbolOf(title: string): string {
  const i = title.lastIndexOf(":");
  return i >= 0 ? title.slice(i + 1) : title;
}

export function StrategyChartsCard({
  latestSessions, onHide,
}: {
  latestSessions: LatestSession[];
  onHide?: () => void;
}) {
  const entries = useMemo(() => {
    const out: Entry[] = [];
    for (const s of latestSessions) {
      for (const [name, fig] of Object.entries(s.charts ?? {})) {
        out.push({
          key: `${s.strategy}.${name}`, title: name,
          symbol: symbolOf(name), strategy: s.strategy, figure: fig,
        });
      }
    }
    out.sort((a, b) => a.symbol.localeCompare(b.symbol));
    return out;
  }, [latestSessions]);

  const [openKey, setOpenKey] = useState<string | null>(null);
  const open = entries.find((e) => e.key === openKey) ?? null;

  return (
    <CockpitCard
      id="charts"
      title="Strategy charts (live signal viz)"
      badge={entries.length || undefined}
      defaultOpen={false}
      fullWidth
      onHide={onHide}
    >
      {entries.length === 0 ? (
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No charts attached to the latest session yet. Strategies that
          implement recent_charts() (today: ichimoku_equity → cloud chart
          per symbol) populate this on the next completed run.
        </span>
      ) : (
        <>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
            Click a symbol to view its chart.
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: open ? 12 : 0 }}>
            {entries.map((e) => {
              const active = e.key === openKey;
              return (
                <button
                  key={e.key} type="button"
                  onClick={() => setOpenKey(active ? null : e.key)}
                  title={`${e.strategy} · ${e.title}`}
                  style={{
                    padding: "4px 10px", fontSize: 12, borderRadius: 6,
                    border: `1px solid ${active ? "#4f8cff" : "var(--border)"}`,
                    background: active ? "rgba(79,140,255,0.12)" : "transparent",
                    color: active ? "#4f8cff" : "var(--text)",
                    cursor: "pointer", fontFamily: "monospace",
                  }}
                >
                  {e.symbol}
                </button>
              );
            })}
          </div>
          {open && (
            <div>
              <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 4 }}>
                {open.strategy} · {open.title}
              </div>
              <PlotlyChart figure={open.figure as Record<string, unknown>} />
            </div>
          )}
        </>
      )}
    </CockpitCard>
  );
}
