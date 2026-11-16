/**
 * Sparkline — tiny, dependency-free inline SVG trend line.
 *
 * Deliberately NOT a charting-lib chart: the cockpit already pulls recharts
 * for the big equity curve, but a per-row position trend needs to be cheap
 * (one <svg> per table row, dozens of rows) and self-contained. So this is a
 * hand-rolled polyline.
 *
 * Honesty rule (user: never fabricate a series): the component renders a real
 * line ONLY when given a numeric `data` array of length >= 2. When `data` is
 * null / empty / single-point — i.e. we genuinely have no recent series for
 * this symbol — it renders a subtle em-dash placeholder instead of inventing
 * a shape. Callers pass `null` rather than a flat/synthetic series.
 *
 * Colour follows the trend: last >= first → up (green), else down (red),
 * matching the rest of the desk's red/green semantics.
 */

export function Sparkline({
  data,
  width = 64,
  height = 20,
  strokeWidth = 1.25,
}: {
  data: number[] | null | undefined;
  width?: number;
  height?: number;
  strokeWidth?: number;
}) {
  // No usable series → subtle placeholder, never a fake line.
  if (!data || data.length < 2) {
    return (
      <span
        title="No recent price series available"
        style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: 12 }}
      >
        —
      </span>
    );
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1; // avoid /0 on a flat genuine series
  const pad = strokeWidth; // keep the stroke inside the box
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  const points = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * innerW;
    // SVG y grows downward → invert so higher value sits higher.
    const y = pad + innerH - ((v - min) / span) * innerH;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });

  const up = data[data.length - 1] >= data[0];
  const colour = up ? "var(--up, #1fc16b)" : "var(--down, #ef4444)";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block" }}
      aria-hidden
    >
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={colour}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
