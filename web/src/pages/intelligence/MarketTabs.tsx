import { useEffect, useState } from "react";
import { get, post } from "../../api";
import { useApp } from "../../app-state";
import { Badge, Empty, Section, useData } from "../../components/ui";
import { num, pct, signed, time, tone } from "../../format";
import { FuturesView, type FuturesInfo } from "./IntelligencePage";
import { EventDetail, EventList, Kpi, SEVERITIES, SimBadge, px, type MarketEvent } from "./shared";

// ---- scanner ---------------------------------------------------------------------------------------------

interface ScanResult {
  at: string | null;
  universe: number;
  priced?: number;
  results: {
    instrument_id: string;
    symbol: string;
    last?: number | null;
    change_pct?: number;
    gap_pct?: number;
    volume_ratio?: number;
    tags: Record<string, string>;
    score: number;
  }[];
  categories?: Record<string, number>;
}

export function ScannerTab({ open }: { open: (instrumentId: string) => void }) {
  const { run } = useApp();
  const scan = useData(() => get<ScanResult>("/intelligence/scanner"), [], 15000);
  const [filter, setFilter] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const runNow = async () => {
    setBusy(true);
    const result = await run(() => post<ScanResult>("/intelligence/scanner/run"));
    setBusy(false);
    if (result) scan.setData(result);
  };
  const s = scan.data;
  const rows = (s?.results ?? []).filter((r) => !filter || filter in r.tags);
  return (
    <Section
      title="Market scanner"
      actions={<button className="primary" onClick={runNow} disabled={busy}>{busy ? "Scanning…" : "Scan now"}</button>}
    >
      <p className="small muted">
        Scans the scanner universe (or the watchlist) for unusual volume, big moves, gaps, breakouts and recent order-book,
        flow, options and news events. {s?.at ? `Last run ${time(s.at)} over ${s.universe} instruments.` : "Not run yet."}
      </p>
      {s?.categories && (
        <div className="chips" style={{ marginBottom: 12 }}>
          <button className="small" aria-pressed={!filter} onClick={() => setFilter(null)}>All ({s.results.length})</button>
          {Object.entries(s.categories)
            .filter(([, n]) => n > 0)
            .map(([c, n]) => (
              <button key={c} className="small" aria-pressed={filter === c} onClick={() => setFilter(c)}>
                {c} ({n})
              </button>
            ))}
        </div>
      )}
      {rows.length === 0 ? (
        <Empty>Nothing stands out.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Instrument</th><th>Score</th><th>Last</th><th>Change</th><th>Volume vs normal</th><th>Why</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.instrument_id}>
                  <td><button className="link" onClick={() => open(r.instrument_id)}>{r.symbol}</button> <span className="small muted">{r.instrument_id}</span></td>
                  <td>{r.score}</td>
                  <td>{px(r.last)}</td>
                  <td className={tone(r.change_pct)}>{r.change_pct == null ? "—" : `${signed(r.change_pct)}%`}</td>
                  <td>{r.volume_ratio == null ? "—" : `${r.volume_ratio}×`}</td>
                  <td className="small" style={{ whiteSpace: "normal" }}>
                    {Object.entries(r.tags).map(([k, v]) => <div key={k}><strong>{k}</strong>: {v}</div>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// ---- options and futures -----------------------------------------------------------------------------

interface OptionLeg {
  ltp: number | null;
  oi: number | null;
  oi_change: number | null;
  volume: number | null;
  iv: number | null;
  delta: number | null;
}

interface Chain {
  underlying: string;
  expiry: string;
  expiries: string[];
  spot: number;
  at: string;
  source: string;
  simulated: boolean;
  atm: number | null;
  pcr_oi: number | null;
  pcr_volume: number | null;
  call_oi_change: number;
  put_oi_change: number;
  max_pain: number | null;
  atm_iv: number | null;
  expected_move: number | null;
  days_to_expiry: number;
  skew: { put_25d_iv: number; call_25d_iv: number; risk_reversal: number } | null;
  resistance: { strike: number; oi: number | null }[];
  support: { strike: number; oi: number | null }[];
  observations: string[];
  rows: { strike: number; call: OptionLeg | null; put: OptionLeg | null }[];
}

export function OptionsTab() {
  const { run } = useApp();
  const settings = useData(() => get<{ option_underlyings: string[]; chain_sources: Record<string, string[]> }>("/intelligence/settings"), []);
  const choices = [...new Set([...(settings.data?.option_underlyings ?? []), ...Object.keys(settings.data?.chain_sources ?? {}), "NIFTY", "BANKNIFTY"])];
  const [underlying, setUnderlying] = useState("");
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<Chain | null>(null);
  const [future, setFuture] = useState("");
  const [futures, setFutures] = useState<FuturesInfo | null>(null);
  const current = underlying || choices[0] || "NIFTY";

  const load = async (refresh = false) => {
    const q = new URLSearchParams();
    if (refresh) q.set("refresh", "true");
    if (expiry) q.set("expiry", expiry);
    const result = await run(() => get<Chain>(`/intelligence/options/${encodeURIComponent(current)}?${q}`));
    if (result) setChain(result);
  };
  useEffect(() => {
    setChain(null);
    if (settings.data) load();
  }, [current, expiry, settings.data]);

  const maxOi = Math.max(1, ...(chain?.rows ?? []).flatMap((r) => [r.call?.oi ?? 0, r.put?.oi ?? 0]));
  return (
    <div className="stack">
      <Section
        title="Option chain"
        actions={
          <>
            <select aria-label="Underlying" value={current} onChange={(e) => { setUnderlying(e.target.value); setExpiry(""); }}>
              {choices.map((c) => <option key={c}>{c}</option>)}
            </select>
            {chain && (
              <select aria-label="Expiry" value={expiry || chain.expiry} onChange={(e) => setExpiry(e.target.value)}>
                {(chain.expiries.length ? chain.expiries : [chain.expiry]).map((d) => <option key={d}>{d}</option>)}
              </select>
            )}
            <button onClick={() => load(true)}>Refresh</button>
          </>
        }
      >
        {!chain ? (
          <Empty>Loading the chain… Fyers or Dhan supply NIFTY, BANKNIFTY, FINNIFTY and MIDCPNIFTY chains.</Empty>
        ) : (
          <div className="stack">
            <div className="row">
              <SimBadge on={chain.simulated} />
              <span className="small muted">{chain.source} · {time(chain.at)} · {chain.days_to_expiry} days to expiry</span>
            </div>
            <div className="metric-grid">
              <Kpi label="Spot" value={num(chain.spot)} />
              <Kpi label="PCR (OI)" value={chain.pcr_oi?.toFixed(2) ?? "—"} />
              <Kpi label="PCR (volume)" value={chain.pcr_volume?.toFixed(2) ?? "—"} />
              <Kpi label="Max pain" value={num(chain.max_pain)} />
              <Kpi label="ATM IV" value={pct(chain.atm_iv, 1)} />
              <Kpi label="Expected move" value={chain.expected_move == null ? "—" : `±${num(chain.expected_move, 0)}`} />
              <Kpi label="25Δ risk reversal" value={chain.skew ? `${(chain.skew.risk_reversal * 100).toFixed(1)} pts` : "—"} title="Put IV minus call IV near 25 delta; positive means puts are richer" />
              <Kpi label="OI change calls / puts" value={`${signed(chain.call_oi_change, 0)} / ${signed(chain.put_oi_change, 0)}`} />
            </div>
            <ul className="small">{chain.observations.map((o) => <li key={o}>{o}</li>)}</ul>
            <div className="table-wrap" style={{ maxHeight: 520 }}>
              <table>
                <thead>
                  <tr>
                    <th>Call OI</th><th>OI chg</th><th>IV</th><th>Δ</th><th>LTP</th>
                    <th>Strike</th>
                    <th>LTP</th><th>Δ</th><th>IV</th><th>OI chg</th><th>Put OI</th>
                  </tr>
                </thead>
                <tbody>
                  {chain.rows.map((r) => (
                    <tr key={r.strike} style={r.strike === chain.atm ? { outline: "2px solid var(--accent)" } : undefined}>
                      <td style={{ background: `linear-gradient(to left, color-mix(in srgb, var(--neg) 22%, transparent) ${((r.call?.oi ?? 0) / maxOi) * 100}%, transparent 0)` }}>{num(r.call?.oi, 0)}</td>
                      <td className={tone(r.call?.oi_change)}>{signed(r.call?.oi_change, 0)}</td>
                      <td>{r.call?.iv ?? "—"}</td>
                      <td>{r.call?.delta ?? "—"}</td>
                      <td>{num(r.call?.ltp)}</td>
                      <td><strong>{num(r.strike)}</strong></td>
                      <td>{num(r.put?.ltp)}</td>
                      <td>{r.put?.delta ?? "—"}</td>
                      <td>{r.put?.iv ?? "—"}</td>
                      <td className={tone(r.put?.oi_change)}>{signed(r.put?.oi_change, 0)}</td>
                      <td style={{ background: `linear-gradient(to right, color-mix(in srgb, var(--pos) 22%, transparent) ${((r.put?.oi ?? 0) / maxOi) * 100}%, transparent 0)` }}>{num(r.put?.oi, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="small muted">
              IV and Greeks use Black-Scholes where the broker does not send them. Large open interest shows where option
              writers are positioned; it is not a price target.
            </p>
          </div>
        )}
      </Section>
      <Section title="Futures positioning">
        <form
          className="row"
          onSubmit={async (e) => {
            e.preventDefault();
            const result = await run(() => get<FuturesInfo>(`/intelligence/futures/${encodeURIComponent(future.trim())}`));
            if (result) setFutures(result);
          }}
        >
          <input aria-label="Futures instrument id" placeholder="e.g. NSE:NIFTY26JANFUT" value={future} onChange={(e) => setFuture(e.target.value)} />
          <button type="submit" disabled={!future.trim()}>Show</button>
        </form>
        {futures && <FuturesView view={futures} />}
        <p className="small muted">
          Price up with open interest up reads as long build-up; price down with OI up as short build-up; price up with OI
          down as short covering; price down with OI down as long unwinding.
        </p>
      </Section>
    </div>
  );
}

// ---- events and the event graph -------------------------------------------------------------------------

interface Graph {
  root: string;
  nodes: MarketEvent[];
  edges: { from: string; to: string }[];
}

export function EventsTab() {
  const [instrument, setInstrument] = useState("");
  const [category, setCategory] = useState("");
  const [severity, setSeverity] = useState("");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [live, setLive] = useState(false);
  const [streamed, setStreamed] = useState<MarketEvent[]>([]);
  const [selected, setSelected] = useState<MarketEvent | null>(null);
  const events = useData(() => get<MarketEvent[]>(`/intelligence/events?limit=300&${query}`), [query], 10000);

  const apply = (e: React.FormEvent) => {
    e.preventDefault();
    const q = new URLSearchParams();
    if (instrument.trim()) q.set("instrument_id", instrument.trim());
    if (category) q.set("category", category);
    if (severity) q.set("min_severity", severity);
    if (text.trim()) q.set("text", text.trim());
    setQuery(q.toString());
    setStreamed([]);
  };

  useEffect(() => {
    if (!live) return;
    const q = instrument.trim() ? `?instrument_id=${encodeURIComponent(instrument.trim())}` : "";
    const source = new EventSource(`/api/v1/intelligence/events/stream${q}`, { withCredentials: true });
    source.addEventListener("market", (msg) => {
      const e = JSON.parse((msg as MessageEvent).data) as MarketEvent;
      setStreamed((s) => [e, ...s].slice(0, 200));
    });
    return () => source.close();
  }, [live, instrument]);

  const seen = new Set(streamed.map((e) => e.event_id));
  const list = [...streamed, ...(events.data ?? []).filter((e) => !seen.has(e.event_id))];
  return (
    <div className="grid two">
      <Section
        title="Event store"
        actions={
          <label className="field inline small">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live
          </label>
        }
      >
        <form className="row" onSubmit={apply} style={{ marginBottom: 8 }}>
          <input aria-label="Instrument" placeholder="Instrument" value={instrument} onChange={(e) => setInstrument(e.target.value)} style={{ width: 150 }} />
          <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {["liquidity", "flow", "price", "derivatives", "news", "data"].map((c) => <option key={c}>{c}</option>)}
          </select>
          <select aria-label="Minimum severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">Any severity</option>
            {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
          </select>
          <input aria-label="Search text" placeholder="Search" value={text} onChange={(e) => setText(e.target.value)} style={{ width: 130 }} />
          <button type="submit">Search</button>
        </form>
        <div style={{ maxHeight: 640, overflowY: "auto" }}>
          <EventList events={list} onSelect={setSelected} showInstrument />
        </div>
      </Section>
      <div className="stack">
        {selected ? (
          <>
            <EventDetail key={selected.event_id} event={selected} onClose={() => setSelected(null)} />
            <EventGraph eventId={selected.event_id} onSelect={setSelected} />
          </>
        ) : (
          <Section title="Event graph">
            <p className="muted small">
              Select an event to see what led to it and what followed: for example a sweep that removed a wall, then a
              breakout and aggressive buying. Links are drawn by timing and rules; they show sequence, not proof of cause.
            </p>
          </Section>
        )}
      </div>
    </div>
  );
}

function EventGraph({ eventId, onSelect }: { eventId: string; onSelect: (e: MarketEvent) => void }) {
  const graph = useData(() => get<Graph>(`/intelligence/events/${eventId}/graph?depth=4`), [eventId]);
  const g = graph.data;
  if (!g) return null;
  const byId = new Map(g.nodes.map((n) => [n.event_id, n]));
  const nodes = [...g.nodes].sort((a, b) => a.at.localeCompare(b.at));
  return (
    <Section title="Event graph">
      {nodes.length <= 1 ? (
        <p className="muted small">No linked events.</p>
      ) : (
        <ol className="timeline">
          {nodes.map((n) => {
            const from = g.edges.filter((e) => e.to === n.event_id).map((e) => byId.get(e.from)?.kind).filter(Boolean);
            return (
              <li key={n.event_id} style={n.event_id === g.root ? { borderLeftColor: "var(--accent)" } : undefined}>
                <div className="row">
                  <span className="small muted">{new Date(n.at).toLocaleTimeString()}</span>
                  <Badge kind={n.event_id === g.root ? "ai" : undefined}>{n.kind.replaceAll("_", " ")}</Badge>
                  <button className={`link sev-${n.severity}`} onClick={() => onSelect(n)}>{n.title}</button>
                </div>
                {from.length > 0 && <div className="small muted">after {from.join(", ").replaceAll("_", " ").toLowerCase()}</div>}
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}
