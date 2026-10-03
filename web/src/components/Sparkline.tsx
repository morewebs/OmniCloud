// Hand-rolled sparkline: 1.5px stroke, no fill. Empty data renders NO line -
// a flat baseline at zero would read as a measured zero (design.md 6).
// Traffic is non-negative, so the scale is 0..max (NOT min..max): a constant
// 100GB series must read as a healthy mid-level line, not a bottom-pinned
// one identical to zero traffic.
export function Sparkline({ values, width = 96, height = 28 }: {
  values: number[]; width?: number; height?: number;
}) {
  if (!values.length) return null;
  const max = Math.max(...values);
  const span = max || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1 || 1)) * (width - 4) + 2;
    const y = height - 3 - (v / span) * (height - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const label = `traffic trend, up to ${max} bytes`;
  return (
    <svg width={width} height={height} role="img" aria-label={label}
         style={{ display: 'block', color: 'inherit' }}>
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
