import { useState } from "react";
import { post } from "../../api";
import { useApp } from "../../app-state";
import { Badge } from "../../components/ui";
import { num, time } from "../../format";

// ---- API shapes (only the fields the UI reads) --------------------------------------------------------

export interface Level {
  price: number;
  quantity: number;
  orders: number | null;
}

export interface Book {
  source: string;
  exchange_time: string;
  received: string;
  bids: Level[];
  asks: Level[];
  last_price: number | null;
  volume: number | null;
  open_interest: number | null;
  total_buy_quantity: number | null;
  total_sell_quantity: number | null;
  spread: number | null;
  capacity: number;
  simulated: boolean;
}

export interface Wall {
  wall_id: string;
  source: string;
  side: "BID" | "ASK";
  price: number;
  price_text: string;
  state: string;
  first_seen: string;
  removed_at: string | null;
  duration_seconds: number;
  peak_quantity: number;
  current_quantity: number;
  peak_orders: number | null;
  average_order_size: number | null;
  size_vs_typical: number | null;
  executed: number;
  cancelled: number;
  refilled: number;
  execution_share: number | null;
  price_at_detection: number | null;
  price_after: number | null;
  confirmed_by: string[];
}

export interface MarketEvent {
  event_id: string;
  instrument_id: string;
  kind: string;
  category: string;
  at: string;
  severity: string;
  title: string;
  source: string;
  data: Record<string, unknown>;
  parents: string[];
  explanation: string | null;
  meaning?: string;
}

export interface FlowBar {
  time: number;
  close: number;
  volume: number;
  buy: number;
  sell: number;
  delta: number;
}

export interface Flow {
  source: string;
  sources: string[];
  last_price: number | null;
  cvd: number;
  day_volume: number;
  vwap: number | null;
  delta_1m: number;
  delta_5m: number;
  buy_5m: number;
  sell_5m: number;
  aggressive_ratio_5m: number | null;
  volume_ratio: number | null;
  imbalance_top5: number | null;
  exchange_buy_sell_ratio: number | null;
  spread: number | null;
  spread_usual: number | null;
  median_trade: number | null;
  large_trades: { at: string; side: string; quantity: number; price: number }[];
  bars: FlowBar[];
  estimated: boolean;
}

export interface Fill {
  touch: number;
  average_price: number | null;
  fillable_in_view: number;
  unfilled_in_view: number;
  slippage: number | null;
  slippage_bps: number | null;
  levels_used: number;
}

export const SEVERITIES = ["INFO", "NOTICE", "WARNING", "CRITICAL"];

/** A price with as many decimals as its size needs (₹1,400.05, $4,000.02, 1.08543). */
export function px(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const digits = Math.abs(n) >= 1000 ? 2 : Math.abs(n) >= 10 ? 3 : 5;
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

// ---- widgets ---------------------------------------------------------------------------------------------

export function SimBadge({ on }: { on?: boolean }) {
  return on ? (
    <Badge kind="warn">
      <span title="Simulated demo data, not a real market">SIMULATED</span>
    </Badge>
  ) : null;
}

export function OrderBookLadder({ book, walls, levels = 10 }: { book: Book; walls: Wall[]; levels?: number }) {
  const asks = book.asks.slice(0, levels).reverse();
  const bids = book.bids.slice(0, levels);
  const max = Math.max(...[...asks, ...bids].map((l) => l.quantity), 1);
  const wallAt = new Set(
    walls.filter((w) => w.source === book.source && w.state !== "REMOVED").map((w) => `${w.side}:${w.price}`),
  );
  const row = (l: Level, side: "BID" | "ASK") => (
    <tr key={`${side}${l.price}`} className={`${side === "BID" ? "bid" : "ask"} ${wallAt.has(`${side}:${l.price}`) ? "wall" : ""}`}>
      <td className="num small muted">{l.orders ?? ""}</td>
      <td className="num">
        <span className="depth" style={{ width: `${(l.quantity / max) * 100}%` }} />
        {num(l.quantity, 4)}
      </td>
      <td className="num price">{px(l.price)}</td>
    </tr>
  );
  return (
    <table className="ladder" aria-label={`${book.source} order book`}>
      <thead>
        <tr>
          <th className="num">Orders</th>
          <th className="num">Quantity</th>
          <th className="num">Price</th>
        </tr>
      </thead>
      <tbody>
        {asks.map((l) => row(l, "ASK"))}
        <tr className="spread">
          <td colSpan={3}>
            spread {px(book.spread)} · last {px(book.last_price)}
          </td>
        </tr>
        {bids.map((l) => row(l, "BID"))}
      </tbody>
    </table>
  );
}

export function EventList({
  events,
  onSelect,
  showInstrument,
}: {
  events: MarketEvent[];
  onSelect?: (e: MarketEvent) => void;
  showInstrument?: boolean;
}) {
  if (!events.length) return <p className="muted small">No events yet.</p>;
  return (
    <ul className="event-list">
      {events.map((e) => (
        <li key={e.event_id}>
          <span className="small muted" style={{ minWidth: 70 }}>{new Date(e.at).toLocaleTimeString()}</span>
          <Badge>{e.kind.replaceAll("_", " ")}</Badge>
          {showInstrument && <span className="small muted">{e.instrument_id}</span>}
          {onSelect ? (
            <button className={`link sev-${e.severity}`} onClick={() => onSelect(e)}>
              {e.title}
            </button>
          ) : (
            <span className={`sev-${e.severity}`}>{e.title}</span>
          )}
          {e.source && <span className="small muted">{e.source}</span>}
        </li>
      ))}
    </ul>
  );
}

/** An event's facts, its plain-language explanation (AI or template) and what led to it. */
export function EventDetail({ event, onClose }: { event: MarketEvent; onClose?: () => void }) {
  const { run } = useApp();
  const [explained, setExplained] = useState<{ explanation: string; by: string; leading_events: MarketEvent[] } | null>(
    event.explanation ? { explanation: event.explanation, by: "saved", leading_events: [] } : null,
  );
  const [busy, setBusy] = useState(false);
  const explain = async () => {
    setBusy(true);
    const result = await run(() =>
      post<{ explanation: string; by: string; leading_events: MarketEvent[] }>(`/intelligence/events/${event.event_id}/explain`),
    );
    setBusy(false);
    if (result) setExplained(result);
  };
  return (
    <div className="card stack" style={{ boxShadow: "none" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap", alignItems: "flex-start" }}>
        <strong className={`sev-${event.severity}`}>{event.title}</strong>
        {onClose && (
          <button className="small" onClick={onClose} aria-label="Close event">
            ✕
          </button>
        )}
      </div>
      <div className="small muted">
        {event.kind} · {event.category} · {event.severity} · {time(event.at)} · {event.instrument_id}
        {event.source && ` · ${event.source}`}
      </div>
      {event.meaning && <div className="small">{event.meaning}</div>}
      <dl className="kv small">
        {Object.entries(event.data).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{k.replaceAll("_", " ")}</dt>
            <dd>{typeof v === "number" ? num(v, 4) : Array.isArray(v) ? v.join(", ") : String(v ?? "—")}</dd>
          </div>
        ))}
      </dl>
      {explained ? (
        <div className="alert small">
          {explained.explanation} <span className="muted">({explained.by === "template" ? "rule-based" : explained.by})</span>
          {explained.leading_events.length > 0 && (
            <div style={{ marginTop: 6 }}>
              Led up to by: {explained.leading_events.map((e) => e.title).join(" → ")}
            </div>
          )}
        </div>
      ) : (
        <div>
          <button className="small" onClick={explain} disabled={busy}>
            {busy ? "Explaining…" : "✦ Explain"}
          </button>
        </div>
      )}
    </div>
  );
}

export function Kpi({ label, value, tone, title }: { label: string; value: React.ReactNode; tone?: string; title?: string }) {
  return (
    <div className="card kpi" title={title}>
      <span className="label">{label}</span>
      <span className={`value ${tone ?? ""}`}>{value}</span>
    </div>
  );
}
