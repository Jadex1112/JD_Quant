import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get } from "../../api";
import { Profile, SignedBars } from "../../components/Bars";
import { Badge, Empty, Section, StatusBadge, useData } from "../../components/ui";
import { num, pct, signed, time, tone } from "../../format";
import { EventsTab, OptionsTab, ScannerTab } from "./MarketTabs";
import { HealthTab, NewsTab, ReplayTab, SettingsTab } from "./DataTabs";
import { ForecastTab, PayoffTab } from "./LabTabs";
import {
  px,
  EventDetail,
  EventList,
  Kpi,
  OrderBookLadder,
  SimBadge,
  type Book,
  type Fill,
  type Flow,
  type MarketEvent,
  type Wall,
} from "./shared";

const TABS = [
  ["overview", "Overview"],
  ["instrument", "Order book & flow"],
  ["scanner", "Scanner"],
  ["options", "Options & futures"],
  ["payoff", "Payoff lab"],
  ["forecast", "Forecast (Kronos)"],
  ["events", "Events"],
  ["replay", "Replay"],
  ["news", "News"],
  ["health", "Data health"],
  ["settings", "Settings"],
] as const;

interface Dashboard {
  at: string;
  instruments: {
    instrument_id: string;
    symbol: string;
    price: number | null;
    change_pct: number | null;
    regime: string | null;
    regime_confidence: number | null;
    trend: string | null;
    volatility: string | null;
    delta_5m: number | null;
    simulated: boolean;
  }[];
  breadth: number | null;
  events_last_hour: Record<string, number>;
  recent_events: MarketEvent[];
  options: { underlying: string; expiry: string; spot: number; pcr_oi: number | null; atm_iv: number | null; max_pain: number | null; simulated: boolean }[];
  scanner: { instrument_id: string; symbol: string; score: number; tags: Record<string, string> }[];
  health: { feeds: { source: string; seconds_since_message: number | null }[]; stale_feeds: string[] };
}

export function IntelligencePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "overview";
  const instrument = params.get("instrument") ?? "";
  const go = (next: string, instrumentId?: string) => {
    const p = new URLSearchParams(params);
    p.set("tab", next);
    if (instrumentId) p.set("instrument", instrumentId);
    setParams(p);
  };
  return (
    <div className="stack">
      <h1>Market intelligence</h1>
      <div className="tabs" role="tablist" aria-label="Market intelligence views">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => go(key)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview open={(iid) => go("instrument", iid)} />}
      {tab === "instrument" && <InstrumentTab instrumentId={instrument} pick={(iid) => go("instrument", iid)} />}
      {tab === "scanner" && <ScannerTab open={(iid) => go("instrument", iid)} />}
      {tab === "options" && <OptionsTab />}
      {tab === "payoff" && <PayoffTab />}
      {tab === "forecast" && <ForecastTab />}
      {tab === "events" && <EventsTab />}
      {tab === "replay" && <ReplayTab />}
      {tab === "news" && <NewsTab />}
      {tab === "health" && <HealthTab />}
      {tab === "settings" && <SettingsTab />}
    </div>
  );
}

function Overview({ open }: { open: (instrumentId: string) => void }) {
  const dash = useData(() => get<Dashboard>("/intelligence/dashboard"), [], 5000);
  const [selected, setSelected] = useState<MarketEvent | null>(null);
  const d = dash.data;
  if (!d) return <Empty>{dash.error ? "Market intelligence is unavailable." : "Loading…"}</Empty>;
  const categories = Object.entries(d.events_last_hour).sort((a, b) => b[1] - a[1]);
  return (
    <div className="stack">
      {d.instruments.length === 0 && (
        <div className="alert">
          Add instruments to the watchlist under <strong>Settings</strong> to follow their order books, order flow,
          VWAP, structure and events.
        </div>
      )}
      <div className="grid three">
        {d.instruments.map((i) => (
          <button key={i.instrument_id} className="card" style={{ textAlign: "left", cursor: "pointer" }} onClick={() => open(i.instrument_id)}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{i.symbol}</strong>
              <SimBadge on={i.simulated} />
            </div>
            <div className="kpi">
              <span className="value">{px(i.price)}</span>
              <span className={`small ${tone(i.change_pct)}`}>{i.change_pct == null ? "—" : `${signed(i.change_pct)}%`}</span>
            </div>
            <div className="small muted">
              {i.regime ? `${i.regime.replaceAll("_", " ").toLowerCase()} (${i.regime_confidence}%)` : "regime —"}
              {i.volatility && ` · volatility ${i.volatility.toLowerCase()}`}
            </div>
            <div className={`small ${tone(i.delta_5m)}`}>5-min delta {signed(i.delta_5m, 0)}</div>
          </button>
        ))}
      </div>
      <div className="grid two">
        <Section title="Events in the last hour">
          {categories.length === 0 ? (
            <p className="muted small">None.</p>
          ) : (
            <div className="metric-grid">
              {categories.map(([cat, n]) => (
                <Kpi key={cat} label={cat} value={n} />
              ))}
            </div>
          )}
          {d.breadth != null && <p className="small muted">Breadth: {pct(d.breadth, 0)} of watched instruments up on the day.</p>}
        </Section>
        <Section title="Options">
          {d.options.length === 0 ? (
            <p className="muted small">Add index underlyings (e.g. NIFTY) under Settings; Fyers or Dhan supply the chains.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Underlying</th><th>Expiry</th><th>Spot</th><th>PCR</th><th>ATM IV</th><th>Max pain</th></tr>
                </thead>
                <tbody>
                  {d.options.map((o) => (
                    <tr key={o.underlying}>
                      <td>{o.underlying} <SimBadge on={o.simulated} /></td>
                      <td>{o.expiry}</td>
                      <td>{num(o.spot)}</td>
                      <td>{o.pcr_oi?.toFixed(2) ?? "—"}</td>
                      <td>{pct(o.atm_iv, 1)}</td>
                      <td>{num(o.max_pain)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>
      <div className="grid two">
        <Section title="Latest events">
          <EventList events={d.recent_events} onSelect={setSelected} showInstrument />
          {selected && <EventDetail key={selected.event_id} event={selected} onClose={() => setSelected(null)} />}
        </Section>
        <Section title="Scanner highlights">
          {d.scanner.length === 0 ? (
            <p className="muted small">Run the scanner, or enable it under Settings to run every few minutes.</p>
          ) : (
            <ul className="event-list">
              {d.scanner.map((s) => (
                <li key={s.instrument_id}>
                  <button className="link" onClick={() => open(s.instrument_id)}>{s.symbol}</button>
                  <span className="small muted">score {s.score}</span>
                  <span className="small">{Object.keys(s.tags).join(" · ")}</span>
                </li>
              ))}
            </ul>
          )}
          {d.health.stale_feeds.length > 0 && (
            <div className="alert warn small" style={{ marginTop: 8 }}>Stale feeds: {d.health.stale_feeds.join(", ")}</div>
          )}
        </Section>
      </div>
    </div>
  );
}

interface Overview {
  instrument_id: string;
  symbol: string;
  quote_asset: string;
  watched: boolean;
  simulated: boolean;
  books: Record<string, Book>;
  primary_source: string | null;
  walls: { active: Wall[]; history: Wall[] };
  flow: Flow | null;
  analysis: Analysis;
  quality: { state: string; confirmed_by: string[]; sources: Record<string, { price: number | null; age_seconds: number | null }> } | null;
  futures: FuturesInfo | null;
  execution_estimate: { quantity: number; buy: Fill | null; sell: Fill | null };
  events: MarketEvent[];
  disclaimer: string;
}

interface Analysis {
  message?: string;
  origin?: string;
  bars?: number;
  last?: number;
  vwap?: {
    vwap: number;
    distance_pct: number | null;
    distance_sd: number | null;
    bands: Record<string, number>;
    slope: string;
    position: string;
    recent_cross: { kind: string } | null;
    volume_vs_average: number | null;
  } | null;
  profile?: { poc: number; vah: number; val: number; hvn: number[]; lvn: number[]; histogram: { price: number; volume: number }[]; source: string } | null;
  levels?: {
    day_high?: number;
    day_low?: number;
    day_open?: number;
    opening_range?: { high: number; low: number; minutes: number; complete: boolean };
    previous_day?: { high: number; low: number; close: number; date: string };
    zones: { low: number; high: number; touches: number; price: number; kind: string }[];
  };
  structure?: { trend: string; last_event: { kind: string; direction: string; level: number } | null };
  timeframes?: { timeframe: string; trend: string | null; bars: number; last_event?: { kind: string; direction: string } | null }[];
  alignment?: { verdict: string };
  regime?: { state: string; labels: string[]; trend?: string; volatility?: string; confidence: number; evidence: string[] };
}

function InstrumentTab({ instrumentId, pick }: { instrumentId: string; pick: (iid: string) => void }) {
  const settings = useData(() => get<{ watch: string[] }>("/intelligence/settings"), []);
  const [text, setText] = useState(instrumentId);
  const watch = settings.data?.watch ?? [];
  const current = instrumentId || watch[0] || "";
  return (
    <div className="stack">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) pick(text.trim());
        }}
      >
        {watch.length > 0 && (
          <select aria-label="Watched instrument" value={watch.includes(current) ? current : ""} onChange={(e) => pick(e.target.value)}>
            <option value="" disabled>Watchlist…</option>
            {watch.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
        )}
        <input aria-label="Instrument id" placeholder="e.g. NSE:RELIANCE" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit">Open</button>
      </form>
      {current ? <InstrumentView key={current} instrumentId={current} /> : <Empty>Add instruments to the watchlist under Settings.</Empty>}
    </div>
  );
}

function InstrumentView({ instrumentId }: { instrumentId: string }) {
  const data = useData(() => get<Overview>(`/intelligence/instruments/${encodeURIComponent(instrumentId)}?levels=15`), [instrumentId], 2000);
  const [source, setSource] = useState<string | null>(null);
  const [selected, setSelected] = useState<MarketEvent | null>(null);
  const o = data.data;
  if (!o) return <Empty>{data.error ? `No data for ${instrumentId}.` : "Loading…"}</Empty>;
  const sources = Object.keys(o.books);
  const shown = source && o.books[source] ? source : o.primary_source ?? sources[0];
  const book = shown ? o.books[shown] : undefined;
  const flow = o.flow;
  const a = o.analysis;
  return (
    <div className="stack">
      <div className="row">
        <h2 style={{ margin: 0 }}>{o.symbol}</h2>
        <span className="muted small">{o.instrument_id}</span>
        <SimBadge on={o.simulated} />
        {!o.watched && <Badge kind="warn">not on the watchlist: depth is not polled</Badge>}
        {o.quality && <StatusBadge status={o.quality.state} />}
      </div>

      <div className="grid two">
        <Section
          title="Order book"
          actions={
            sources.length > 1 && (
              <div className="segmented" role="group" aria-label="Book source">
                {sources.map((s) => (
                  <button key={s} className="small" aria-pressed={s === shown} onClick={() => setSource(s)}>{s}</button>
                ))}
              </div>
            )
          }
        >
          {book ? (
            <>
              <OrderBookLadder book={book} walls={o.walls.active} levels={15} />
              <p className="small muted">
                {book.source} · {book.capacity} levels · updated {new Date(book.received).toLocaleTimeString()}
                {book.total_buy_quantity != null && ` · total bid ${num(book.total_buy_quantity, 0)} / ask ${num(book.total_sell_quantity, 0)}`}
                {sources.length > 1 && " · each broker's book is shown separately, never added together"}
              </p>
            </>
          ) : (
            <Empty>No order book yet. Watch this instrument and connect a broker that supplies depth.</Empty>
          )}
        </Section>
        <Section title="Order flow">
          {flow ? (
            <div className="stack">
              <div className="metric-grid">
                <Kpi label="CVD (day)" value={signed(flow.cvd, 0)} tone={tone(flow.cvd)} />
                <Kpi label="Delta 1 min" value={signed(flow.delta_1m, 0)} tone={tone(flow.delta_1m)} />
                <Kpi label="Delta 5 min" value={signed(flow.delta_5m, 0)} tone={tone(flow.delta_5m)} />
                <Kpi label="Buy/sell 5 min" value={flow.aggressive_ratio_5m?.toFixed(2) ?? "—"} />
                <Kpi label="Top-5 imbalance" value={flow.imbalance_top5 == null ? "—" : pct(flow.imbalance_top5, 0)} title="(bid − ask) / (bid + ask) quantity in the top five levels" />
                <Kpi label="Volume vs usual" value={flow.volume_ratio == null ? "—" : `${flow.volume_ratio}×`} />
                <Kpi label="Spread (usual)" value={`${px(flow.spread)} (${px(flow.spread_usual)})`} />
                <Kpi label="Session VWAP" value={px(flow.vwap)} />
              </div>
              <div>
                <h3>Delta per minute</h3>
                <SignedBars
                  label="Buy minus sell volume per minute"
                  values={flow.bars.slice(-60).map((b) => ({ key: b.time, value: b.delta, title: `${new Date(b.time * 1000).toLocaleTimeString()}: ${signed(b.delta, 0)}` }))}
                />
              </div>
              {flow.large_trades.length > 0 && (
                <div>
                  <h3>Large trades</h3>
                  <ul className="event-list small">
                    {flow.large_trades.slice(-8).reverse().map((t) => (
                      <li key={t.at + t.price}>
                        <span className="muted">{new Date(t.at).toLocaleTimeString()}</span>
                        <span className={t.side === "BUY" ? "pos" : "neg"}>{t.side}</span>
                        {num(t.quantity, 0)} @ {px(t.price)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="small muted">
                Trades are inferred from volume changes between book updates, and the aggressor from where they printed
                against the quote; feeds do not identify participants.
              </p>
            </div>
          ) : (
            <Empty>No order flow yet.</Empty>
          )}
        </Section>
      </div>

      <Section title="Liquidity walls">
        <WallTable walls={o.walls.active} empty="No active walls." />
        {o.walls.history.length > 0 && (
          <>
            <h3 style={{ marginTop: 16 }}>Recently ended</h3>
            <WallTable walls={o.walls.history.slice(0, 15)} ended />
          </>
        )}
        <p className="small muted">{o.disclaimer}</p>
      </Section>

      {a.message ? (
        <div className="alert small">{a.message}</div>
      ) : (
        <div className="grid two">
          <Section title="VWAP and volume profile">
            {a.vwap && (
              <dl className="kv">
                <dt>VWAP</dt><dd>{px(a.vwap.vwap)} · price {a.vwap.position.toLowerCase()} by {signed(a.vwap.distance_pct)}% ({a.vwap.distance_sd?.toFixed(1) ?? "—"} SD)</dd>
                <dt>Slope</dt><dd>{a.vwap.slope.toLowerCase()}</dd>
                <dt>Bands ±1/±2 SD</dt><dd>{px(a.vwap.bands.lower2)} · {px(a.vwap.bands.lower1)} · {px(a.vwap.bands.upper1)} · {px(a.vwap.bands.upper2)}</dd>
                {a.vwap.recent_cross && <><dt>Recent</dt><dd>{a.vwap.recent_cross.kind.replaceAll("_", " ").toLowerCase()}</dd></>}
              </dl>
            )}
            {a.profile && (
              <>
                <dl className="kv">
                  <dt>POC</dt><dd>{px(a.profile.poc)}</dd>
                  <dt>Value area</dt><dd>{px(a.profile.val)} – {px(a.profile.vah)}</dd>
                  <dt>High-volume nodes</dt><dd>{a.profile.hvn.map((p) => px(p)).join(", ") || "—"}</dd>
                  <dt>Low-volume nodes</dt><dd>{a.profile.lvn.map((p) => px(p)).join(", ") || "—"}</dd>
                </dl>
                <Profile
                  label="Volume profile for today"
                  rows={a.profile.histogram}
                  marks={[
                    { price: a.profile.poc, name: "POC" },
                    { price: a.profile.vah, name: "VAH" },
                    { price: a.profile.val, name: "VAL" },
                    { price: a.last, name: "price", kind: "price" },
                  ]}
                />
                <p className="small muted">From {a.profile.source === "trades" ? "inferred trades" : "one-minute bars"}.</p>
              </>
            )}
          </Section>
          <Section title="Structure and regime">
            {a.regime && (
              <>
                <div className="row">
                  <Badge kind="ai">{a.regime.state.replaceAll("_", " ")}</Badge>
                  <span className="small">confidence {a.regime.confidence}%</span>
                  {a.regime.labels.slice(1).map((l) => <Badge key={l}>{l.replaceAll("_", " ")}</Badge>)}
                </div>
                <ul className="small" style={{ paddingLeft: 18 }}>
                  {a.regime.evidence.map((e) => <li key={e}>{e}</li>)}
                </ul>
              </>
            )}
            {a.alignment && <p><strong>{a.alignment.verdict}</strong></p>}
            {a.timeframes && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Timeframe</th><th>Trend</th><th>Last break</th><th>Bars</th></tr></thead>
                  <tbody>
                    {a.timeframes.map((t) => (
                      <tr key={t.timeframe}>
                        <td>{t.timeframe}</td>
                        <td className={t.trend === "BULLISH" ? "pos" : t.trend === "BEARISH" ? "neg" : ""}>{t.trend?.toLowerCase() ?? "—"}</td>
                        <td>{t.last_event ? `${t.last_event.kind} ${t.last_event.direction.toLowerCase()}` : "—"}</td>
                        <td>{t.bars}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {a.levels && (
              <dl className="kv">
                <dt>Today</dt><dd>open {px(a.levels.day_open)} · high {px(a.levels.day_high)} · low {px(a.levels.day_low)}</dd>
                {a.levels.opening_range && (
                  <><dt>Opening range ({a.levels.opening_range.minutes} min)</dt><dd>{px(a.levels.opening_range.low)} – {px(a.levels.opening_range.high)}{a.levels.opening_range.complete ? "" : " (forming)"}</dd></>
                )}
                {a.levels.previous_day && (
                  <><dt>Previous day</dt><dd>high {px(a.levels.previous_day.high)} · low {px(a.levels.previous_day.low)} · close {px(a.levels.previous_day.close)}</dd></>
                )}
                {a.levels.zones.map((z) => (
                  <div key={z.price} style={{ display: "contents" }}>
                    <dt>{z.kind.toLowerCase()}</dt><dd>{px(z.low)} – {px(z.high)} ({z.touches} touches)</dd>
                  </div>
                ))}
              </dl>
            )}
            <p className="small muted">From {a.bars} one-minute bars ({a.origin}).</p>
          </Section>
        </div>
      )}

      <div className="grid two">
        <Section title="Execution estimate">
          <p className="small muted">What {num(o.execution_estimate.quantity)} units would cost now, walking the visible book.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Side</th><th>Touch</th><th>Average</th><th>Slippage</th><th>Levels</th><th>Unfilled in view</th></tr></thead>
              <tbody>
                {(["buy", "sell"] as const).map((side) => {
                  const f = o.execution_estimate[side];
                  return (
                    <tr key={side}>
                      <td>{side}</td>
                      <td>{px(f?.touch)}</td>
                      <td>{px(f?.average_price)}</td>
                      <td>{f?.slippage_bps == null ? "—" : `${f.slippage_bps.toFixed(1)} bps`}</td>
                      <td>{f?.levels_used ?? "—"}</td>
                      <td>{num(f?.unfilled_in_view)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {o.futures && (
            <>
              <h3 style={{ marginTop: 16 }}>Futures</h3>
              <FuturesView view={o.futures} />
            </>
          )}
        </Section>
        <Section title="Events">
          <EventList events={o.events.slice(0, 30)} onSelect={setSelected} />
          {selected && <EventDetail key={selected.event_id} event={selected} onClose={() => setSelected(null)} />}
        </Section>
      </div>
      <p className="small muted">Updated {time(new Date().toISOString())}. Analysis describes the market; it does not predict it.</p>
    </div>
  );
}

export interface FuturesInfo {
  message?: string;
  price?: number;
  open_interest?: number;
  expiry?: string | null;
  positioning?: { state: string | null; reading: string; price_change_pct?: number; oi_change_pct?: number };
  basis?: { basis: number; basis_pct: number | null; days_to_expiry: number | null; annualized_pct: number | null; state: string };
  reference?: string;
  calendar?: { near: string; far: string; near_expiry: string; far_expiry: string; spread: number; spread_pct: number | null; state: string }[];
}

export function FuturesView({ view }: { view: FuturesInfo }) {
  if (view.message) return <p className="small muted">{view.message}</p>;
  const pos = view.positioning;
  return (
    <dl className="kv small">
      <dt>Price</dt><dd>{px(view.price)}</dd>
      <dt>Open interest</dt><dd>{num(view.open_interest, 0)}</dd>
      {view.expiry && <><dt>Expiry</dt><dd>{view.expiry.slice(0, 10)}</dd></>}
      {pos && (
        <>
          <dt>Positioning</dt>
          <dd>
            <strong>{pos.state?.replaceAll("_", " ").toLowerCase() ?? "—"}</strong>: {pos.reading}
            {pos.price_change_pct != null && ` (price ${signed(pos.price_change_pct)}%, OI ${signed(pos.oi_change_pct)}% since ${view.reference})`}
          </dd>
        </>
      )}
      {view.basis && (
        <>
          <dt>Basis</dt>
          <dd>
            {view.basis.state.toLowerCase()} {signed(view.basis.basis)} ({signed(view.basis.basis_pct)}%)
            {view.basis.annualized_pct != null && `, ${view.basis.annualized_pct.toFixed(1)}% a year`}
          </dd>
        </>
      )}
      {view.calendar && view.calendar.length > 0 && (
        <>
          <dt>Calendar spreads</dt>
          <dd>
            {view.calendar.map((c) => (
              <div key={c.near + c.far}>
                {c.near_expiry} → {c.far_expiry}: {signed(c.spread)} ({signed(c.spread_pct)}%) {c.state.toLowerCase()}
              </div>
            ))}
          </dd>
        </>
      )}
    </dl>
  );
}

function WallTable({ walls, empty, ended }: { walls: Wall[]; empty?: string; ended?: boolean }) {
  if (!walls.length) return <p className="muted small">{empty ?? "None."}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Side</th><th>Price</th><th>Source</th><th>State</th><th>Peak qty</th><th>Orders</th><th>× typical</th>
            <th>Lasted</th><th>Traded</th><th>Cancelled</th><th>Executed share</th><th>Also seen by</th>
          </tr>
        </thead>
        <tbody>
          {walls.map((w) => (
            <tr key={w.wall_id}>
              <td className={w.side === "BID" ? "pos" : "neg"}>{w.side}</td>
              <td>{w.price_text}</td>
              <td className="small">{w.source}</td>
              <td><Badge kind={w.state === "CONSUMED" ? "good" : w.state === "WITHDRAWN" ? "warn" : ""}>{w.state.replaceAll("_", " ")}</Badge></td>
              <td>{num(w.peak_quantity, 0)}</td>
              <td>{w.peak_orders ?? "—"}</td>
              <td>{w.size_vs_typical ?? "—"}</td>
              <td>{w.duration_seconds < 90 ? `${w.duration_seconds.toFixed(0)}s` : `${(w.duration_seconds / 60).toFixed(1)}m`}</td>
              <td>{num(w.executed, 0)}</td>
              <td>{num(w.cancelled, 0)}</td>
              <td>{w.execution_share == null ? "—" : pct(w.execution_share, 0)}</td>
              <td className="small">{w.confirmed_by.join(", ") || (ended ? "—" : "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
