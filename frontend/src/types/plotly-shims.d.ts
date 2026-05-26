// Minimal type shims so the PlotlyChart wrapper compiles without
// pulling in the heavy @types/plotly.js declarations. We only call
// the runtime API surface; the data + layout are typed as `unknown`
// at the public boundary because the figure JSON shape is owned
// by the backend's viz framework, not the frontend.

declare module "plotly.js-basic-dist" {
  // The basic dist's default export is the Plotly namespace used by
  // react-plotly.js/factory. We type it loosely — the factory call
  // doesn't introspect.
  const Plotly: unknown;
  export default Plotly;
}

declare module "react-plotly.js/factory" {
  import type { ComponentType } from "react";

  // The factory returns a React component accepting Plotly figure
  // props. We use a permissive shape since the figure JSON is
  // produced by the backend and validated there.
  type PlotProps = {
    data: unknown[];
    layout?: Record<string, unknown>;
    config?: Record<string, unknown>;
    style?: Record<string, unknown>;
    useResizeHandler?: boolean;
    onClick?: (event: unknown) => void;
    onHover?: (event: unknown) => void;
    className?: string;
  };
  const createPlotlyComponent: (plotly: unknown) => ComponentType<PlotProps>;
  export default createPlotlyComponent;
}
