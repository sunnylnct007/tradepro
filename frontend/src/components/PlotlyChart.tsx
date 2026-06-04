/**
 * PlotlyChart — single component that renders any Plotly figure
 * JSON returned by the backend's viz framework
 * (tradepro_strategies.viz). New charts on the backend require
 * zero frontend code; they slot in here automatically.
 *
 * Why dynamic import: plotly.js is ~1MB even in the "basic" build.
 * Most pages never render a chart. Lazy-loading the dep until the
 * Charts tab actually mounts keeps initial page load fast.
 *
 * Contract: figure is the Plotly figure spec — { data, layout, ... }.
 * No interactivity wired yet (no click-to-zoom, no event callbacks);
 * we can layer that in later without changing the component
 * boundary.
 */
import { lazy, Suspense, useMemo } from "react";

const Plot = lazy(async () => {
  // plotly.js-basic-dist is the smaller build (~1MB) — covers Scatter,
  // Histogram, Heatmap, Bar; excludes more exotic 3-D / geo traces.
  // Upgrade to plotly.js-dist-min if a future chart needs them.
  const Plotly = (await import("plotly.js-basic-dist")).default;
  const factory = (await import("react-plotly.js/factory")).default;
  return { default: factory(Plotly) };
});

type FigureLike = {
  data?: unknown;
  layout?: unknown;
  config?: unknown;
};

// plotly.js-basic-dist ships only scatter/bar/pie/etc — it does NOT include
// the `candlestick` / `ohlc` finance modules. When a figure hands us a
// candlestick (our Ichimoku charts use one for the price), the basic build
// can't process its open/high/low/close (worse: it can't decode the binary
// {dtype,bdata} typed arrays for an unregistered trace), so the price fails
// to render AND the y-axis auto-ranges off garbage — the "weird green
// diagonal at the bottom" bug. Down-convert any candlestick/ohlc to a clean
// scatter line on `close` so it renders correctly without bloating the bundle
// with the full finance dist.
function normaliseTraces(data: unknown[]): unknown[] {
  return data.map((trace) => {
    if (!trace || typeof trace !== "object") return trace;
    const t = trace as Record<string, unknown>;
    if (t.type !== "candlestick" && t.type !== "ohlc") return trace;
    const inc = (t.increasing as Record<string, unknown> | undefined)?.line as
      | Record<string, unknown>
      | undefined;
    const colour = (inc?.color as string) ?? "#06A77D";
    return {
      type: "scatter",
      mode: "lines",
      x: t.x,
      y: t.close,                // close series — typed-array safe in basic-dist
      name: t.name ?? "Price",
      line: { color: colour, width: 1.5 },
      hovertemplate: t.hovertemplate,
    };
  });
}

export function PlotlyChart({
  figure,
  className,
  fallback = "Loading chart…",
}: {
  figure: FigureLike | null | undefined;
  className?: string;
  fallback?: string;
}) {
  // Normalise — backend may hand us either the figure dict directly
  // or a wrapper { figure: {...} }. Guard against null gracefully.
  const fig = useMemo(() => {
    if (!figure) return null;
    const f = figure as Record<string, unknown>;
    if (Array.isArray(f.data)) return f;
    if (f.figure) return f.figure as FigureLike;
    return null;
  }, [figure]);

  if (!fig) {
    return (
      <div style={{ padding: 12, fontSize: 12, color: "var(--text-muted)" }}>
        No chart data.
      </div>
    );
  }
  return (
    <div className={className} style={{ width: "100%", minHeight: 400 }}>
      <Suspense
        fallback={
          <div style={{ padding: 12, fontSize: 12, color: "var(--text-muted)" }}>
            {fallback}
          </div>
        }
      >
        <Plot
          data={normaliseTraces((fig as Record<string, unknown>).data as unknown[])}
          layout={
            ((fig as Record<string, unknown>).layout as Record<string, unknown>) ?? {}
          }
          config={{
            responsive: true,
            displaylogo: false,
            scrollZoom: true,            // scroll/pinch to zoom into the chart
            displayModeBar: true,        // always show zoom/pan/autoscale/reset tools
            modeBarButtonsToRemove: ["select2d", "lasso2d"],
            doubleClick: "reset",        // double-click to reset the view
          }}
          style={{ width: "100%", height: "100%" }}
          useResizeHandler
        />
      </Suspense>
    </div>
  );
}
