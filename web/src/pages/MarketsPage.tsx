import { lazy, Suspense, useEffect, useState } from "react";
import { get, type Instrument } from "../api";
import { useApp } from "../app-state";
import { Dialog, Empty, Section, StatusBadge, useData } from "../components/ui";
import { num } from "../format";
import { pushQuote } from "./TradingPage";

const PriceChart = lazy(() => import("../components/PriceChart").then((m) => ({ default: m.PriceChart })));

export function MarketsPage() {
  const { can, run } = useApp();
  const [venue, setVenue] = useState("");
  const [query, setQuery] = useState("");
  const [quoteFor, setQuoteFor] = useState<Instrument | null>(null);
  const [charted, setCharted] = useState("");
  const instruments = useData(() => get<Instrument[]>("/instruments"), [], 3000);
  const all = instruments.data ?? [];
  const venues = [...new Set(all.map((i) => i.venue))].sort();
  const shown = all.filter(
    (i) => (!venue || i.venue === venue) && (!query || i.instrument_id.toLowerCase().includes(query.toLowerCase())),
  );

  useEffect(() => {
    if (!charted && all.length) setCharted((all.find((i) => i.instrument_id === "OANDA:XAU_USD") ?? all[0]).instrument_id);
  }, [all, charted]);

  return (
    <div className="stack">
      <h1>Markets</h1>
      {charted && (
        <section className="card">
          <Suspense fallback={<div className="muted small">Loading chart…</div>}>
            <PriceChart instrumentId={charted} instruments={all} onInstrumentChange={setCharted} />
          </Suspense>
        </section>
      )}
      <Section
        title={`Instruments (${shown.length})`}
        actions={
          <>
            <label className="sr-only" htmlFor="mk-search">Search instruments</label>
            <input id="mk-search" placeholder="Search…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <label className="sr-only" htmlFor="mk-venue">Venue</label>
            <select id="mk-venue" value={venue} onChange={(e) => setVenue(e.target.value)}>
              <option value="">All venues</option>
              {venues.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </>
        }
      >
        {shown.length === 0 ? (
          <Empty>{instruments.loading ? "Loading…" : "No instruments match."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Venue</th>
                  <th>Class</th>
                  <th className="num">Reference price</th>
                  <th>Feed</th>
                  <th className="num">Tick</th>
                  <th className="num">Lot</th>
                  <th className="num">Min notional</th>
                  <th>Status</th>
                  {can("marketdata:manage_reference") && <th><span className="sr-only">Actions</span></th>}
                </tr>
              </thead>
              <tbody>
                {shown.map((i) => (
                  <tr key={i.instrument_id}>
                    <td>
                      <button className="link" onClick={() => setCharted(i.instrument_id)} aria-pressed={charted === i.instrument_id}>
                        <strong>{i.symbol}</strong>
                      </button>{" "}
                      <span className="small muted">{i.instrument_id}</span>
                    </td>
                    <td>{i.venue}</td>
                    <td className="small">{i.asset_class}</td>
                    <td className="num">{num(i.reference_price)}</td>
                    <td><StatusBadge status={i.feed_status} /></td>
                    <td className="num">{num(i.tick_size)}</td>
                    <td className="num">{num(i.lot_size)}</td>
                    <td className="num">{num(i.min_notional)}</td>
                    <td><StatusBadge status={i.status} /></td>
                    {can("marketdata:manage_reference") && (
                      <td>
                        <button className="small" onClick={() => setQuoteFor(i)}>Set quote</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted" style={{ marginBottom: 0 }}>
          Live venues are polled for quotes while connected. Paper instruments use quotes set here or by the simulator.
        </p>
      </Section>
      {quoteFor && (
        <QuoteDialog
          instrument={quoteFor}
          onClose={() => setQuoteFor(null)}
          onSave={async (bid, ask) => {
            const ok = await run(async () => {
              await pushQuote(quoteFor.instrument_id, bid, ask);
              return true;
            }, "Quote updated");
            if (ok) {
              setQuoteFor(null);
              instruments.reload();
            }
          }}
        />
      )}
    </div>
  );
}

function QuoteDialog({
  instrument,
  onClose,
  onSave,
}: {
  instrument: Instrument;
  onClose: () => void;
  onSave: (bid: string, ask: string) => void;
}) {
  const ref = instrument.reference_price ?? "";
  const [bid, setBid] = useState(ref);
  const [ask, setAsk] = useState(ref);
  return (
    <Dialog title={`Quote for ${instrument.instrument_id}`} onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(bid, ask);
        }}
      >
        <div className="form-grid">
          <label className="field">
            Bid
            <input required inputMode="decimal" value={bid} onChange={(e) => setBid(e.target.value)} />
          </label>
          <label className="field">
            Ask
            <input required inputMode="decimal" value={ask} onChange={(e) => setAsk(e.target.value)} />
          </label>
        </div>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Publish quote</button>
        </div>
      </form>
    </Dialog>
  );
}
