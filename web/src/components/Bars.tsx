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
