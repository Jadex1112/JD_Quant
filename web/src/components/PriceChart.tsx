import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { get, type Fill, type Instrument } from "../api";
import { num, signed, tone } from "../format";
import { InstrumentPicker } from "./InstrumentPicker";
import { Badge } from "./ui";

interface Bar {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface CandleResponse {
  origin: "broker" | "quotes";
  simulated: boolean;
  candles: Bar[];
}

const INTERVALS: [number, string][] = [
  [60, "1m"],
  [300, "5m"],
  [900, "15m"],
  [3600, "1h"],
  [86400, "1D"],
];

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Candlestick chart that keeps the last bar moving with every streamed quote, with your fills marked.
 * Pass `instruments` and `onInstrumentChange` to let the viewer switch instrument from the chart's header.
 */
export function PriceChart({
  instrumentId,
  height = 360,
  instruments,
  onInstrumentChange,
}: {
  instrumentId: string;
  height?: number;
  instruments?: Instrument[];
  onInstrumentChange?: (instrumentId: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lastBar = useRef<Bar | null>(null);
  const [interval, setIntervalSeconds] = useState(60);
  const [meta, setMeta] = useState<{ origin: string; simulated: boolean } | null>(null);
  const [price, setPrice] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [status, setStatus] = useState<"connecting" | "live" | "offline">("connecting");

  // Chart and history
  useEffect(() => {
    if (!box.current) return;
    const up = cssVar("--pos");
    const down = cssVar("--neg");
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--text-muted") },
      grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: interval < 86400, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      wickUpColor: up,
      wickDownColor: down,
      borderVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    let cancelled = false;
    Promise.all([
      get<CandleResponse>(
        `/market-data/candles?instrument_id=${encodeURIComponent(instrumentId)}&interval_seconds=${interval}&limit=500`,
      ),
      get<Fill[]>(`/fills?instrument_id=${encodeURIComponent(instrumentId)}&limit=200`).catch(() => [] as Fill[]),
    ])
      .then(([data, fills]) => {
        if (cancelled) return;
        series.setData(data.candles);
        lastBar.current = data.candles[data.candles.length - 1] ?? null;
        setMeta({ origin: data.origin, simulated: data.simulated });
        if (lastBar.current) {
          setPrice(lastBar.current.close);
          setOpen(data.candles[0].open);
        }
        const markers: SeriesMarker<UTCTimestamp>[] = fills
          .map((f) => {
            const t = Math.floor(new Date(f.exchange_ts).getTime() / 1000);
            return {
              time: (t - (t % interval)) as UTCTimestamp,
              position: f.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
              color: f.side === "BUY" ? up : down,
              shape: f.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
              text: `${f.side === "BUY" ? "B" : "S"} ${num(f.quantity)}`,
            };
          })
          .sort((a, b) => a.time - b.time);
        createSeriesMarkers(series, markers);
        chart.timeScale().fitContent();
      })
      .catch(() => setStatus("offline"));
    return () => {
      cancelled = true;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [instrumentId, interval]);

  // Live quotes
  useEffect(() => {
    setStatus("connecting");
    const source = new EventSource(`/api/v1/market-data/stream?instruments=${encodeURIComponent(instrumentId)}`);
    source.onopen = () => setStatus("live");
    source.onerror = () => setStatus("offline");
    source.addEventListener("quote", (event) => {
      const quote = JSON.parse((event as MessageEvent).data) as { time: string; price: number; simulated: boolean };
      const t = Math.floor(new Date(quote.time).getTime() / 1000);
      const bucket = (t - (t % interval)) as UTCTimestamp;
      const last = lastBar.current;
      let bar: Bar;
      if (last && last.time === bucket) {
        bar = { ...last, high: Math.max(last.high, quote.price), low: Math.min(last.low, quote.price), close: quote.price };
      } else if (!last || bucket > last.time) {
        bar = { time: bucket, open: quote.price, high: quote.price, low: quote.price, close: quote.price };
      } else {
        return; // an old quote arriving late
      }
      lastBar.current = bar;
      seriesRef.current?.update(bar);
      setPrice(quote.price);
      setStatus("live");
    });
    return () => source.close();
  }, [instrumentId, interval]);

  const change = price !== null && open ? price - open : null;
  return (
    <figure style={{ margin: 0 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <div className="row">
          {instruments && onInstrumentChange ? (
            <span className="chart-picker">
              <InstrumentPicker instruments={instruments} value={instrumentId} onChange={onInstrumentChange} label="Chart instrument" />
            </span>
          ) : (
            <strong>{instrumentId}</strong>
          )}
          <span className="value" style={{ fontVariantNumeric: "tabular-nums" }}>{num(price)}</span>
          {change !== null && (
            <span className={`small ${tone(change)}`}>
              {signed(change)} ({((change / (open || 1)) * 100).toFixed(2)}%)
            </span>
          )}
          {meta?.simulated ? (
            <Badge kind="warn">SIMULATED</Badge>
          ) : status === "live" ? (
            <Badge kind="good">● LIVE</Badge>
          ) : (
            <Badge kind={status === "offline" ? "bad" : ""}>{status === "offline" ? "OFFLINE" : "CONNECTING"}</Badge>
          )}
          {meta && <span className="small muted">{meta.origin === "broker" ? "broker history + live" : "built from live quotes"}</span>}
        </div>
        <div className="segmented" role="group" aria-label="Candle interval">
          {INTERVALS.map(([value, label]) => (
            <button key={value} aria-pressed={interval === value} onClick={() => setIntervalSeconds(value)} className="small">
              {label}
            </button>
          ))}
        </div>
      </div>
      <div ref={box} style={{ width: "100%", height }} role="img" aria-label={`${instrumentId} live price chart`} />
      <figcaption className="sr-only">
        Live candlestick chart for {instrumentId}; last price {num(price)}.
      </figcaption>
    </figure>
  );
}
