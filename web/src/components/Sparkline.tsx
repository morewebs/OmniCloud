// Hand-rolled sparkline: 1.5px stroke, no fill. Empty data renders NO line -
// a flat baseline at zero would read as a measured zero (design.md 6).
// Stroke follows currentColor so it dims/strengthens with theme and context.
export function Sparkline({ values, width = 96, height = 28 }: {
  values: number[]; width?: number; height?: number;
}) {
  if (!values.length) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1 || 1)) * (width - 4) + 2;
    const y = height - 3 - ((v - min) / span) * (height - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg width={width} height={height} aria-hidden style={{ display: 'block', color: 'inherit' }}>
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
