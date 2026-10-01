import { useState } from "react";
import { del, patch, type Fill, type Order, type Position } from "../api";
import { useApp } from "../app-state";
import { num, signed, time, tone } from "../format";
import { Dialog, Empty, StatusBadge } from "./ui";

const WORKING = new Set(["OPEN", "PARTIALLY_FILLED"]);

export function OrdersTable({ orders, onChange, compact }: { orders: Order[]; onChange?: () => void; compact?: boolean }) {
  const { run, can } = useApp();
  const [modify, setModify] = useState<Order | null>(null);
  if (!orders.length) return <Empty>No orders.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Instrument</th>
            <th>Side</th>
            <th>Type</th>
            <th className="num">Qty</th>
            <th className="num">Filled</th>
            <th className="num">Price</th>
            <th className="num">Avg fill</th>
            <th>Status</th>
            {!compact && <th>Account</th>}
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.order_id}>
              <td className="small">{time(o.created_at)}</td>
              <td>{o.instrument_id}</td>
              <td className={o.side === "BUY" ? "pos" : "neg"}>{o.side === "BUY" ? "▲ BUY" : "▼ SELL"}</td>
              <td>{o.order_type}</td>
              <td className="num">{num(o.quantity)}</td>
              <td className="num">{num(o.filled_quantity)}</td>
              <td className="num">{num(o.limit_price)}</td>
              <td className="num">{num(o.average_fill_price)}</td>
              <td title={o.reject_reason ?? undefined}>
                <StatusBadge status={o.status} />
                {o.reject_code && <span className="small muted"> {o.reject_code}</span>}
              </td>
              {!compact && <td className="small">{o.account_id}</td>}
              <td>
                {WORKING.has(o.status) && (
                  <span className="row">
                    {can("order:modify") && o.order_type === "LIMIT" && (
                      <button className="small" onClick={() => setModify(o)}>Modify</button>
                    )}
                    {can("order:cancel") && (
                      <button
                        className="small"
                        onClick={async () => {
                          await run(() => del(`/orders/${o.order_id}`), "Cancel requested");
                          onChange?.();
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {modify && (
        <ModifyDialog
          order={modify}
          onClose={() => {
            setModify(null);
            onChange?.();
          }}
        />
      )}
    </div>
  );
}

function ModifyDialog({ order, onClose }: { order: Order; onClose: () => void }) {
  const { run } = useApp();
  const [quantity, setQuantity] = useState(order.quantity);
  const [price, setPrice] = useState(order.limit_price ?? "");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const done = await run(
      () => patch(`/orders/${order.order_id}`, { quantity, limit_price: price }),
      "Modification sent",
    );
    if (done) onClose();
  };
  return (
    <Dialog title={`Modify ${order.side} ${order.instrument_id}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="muted small" style={{ margin: 0 }}>
          Filled so far: {num(order.filled_quantity)}. A larger size or a more aggressive price is re-checked by risk.
        </p>
        <div className="form-grid">
          <label className="field">
            Total quantity
            <input value={quantity} onChange={(e) => setQuantity(e.target.value)} inputMode="decimal" />
          </label>
          <label className="field">
            Limit price
            <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" />
          </label>
        </div>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Modify order</button>
        </div>
      </form>
    </Dialog>
  );
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  if (!positions.length) return <Empty>No open positions.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Instrument</th>
            <th>Account</th>
            <th>Deployment</th>
            <th className="num">Quantity</th>
            <th className="num">Avg entry</th>
            <th className="num">Unrealized P&amp;L</th>
            <th className="num">Realized P&amp;L</th>
            <th className="num">Fees</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={`${p.account_id}-${p.instrument_id}-${p.deployment_id}`}>
              <td>{p.instrument_id}</td>
              <td className="small">{p.account_id}</td>
              <td className="small">{p.deployment_id.startsWith("MANUAL") ? "manual" : p.deployment_id}</td>
              <td className={`num ${tone(p.quantity)}`}>{num(p.quantity)}</td>
              <td className="num">{num(p.average_entry_price)}</td>
              <td className={`num ${tone(p.unrealized_pnl)}`}>{signed(p.unrealized_pnl)}</td>
              <td className={`num ${tone(p.realized_pnl)}`}>{signed(p.realized_pnl)}</td>
              <td className="num">{num(p.fees_paid, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FillsTable({ fills }: { fills: Fill[] }) {
  if (!fills.length) return <Empty>No fills yet.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Instrument</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th className="num">Price</th>
            <th className="num">Fee</th>
            <th>Liquidity</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f) => (
            <tr key={f.fill_id}>
              <td className="small">{time(f.exchange_ts)}</td>
              <td>{f.instrument_id}</td>
              <td className={f.side === "BUY" ? "pos" : "neg"}>{f.side}</td>
              <td className="num">{num(f.quantity)}</td>
              <td className="num">{num(f.price)}</td>
              <td className="num">{num(f.fee, 6)} {f.fee_asset}</td>
              <td className="small">{f.liquidity}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
