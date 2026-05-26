/**
 * StrategyChartsCard — embed any Plotly figure the strategy emitted
 * via recent_charts() directly on the cockpit. Today that's the
 * per-symbol Ichimoku cloud + fill markers from ichimoku_equity.
 *
 * Defaults to closed so the heavy plotly.js bundle only lazy-loads
 * when the trader explicitly opens it.
 */
import { CockpitCard } from "../CockpitCard";
import { PlotlyChart } from "../PlotlyChart";
import type { LatestSession } from "../../types/cockpit";

type Entry = {
  key: string;
  title: string;
  strategy: string;
  figure: unknown;
};

export function StrategyChartsCard({
  latestSessions, onHide,
}: {
  latestSessions: LatestSession[];
  onHide?: () => void;
}) {
  const entries: Entry[] = [];
  for (const s of latestSessions) {
    for (const [name, fig] of Object.entries(s.charts ?? {})) {
      entries.push({ key: `${s.strategy}.${name}`, title: name, strategy: s.strategy, figure: fig });
    }
  }
  entries.sort((a, b) => a.key.localeCompare(b.key));

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
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {entries.map((e) => (
            <div key={e.key}>
              <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 4 }}>
                {e.strategy} · {e.title}
              </div>
              <PlotlyChart figure={e.figure as Record<string, unknown>} />
            </div>
          ))}
        </div>
      )}
    </CockpitCard>
  );
}
