import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { ApiError, accountTrades, api, get, post, type Fill, type Instrument, type Order, type Position } from "../api";
import { describe, useApp } from "../app-state";
import { InstrumentPicker } from "../components/InstrumentPicker";
import { FillsTable, OrdersTable, PositionsTable } from "../components/tables";
import { Confirm, ModeBadge, Section, useData } from "../components/ui";
import { num } from "../format";

const CONFIRM_NOTIONAL = 10_000;
const PriceChart = lazy(() => import("../components/PriceChart").then((m) => ({ default: m.PriceChart })));

export function TradingPage() {
  const { can } = useApp();
  const [tab, setTab] = useState<"working" | "history" | "fills">("working");
  const [charted, setCharted] = useState("");
  const working = useData(() => get<Order[]>("/orders?working_only=true"), [], 2000);
  const history = useData(() => get<Order[]>("/orders"), [], 5000);
  const positions = useData(() => get<Position[]>("/positions"), [], 2000);
  const fills = useData(() => get<Fill[]>("/fills?limit=100"), [], 4000);
  const refresh = () => {
    working.reload();
    history.reload();
    positions.reload();
    fills.reload();
  };

  return (
    <div className="stack">
      <h1>Trading</h1>
      {charted && (
        <section className="card">
          <Suspense fallback={<div className="muted small">Loading chart…</div>}>
            <PriceChart instrumentId={charted} height={320} />
          </Suspense>
        </section>
      )}
      <div className="grid two" style={{ alignItems: "start" }}>
        {can("order:create") && <OrderTicket onSubmitted={refresh} onInstrument={setCharted} />}
        <Section title="Positions">
          <PositionsTable positions={positions.data ?? []} />
        </Section>
      </div>
      <section className="card">
        <div className="segmented" role="tablist" aria-label="Order views" style={{ marginBottom: 12 }}>
          {(["working", "history", "fills"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} aria-pressed={tab === t} onClick={() => setTab(t)}>
              {t === "working" ? `Working (${working.data?.length ?? 0})` : t === "history" ? "All orders" : "Fills"}
            </button>
          ))}
        </div>
        {tab === "working" && <OrdersTable orders={working.data ?? []} onChange={refresh} />}
        {tab === "history" && <OrdersTable orders={history.data ?? []} onChange={refresh} />}
        {tab === "fills" && <FillsTable fills={fills.data ?? []} />}
      </section>
    </div>
  );
}

function OrderTicket({ onSubmitted, onInstrument }: { onSubmitted: () => void; onInstrument?: (id: string) => void }) {
  const { accounts, notify } = useApp();
  const instruments = useData(() => get<Instrument[]>("/instruments"), [], 5000);
  const [accountId, setAccountId] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("LIMIT");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [postOnly, setPostOnly] = useState(false);
  const [reduceOnly, setReduceOnly] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!accountId && accounts.length) setAccountId(accounts[0].account_id);
  }, [accounts, accountId]);
  const account = accounts.find((a) => a.account_id === accountId);
  const available = useMemo(
    () => (instruments.data ?? []).filter((i) => accountTrades(account, i)),
    [instruments.data, account],
  );
  useEffect(() => {
    if (available.length && !available.some((i) => i.instrument_id === instrumentId)) setInstrumentId(available[0].instrument_id);
  }, [available, instrumentId]);
  const instrument = available.find((i) => i.instrument_id === instrumentId);
  useEffect(() => {
    if (instrumentId) onInstrument?.(instrumentId);
  }, [instrumentId, onInstrument]);
  const refPrice = instrument?.reference_price ? Number(instrument.reference_price) : null;
  const notional = Number(quantity || 0) * Number(type === "LIMIT" ? price || refPrice || 0 : refPrice || 0);

  const submit = async () => {
    setConfirming(false);
    setError(null);
    try {
      const order = await api<Order>(
        "POST",
        "/orders",
        {
          account_id: accountId,
          instrument_id: instrumentId,
          side,
          order_type: type,
          quantity,
          limit_price: type === "LIMIT" ? price : null,
          post_only: type === "LIMIT" && postOnly,
          reduce_only: reduceOnly,
        },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      notify(`${order.side} ${num(order.quantity)} ${order.instrument_id}: ${order.status.replaceAll("_", " ")}`);
      onSubmitted();
    } catch (e) {
      if (e instanceof ApiError) setError(e);
      else notify(describe(e), true);
      onSubmitted();
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (account?.mode === "LIVE" || notional >= CONFIRM_NOTIONAL) setConfirming(true);
    else submit();
  };

  return (
    <section className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Order ticket</h2>
        {account && <ModeBadge mode={account.mode} />}
      </div>
      <form className="stack" onSubmit={onSubmit}>
        <div className="form-grid">
          <label className="field">
            Account
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.map((a) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.name} ({a.mode})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Instrument
            <InstrumentPicker instruments={available} value={instrumentId} onChange={setInstrumentId} />
          </label>
        </div>
        <div className="row">
          <div className="segmented" role="group" aria-label="Side">
            <button type="button" className="buy" aria-pressed={side === "BUY"} onClick={() => setSide("BUY")}>
              Buy
            </button>
            <button type="button" className="sell" aria-pressed={side === "SELL"} onClick={() => setSide("SELL")}>
              Sell
            </button>
          </div>
          <div className="segmented" role="group" aria-label="Order type">
            {(["LIMIT", "MARKET"] as const).map((t) => (
              <button key={t} type="button" aria-pressed={type === t} onClick={() => setType(t)}>
                {t === "LIMIT" ? "Limit" : "Market"}
              </button>
            ))}
          </div>
          <span className="small muted">
            Ref. price {num(instrument?.reference_price)} · feed {instrument?.feed_status ?? "—"}
          </span>
        </div>
        <div className="form-grid">
          <label className="field">
            Quantity (lot {instrument ? num(instrument.lot_size) : "—"})
            <input required inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)}
              aria-invalid={error?.fieldErrors.some((f) => f.field === "quantity")} />
          </label>
          {type === "LIMIT" && (
            <label className="field">
              Limit price (tick {instrument ? num(instrument.tick_size) : "—"})
              <input required inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
            </label>
          )}
        </div>
        <div className="row">
          {type === "LIMIT" && (
            <label className="field inline">
              <input type="checkbox" checked={postOnly} onChange={(e) => setPostOnly(e.target.checked)} /> Post only
            </label>
          )}
          <label className="field inline">
            <input type="checkbox" checked={reduceOnly} onChange={(e) => setReduceOnly(e.target.checked)} /> Reduce only
          </label>
          <span className="spacer" style={{ flex: 1 }} />
          <span className="small muted">≈ {num(notional, 2)} {instrument?.quote_asset}</span>
        </div>
        {error && (
          <div className="alert error" role="alert">
            <strong>{error.code.replaceAll("_", " ")}</strong> — {describe(error)}
          </div>
        )}
        <button className={side === "BUY" ? "primary" : "danger"} type="submit" disabled={!accountId || !instrumentId}>
          {side === "BUY" ? "Buy" : "Sell"} {quantity || ""} {instrument?.base_asset ?? ""}
        </button>
      </form>
      {confirming && (
        <Confirm
          title="Confirm order"
          danger={account?.mode === "LIVE"}
          confirmLabel={`Send ${account?.mode === "LIVE" ? "live " : ""}order`}
          body={
            <>
              <p>
                {side} {quantity} {instrumentId} {type === "LIMIT" ? `at ${price}` : "at market"} on{" "}
                <strong>{account?.name}</strong> <ModeBadge mode={account?.mode ?? "PAPER"} />
              </p>
              <p className="muted">Estimated notional {num(notional, 2)} {instrument?.quote_asset}.</p>
            </>
          }
          onConfirm={submit}
          onCancel={() => setConfirming(false)}
        />
      )}
    </section>
  );
}

export async function pushQuote(instrumentId: string, bid: string, ask: string) {
  return post("/market-data/quotes", { instrument_id: instrumentId, bid_price: bid, bid_size: "1", ask_price: ask, ask_size: "1" });
}
