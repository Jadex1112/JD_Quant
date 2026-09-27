import { useEffect, useRef } from "react";
import { AreaSeries, ColorType, createChart, type UTCTimestamp } from "lightweight-charts";

export interface Point {
  time: string;
  value: number;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Area chart for equity curves; exposes a table of the same data for screen readers (NFR-73005). */
export function LineChart({ points, label }: { points: Point[]; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const text = cssVar("--text-muted");
    const accent = cssVar("--accent");
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: text },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: accent,
      topColor: `${accent}55`,
      bottomColor: `${accent}05`,
      lineWidth: 2,
    });
    const seen = new Set<number>();
    series.setData(
      points
        .map((p) => ({ time: Math.floor(new Date(p.time).getTime() / 1000) as UTCTimestamp, value: p.value }))
        .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true))),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  const first = points[0]?.value;
  const last = points[points.length - 1]?.value;
  return (
    <figure style={{ margin: 0 }}>
      <div ref={ref} className="chart" role="img" aria-label={label} />
      <figcaption className="sr-only">
        {label}: {points.length} points from {first} to {last}.
      </figcaption>
    </figure>
  );
}
