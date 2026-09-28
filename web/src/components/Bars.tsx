// Small SVG charts for order flow and volume profiles; each has a text alternative for screen readers.

export function SignedBars({
  values,
  label,
  height = 80,
}: {
  values: { key: string | number; value: number; title?: string }[];
  label: string;
  height?: number;
}) {
  if (!values.length) return null;
  const max = Math.max(...values.map((v) => Math.abs(v.value)), 1);
  const width = 100 / values.length;
  const mid = height / 2;
  return (
    <svg className="bars" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <line x1="0" x2="100" y1={mid} y2={mid} className="axis" />
      {values.map((v, i) => {
        const h = (Math.abs(v.value) / max) * (mid - 2);
        return (
          <rect
            key={v.key}
            x={i * width + width * 0.1}
            width={width * 0.8}
            y={v.value >= 0 ? mid - h : mid}
            height={Math.max(h, 0.5)}
            className={v.value >= 0 ? "up" : "down"}
          >
            {v.title && <title>{v.title}</title>}
          </rect>
        );
      })}
    </svg>
  );
}

/** A horizontal histogram, highest price on top, with markers for named prices (POC, VAH, VAL, price). */
export function Profile({
  rows,
  marks,
  label,
}: {
  rows: { price: number; volume: number }[];
  marks: { price: number | null | undefined; name: string; kind?: string }[];
  label: string;
}) {
  if (!rows.length) return null;
  const sorted = [...rows].sort((a, b) => b.price - a.price);
  const max = Math.max(...sorted.map((r) => r.volume), 1);
  const hi = sorted[0].price;
  const lo = sorted[sorted.length - 1].price;
  const span = hi - lo || 1;
  const h = Math.max(120, Math.min(360, sorted.length * 4));
  const y = (p: number) => ((hi - p) / span) * (h - 8) + 4;
  const bar = Math.max(1, (h - 8) / sorted.length - 0.5);
  return (
    <svg className="profile" viewBox={`0 0 100 ${h}`} preserveAspectRatio="none" role="img" aria-label={label}>
      {sorted.map((r) => (
        <rect key={r.price} x="0" y={y(r.price) - bar / 2} height={bar} width={(r.volume / max) * 70} className="vol">
          <title>{`${r.price}: ${Math.round(r.volume).toLocaleString()}`}</title>
        </rect>
      ))}
      {marks
        .filter((m) => m.price != null && m.price <= hi && m.price >= lo)
        .map((m) => (
          <g key={m.name}>
            <line x1="0" x2="100" y1={y(m.price!)} y2={y(m.price!)} className={`mark ${m.kind ?? ""}`} />
            <text x="99" y={y(m.price!) - 1} textAnchor="end" className="mark-label">
              {m.name}
            </text>
          </g>
        ))}
    </svg>
  );
}

/** Several lines over a shared numeric x axis, with an optional zero line and a text summary for screen readers. */
export function Lines({
  series,
  label,
  height = 220,
  zero = false,
  marks = [],
}: {
  series: { name: string; points: [number, number][]; kind?: "main" | "alt" | "band" | "muted" }[];
  label: string;
  height?: number;
  zero?: boolean;
  marks?: { x: number; name: string }[];
}) {
  const all = series.flatMap((s) => s.points);
  if (!all.length) return null;
  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]).concat(zero ? [0] : []);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const pad = (y1 - y0) * 0.08 || 1;
  const W = 600;
  const X = (x: number) => ((x - x0) / (x1 - x0 || 1)) * (W - 50) + 45;
  const Y = (y: number) => height - 18 - ((y - (y0 - pad)) / (y1 - y0 + 2 * pad)) * (height - 28);
  const ticks = [y0 - pad, (y0 + y1) / 2, y1 + pad];
  return (
    <svg className="lines" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={45} x2={W - 5} y1={Y(t)} y2={Y(t)} className="grid" />
          <text x={40} y={Y(t) + 3} textAnchor="end" className="tick">
            {Math.abs(t) >= 1000 ? t.toLocaleString(undefined, { maximumFractionDigits: 0 }) : t.toPrecision(4)}
          </text>
        </g>
      ))}
      {zero && y0 < 0 && y1 > 0 && <line x1={45} x2={W - 5} y1={Y(0)} y2={Y(0)} className="zero" />}
      {marks.map((m) => (
        <g key={m.name + m.x}>
          <line x1={X(m.x)} x2={X(m.x)} y1={8} y2={height - 18} className="mark" />
          <text x={X(m.x) + 3} y={16} className="tick">{m.name}</text>
        </g>
      ))}
      {series.map((s) => (
        <polyline
          key={s.name}
          className={`line ${s.kind ?? "main"}`}
          points={s.points.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ")}
        >
          <title>{s.name}</title>
        </polyline>
      ))}
    </svg>
  );
}
