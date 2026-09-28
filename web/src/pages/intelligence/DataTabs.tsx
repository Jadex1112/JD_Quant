import { useEffect, useState } from "react";
import { del, get, post, put } from "../../api";
import { useApp } from "../../app-state";
import { SignedBars } from "../../components/Bars";
import { Empty, Section, StatusBadge, useData } from "../../components/ui";
import { num, signed, time, tone } from "../../format";
import { EventList, Kpi, OrderBookLadder, SEVERITIES, px, type Book, type Flow, type MarketEvent, type Wall } from "./shared";

// ---- replay ----------------------------------------------------------------------------------------------

interface Recordings {
  settings: { enabled: boolean; acknowledged_by: string | null; retention_days: number; books_written: number; size_bytes: number };
  coverage: { instrument_id: string; first: string; last: string; books: number; ticks: number }[];
}

interface ReplayInfo {
  replay_id: string;
  instrument_id: string;
  start: string;
  end: string;
  books: number;
  sources: string[];
  marks: { at: string; kind: string; severity: string }[];
}

interface Frame {
  at: string;
  position: number;
  total: number;
  done: boolean;
  next_at: string | null;
  books: Record<string, Book>;
  walls: { active: Wall[]; history: Wall[] };
  flow: Partial<Flow>;
  events: MarketEvent[];
}

interface ReplayResult {
  net_pnl: number;
  return_pct: number;
  max_drawdown: number;
  books: number;
  trades: { opened_at: string; closed_at: string; direction: string; entry: number; exit: number; entry_reasons: string[]; exit_reason: string; net_pnl: number }[];
  stats: Record<string, number | null>;
  caveats: string[];
}

const toLocal = (iso: string) => {
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
};

export function ReplayTab() {
  const { run } = useApp();
  const recordings = useData(() => get<Recordings>("/intelligence/recordings"), []);
  const [instrument, setInstrument] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [info, setInfo] = useState<ReplayInfo | null>(null);
  const [position, setPosition] = useState(0);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [playing, setPlaying] = useState(false);
  const [strategy, setStrategy] = useState("order_flow_momentum");
  const [result, setResult] = useState<ReplayResult | null>(null);
  const coverage = recordings.data?.coverage ?? [];

  const pickCoverage = (iid: string) => {
    setInstrument(iid);
    const c = coverage.find((x) => x.instrument_id === iid);
    if (c) {
      setStart(toLocal(c.first));
      setEnd(toLocal(c.last));
    }
  };

  const span = info ? new Date(info.end).getTime() - new Date(info.start).getTime() : 0;
  const at = info ? new Date(new Date(info.start).getTime() + (span * position) / 1000).toISOString() : "";

  useEffect(() => {
    if (!info) return;
    let cancelled = false;
    get<Frame>(`/intelligence/replays/${info.replay_id}/frame?at=${encodeURIComponent(at)}&levels=10`)
      .then((f) => !cancelled && setFrame(f))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [info, at]);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setPosition((p) => (p >= 1000 ? (setPlaying(false), p) : p + 2)), 250);
    return () => clearInterval(timer);
  }, [playing]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const created = await run(() =>
      post<ReplayInfo>("/intelligence/replays", {
        instrument_id: instrument,
        start: new Date(start).toISOString(),
        end: new Date(end).toISOString(),
      }),
    );
    if (created) {
      setInfo(created);
      setPosition(0);
      setResult(null);
    }
  };

  const backtest = async () => {
    if (!info) return;
    const r = await run(() => post<ReplayResult>("/automation/replay-backtest", { replay_id: info.replay_id, strategy }));
    if (r) setResult(r);
  };

  const book = frame ? Object.values(frame.books)[0] : undefined;
  return (
    <div className="stack">
      <Section title="Recorded sessions">
        {!recordings.data?.settings.enabled && (
          <div className="alert small" style={{ marginBottom: 8 }}>
            Recording is off. Turn it on under Settings (after acknowledging the data-use notice) to replay order books later.
          </div>
        )}
        {coverage.length === 0 ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <form className="row" onSubmit={create}>
            <select aria-label="Recorded instrument" value={instrument} onChange={(e) => pickCoverage(e.target.value)} required>
              <option value="" disabled>Instrument…</option>
              {coverage.map((c) => (
                <option key={c.instrument_id} value={c.instrument_id}>
                  {c.instrument_id} ({num(c.books)} books)
                </option>
              ))}
            </select>
            <label className="field inline small">From <input type="datetime-local" step="1" value={start} onChange={(e) => setStart(e.target.value)} required /></label>
            <label className="field inline small">To <input type="datetime-local" step="1" value={end} onChange={(e) => setEnd(e.target.value)} required /></label>
            <button className="primary" type="submit">Load replay</button>
          </form>
        )}
      </Section>
      {info && (
        <Section title={`Replay ${info.instrument_id}`}>
          <div className="row">
            <button onClick={() => setPlaying((p) => !p)}>{playing ? "❚❚ Pause" : "▶ Play"}</button>
            <input
              type="range"
              min={0}
              max={1000}
              value={position}
              onChange={(e) => setPosition(Number(e.target.value))}
              aria-label="Replay position"
              style={{ flex: 1, minWidth: 200 }}
            />
            <span className="small">{time(at)}</span>
            <span className="small muted">{frame ? `${frame.position}/${frame.total} books` : ""}</span>
          </div>
          {frame && (
            <div className="grid two" style={{ marginTop: 12 }}>
              <div>{book ? <OrderBookLadder book={book} walls={frame.walls.active} levels={10} /> : <Empty>No book yet at this time.</Empty>}</div>
              <div className="stack">
                <div className="metric-grid">
                  <Kpi label="CVD" value={signed(frame.flow.cvd, 0)} tone={tone(frame.flow.cvd)} />
                  <Kpi label="Delta 1 min" value={signed(frame.flow.delta_1m, 0)} tone={tone(frame.flow.delta_1m)} />
                  <Kpi label="Active walls" value={frame.walls.active.length} />
                </div>
                <SignedBars label="Delta per minute" values={(frame.flow.bars ?? []).map((b) => ({ key: b.time, value: b.delta }))} />
                <div style={{ maxHeight: 260, overflowY: "auto" }}>
                  <EventList events={frame.events.slice(0, 40)} />
                </div>
              </div>
            </div>
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <strong>Test a strategy on this session:</strong>
            <select aria-label="Order-book strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="order_flow_momentum">Order-flow momentum</option>
              <option value="liquidity_wall_bounce">Liquidity wall bounce</option>
              <option value="vwap_reclaim">VWAP reclaim</option>
            </select>
            <button onClick={backtest}>Run</button>
          </div>
          {result && (
            <div className="stack" style={{ marginTop: 12 }}>
              <div className="metric-grid">
                <Kpi label="Net P&L" value={signed(result.net_pnl)} tone={tone(result.net_pnl)} />
                <Kpi label="Return" value={`${signed(result.return_pct)}%`} tone={tone(result.return_pct)} />
                <Kpi label="Max drawdown" value={num(result.max_drawdown, 2)} />
                <Kpi label="Trades" value={result.trades.length} />
              </div>
              {result.trades.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Opened</th><th>Direction</th><th>Entry</th><th>Exit</th><th>Why in</th><th>Why out</th><th>Net</th></tr></thead>
                    <tbody>
                      {result.trades.map((t) => (
                        <tr key={t.opened_at}>
                          <td>{new Date(t.opened_at).toLocaleTimeString()}</td>
                          <td>{t.direction}</td>
                          <td>{px(t.entry)}</td>
                          <td>{px(t.exit)}</td>
                          <td className="small">{t.entry_reasons.join(", ")}</td>
                          <td className="small">{t.exit_reason}</td>
                          <td className={tone(t.net_pnl)}>{signed(t.net_pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <ul className="small muted">{result.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          )}
          <p className="small muted" style={{ marginTop: 8 }}>
            The replay runs the recorded books through fresh copies of the engines, so walls, flow and events appear as they
            did live (sources: {info.sources.join(", ")}).
          </p>
        </Section>
      )}
    </div>
  );
}

// ---- news and corporate events ---------------------------------------------------------------------------

interface NewsItem {
  news_id: string;
  at: string;
  headline: string;
  source: string;
  url: string;
  summary: string;
  instruments: string[];
}

interface Reaction {
  news: NewsItem;
  reactions: {
    instrument_id: string;
    prices: Record<string, { price: number | null; change_pct: number | null }>;
    volume_ratio: number | null;
    timeline: MarketEvent[];
  }[];
}

interface Corporate {
  event_id: string;
  instrument_id: string;
  kind: string;
  day: string;
  details: string;
  source: string;
}

const CORPORATE_KINDS = ["RESULTS", "DIVIDEND", "SPLIT", "BONUS", "RIGHTS", "BOARD_MEETING", "AGM", "BUYBACK", "OTHER"];

export function NewsTab() {
  const { run, can } = useApp();
  const news = useData(() => get<{ settings: { feeds: string[]; block_mode: string; feed_errors: Record<string, string> }; items: NewsItem[] }>("/intelligence/news?limit=100"), [], 30000);
  const corporate = useData(() => get<Corporate[]>("/intelligence/corporate-events?days=60"), []);
  const [reaction, setReaction] = useState<Reaction | null>(null);
  const [headline, setHeadline] = useState("");
  const [feeds, setFeeds] = useState<string | null>(null);
  const [corp, setCorp] = useState({ instrument_id: "", kind: "RESULTS", day: "", details: "" });
  const [csv, setCsv] = useState("");
  const settings = news.data?.settings;

  return (
    <div className="grid two">
      <div className="stack">
        <Section
          title="News"
          actions={can("marketdata:subscribe") && <button onClick={async () => { await run(() => post("/intelligence/news/poll"), "Feeds checked"); news.reload(); }}>Check feeds</button>}
        >
          {can("marketdata:import") && (
            <form
              className="row"
              style={{ marginBottom: 8 }}
              onSubmit={async (e) => {
                e.preventDefault();
                if (await run(() => post("/intelligence/news", { headline }), "Headline added")) {
                  setHeadline("");
                  news.reload();
                }
              }}
            >
              <input aria-label="Headline" placeholder="Add a headline (instruments are matched by symbol)" value={headline} onChange={(e) => setHeadline(e.target.value)} style={{ flex: 1 }} />
              <button type="submit" disabled={headline.trim().length < 3}>Add</button>
            </form>
          )}
          {(news.data?.items ?? []).length === 0 ? (
            <Empty>No news yet. Add RSS or Atom feeds (exchange announcements, company press pages) below.</Empty>
          ) : (
            <ul className="event-list">
              {news.data!.items.map((n) => (
                <li key={n.news_id}>
                  <span className="small muted">{time(n.at)}</span>
                  {n.url ? <a href={n.url} target="_blank" rel="noreferrer noopener">{n.headline}</a> : <span>{n.headline}</span>}
                  <span className="small muted">{n.source}</span>
                  {n.instruments.length > 0 && (
                    <button className="small" onClick={async () => setReaction((await run(() => get<Reaction>(`/intelligence/news/${n.news_id}/reaction`))) ?? null)}>
                      Market reaction ({n.instruments.join(", ")})
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>
        {reaction && (
          <Section title="Market reaction" actions={<button className="small" onClick={() => setReaction(null)}>Close</button>}>
            <p><strong>{reaction.news.headline}</strong></p>
            {reaction.reactions.map((r) => (
              <div key={r.instrument_id} className="stack" style={{ marginBottom: 12 }}>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>{r.instrument_id}</th>{Object.keys(r.prices).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                    <tbody>
                      <tr><td>Price</td>{Object.values(r.prices).map((p, i) => <td key={i}>{px(p.price)}</td>)}</tr>
                      <tr><td>Change</td>{Object.values(r.prices).map((p, i) => <td key={i} className={tone(p.change_pct)}>{p.change_pct == null ? "—" : `${signed(p.change_pct)}%`}</td>)}</tr>
                    </tbody>
                  </table>
                </div>
                <span className="small">Volume 15 min after vs before: {r.volume_ratio == null ? "—" : `${r.volume_ratio.toFixed(1)}×`}</span>
                <EventList events={r.timeline} />
              </div>
            ))}
            <p className="small muted">Moves around news show timing, not cause.</p>
          </Section>
        )}
        {settings && can("marketdata:subscribe") && (
          <Section title="News settings">
            <label className="field">
              RSS / Atom feeds (one per line)
              <textarea rows={3} value={feeds ?? settings.feeds.join("\n")} onChange={(e) => setFeeds(e.target.value)} />
            </label>
            {Object.entries(settings.feed_errors).map(([url, err]) => <div key={url} className="alert warn small">{url}: {err}</div>)}
            <div className="row" style={{ marginTop: 8 }}>
              <label className="field inline small">
                Corporate events near a trade:
                <select
                  value={settings.block_mode}
                  onChange={async (e) => {
                    await run(() => put("/intelligence/news/settings", { block_mode: e.target.value }), "Saved");
                    news.reload();
                  }}
                >
                  <option value="off">ignore</option>
                  <option value="warn">warn</option>
                  <option value="block">block automated entries</option>
                </select>
              </label>
              <span className="spacer" style={{ flex: 1 }} />
              <button
                onClick={async () => {
                  const list = (feeds ?? "").split("\n").map((f) => f.trim()).filter(Boolean);
                  if (await run(() => put("/intelligence/news/settings", { feeds: list }), "Feeds saved")) news.reload();
                }}
                disabled={feeds === null}
              >
                Save feeds
              </button>
            </div>
          </Section>
        )}
      </div>
      <Section title="Corporate events (next 60 days)">
        {(corporate.data ?? []).length === 0 ? (
          <Empty>None recorded. Results, dividends, splits and board meetings can move prices sharply.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Day</th><th>Instrument</th><th>Event</th><th>Details</th><th /></tr></thead>
              <tbody>
                {corporate.data!.map((c) => (
                  <tr key={c.event_id}>
                    <td>{c.day}</td>
                    <td>{c.instrument_id}</td>
                    <td>{c.kind.replaceAll("_", " ").toLowerCase()}</td>
                    <td className="small">{c.details}</td>
                    <td>
                      {can("marketdata:import") && (
                        <button className="small" onClick={async () => { await run(() => del(`/intelligence/corporate-events/${c.event_id}`)); corporate.reload(); }}>
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {can("marketdata:import") && (
          <div className="stack" style={{ marginTop: 12 }}>
            <form
              className="form-grid"
              onSubmit={async (e) => {
                e.preventDefault();
                if (await run(() => post("/intelligence/corporate-events", corp), "Added")) corporate.reload();
              }}
            >
              <label className="field">Instrument<input required value={corp.instrument_id} onChange={(e) => setCorp({ ...corp, instrument_id: e.target.value })} placeholder="NSE:RELIANCE" /></label>
              <label className="field">Event<select value={corp.kind} onChange={(e) => setCorp({ ...corp, kind: e.target.value })}>{CORPORATE_KINDS.map((k) => <option key={k}>{k}</option>)}</select></label>
              <label className="field">Day<input required type="date" value={corp.day} onChange={(e) => setCorp({ ...corp, day: e.target.value })} /></label>
              <label className="field">Details<input value={corp.details} onChange={(e) => setCorp({ ...corp, details: e.target.value })} /></label>
              <button type="submit" style={{ alignSelf: "end" }}>Add</button>
            </form>
            <label className="field">
              Import CSV: symbol, date (YYYY-MM-DD), kind, details
              <textarea rows={3} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder="RELIANCE,2026-01-16,RESULTS,Q3 results" />
            </label>
            <div className="row end">
              <button
                disabled={!csv.trim()}
                onClick={async () => {
                  const r = await run(() => post<{ added: number; errors: string[] }>("/intelligence/corporate-events/import", { csv }));
                  if (r) {
                    setCsv(r.errors.join("\n"));
                    corporate.reload();
                  }
                }}
              >
                Import
              </button>
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}

// ---- data health -----------------------------------------------------------------------------------------

interface Health {
  connections: { connection_id: string; name: string; venue: string; status: string; ready: boolean; last_error: string | null; rest_latency_ms: number | null; rejections_last_hour: number; circuit_open: boolean }[];
  feeds: { source: string; kind: string; connected: boolean; messages: number; books: number; ticks: number; rejected: number; reconnects: number; auth_failures: number; seconds_since_message: number | null; lag_ms: number | null; last_error: string | null; instruments: string[] }[];
  streams: { connection_id: string; source: string; feed: string; running: boolean; connected: boolean; instruments: string[]; reconnects: number; last_error: string | null }[];
  stale_feeds: string[];
}

interface Quality {
  instrument_id: string;
  state: string;
  confirmed_by: string[];
  sources: Record<string, { price: number | null; bid: number | null; ask: number | null; age_seconds: number | null }>;
}

export function HealthTab() {
  const health = useData(() => get<Health>("/intelligence/health"), [], 5000);
  const quality = useData(() => get<Quality[]>("/intelligence/quality"), [], 5000);
  const recordings = useData(() => get<Recordings>("/intelligence/recordings"), [], 30000);
  const h = health.data;
  return (
    <div className="stack">
      {h?.stale_feeds.length ? <div className="alert warn">Stale feeds: {h.stale_feeds.join(", ")}. Automated entries on them are held back.</div> : null}
      <Section title="Brokers">
        {!h?.connections.length ? (
          <Empty>No broker connections.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Connection</th><th>Status</th><th>Ready</th><th>REST latency</th><th>Rejections (1h)</th><th>Circuit</th><th>Last error</th></tr></thead>
              <tbody>
                {h.connections.map((c) => (
                  <tr key={c.connection_id}>
                    <td>{c.name} <span className="small muted">{c.venue}</span></td>
                    <td><StatusBadge status={c.status} /></td>
                    <td>{c.ready ? "yes" : "no"}</td>
                    <td>{c.rest_latency_ms == null ? "—" : `${c.rest_latency_ms} ms`}</td>
                    <td>{c.rejections_last_hour}</td>
                    <td>{c.circuit_open ? <StatusBadge status="OPEN" /> : "closed"}</td>
                    <td className="small" style={{ whiteSpace: "normal" }}>{c.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Market data feeds">
        {!h?.feeds.length ? (
          <Empty>No feed has delivered data yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source</th><th>Kind</th><th>Last message</th><th>Lag</th><th>Books</th><th>Ticks</th><th>Rejected</th><th>Reconnects</th><th>Auth failures</th><th>Instruments</th><th>Last error</th></tr></thead>
              <tbody>
                {h.feeds.map((f) => (
                  <tr key={f.source}>
                    <td>{f.source}</td>
                    <td>{f.kind}</td>
                    <td className={f.seconds_since_message != null && f.seconds_since_message > 30 ? "neg" : ""}>{f.seconds_since_message == null ? "—" : `${f.seconds_since_message.toFixed(1)}s ago`}</td>
                    <td>{f.lag_ms == null ? "—" : `${f.lag_ms.toFixed(0)} ms`}</td>
                    <td>{num(f.books)}</td>
                    <td>{num(f.ticks)}</td>
                    <td>{f.rejected}</td>
                    <td>{f.reconnects}</td>
                    <td>{f.auth_failures}</td>
                    <td className="small">{f.instruments.length}</td>
                    <td className="small" style={{ whiteSpace: "normal" }}>{f.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {h && h.streams.length > 0 && (
          <>
            <h3 style={{ marginTop: 16 }}>WebSocket streams</h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Source</th><th>Feed</th><th>State</th><th>Instruments</th><th>Reconnects</th><th>Last error</th></tr></thead>
                <tbody>
                  {h.streams.map((f) => (
                    <tr key={f.connection_id + f.feed}>
                      <td>{f.source}</td>
                      <td>{f.feed}</td>
                      <td><StatusBadge status={f.connected ? "CONNECTED" : f.running ? "RECOVERING" : "STOPPED"} /></td>
                      <td className="small">{f.instruments.length}</td>
                      <td>{f.reconnects}</td>
                      <td className="small" style={{ whiteSpace: "normal" }}>{f.last_error ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>
      <Section title="Cross-broker data quality">
        <p className="small muted">
          Each broker's prices are compared, never merged. TIMING means one feed is behind the other; DISCREPANCY means they
          disagree for longer than timing explains.
        </p>
        {!quality.data?.length ? (
          <Empty>No instruments with data.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Instrument</th><th>State</th><th>Sources (price · age)</th></tr></thead>
              <tbody>
                {quality.data.map((q) => (
                  <tr key={q.instrument_id}>
                    <td>{q.instrument_id}</td>
                    <td><StatusBadge status={q.state} /></td>
                    <td className="small">
                      {Object.entries(q.sources).map(([s, v]) => `${s} ${px(v.price)} · ${v.age_seconds?.toFixed(1) ?? "—"}s`).join("   |   ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      {recordings.data && (
        <Section title="Recording">
          <p className="small">
            {recordings.data.settings.enabled ? "On" : "Off"} · {num(recordings.data.settings.books_written)} books written this run ·
            database {num(recordings.data.settings.size_bytes / 1e6, 1)} MB · kept {recordings.data.settings.retention_days} days
          </p>
        </Section>
      )}
    </div>
  );
}

// ---- settings --------------------------------------------------------------------------------------------

interface IntelSettings {
  watch: string[];
  scanner_universe: string[];
  scanner_enabled: boolean;
  option_underlyings: string[];
  explain_events: boolean;
  explain_min_severity: string;
  streaming_enabled: boolean;
  deep_depth: string[];
  recording: { enabled: boolean; acknowledged_by: string | null; retention_days: number };
  data_use_notice: string;
  max_watch: number;
  simulated_depth: boolean;
}

const lines = (text: string) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

export function SettingsTab() {
  const { run, can } = useApp();
  const settings = useData(() => get<IntelSettings>("/intelligence/settings"), []);
  const [form, setForm] = useState<IntelSettings | null>(null);
  const [watch, setWatch] = useState("");
  const [universe, setUniverse] = useState("");
  const [underlyings, setUnderlyings] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);

  useEffect(() => {
    if (settings.data) {
      setForm(settings.data);
      setWatch(settings.data.watch.join("\n"));
      setUniverse(settings.data.scanner_universe.join("\n"));
      setUnderlyings(settings.data.option_underlyings.join(", "));
    }
  }, [settings.data]);

  if (!form) return <Empty>Loading…</Empty>;
  const editable = can("marketdata:subscribe");
  const watchList = lines(watch);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const saved = await run(
      () =>
        put<IntelSettings>("/intelligence/settings", {
          watch: watchList,
          scanner_universe: lines(universe),
          scanner_enabled: form.scanner_enabled,
          option_underlyings: lines(underlyings),
          explain_events: form.explain_events,
          explain_min_severity: form.explain_min_severity,
          streaming_enabled: form.streaming_enabled,
          deep_depth: form.deep_depth.filter((d) => watchList.includes(d)),
          recording: { enabled: form.recording.enabled, acknowledge, retention_days: form.recording.retention_days },
        }),
      "Settings saved",
    );
    if (saved) settings.setData({ ...settings.data!, ...saved });
  };
  return (
    <form className="stack" onSubmit={save}>
      <Section title="Watchlist">
        <label className="field">
          Instruments whose order books, flow and structure are followed (one per line, up to {form.max_watch})
          <textarea rows={5} value={watch} onChange={(e) => setWatch(e.target.value)} disabled={!editable} placeholder={"NSE:RELIANCE\nNSE:CUPID\nOANDA:XAU_USD"} />
        </label>
        <fieldset style={{ border: "none", padding: 0, margin: "8px 0 0" }}>
          <legend className="small muted">Deep depth (Dhan 200-level, up to 5 instruments)</legend>
          <div className="chips">
            {watchList.map((iid) => (
              <label key={iid} className="field inline small">
                <input
                  type="checkbox"
                  disabled={!editable}
                  checked={form.deep_depth.includes(iid)}
                  onChange={(e) =>
                    setForm({ ...form, deep_depth: e.target.checked ? [...form.deep_depth, iid].slice(0, 5) : form.deep_depth.filter((d) => d !== iid) })
                  }
                />
                {iid}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field inline" style={{ marginTop: 8 }}>
          <input type="checkbox" disabled={!editable} checked={form.streaming_enabled} onChange={(e) => setForm({ ...form, streaming_enabled: e.target.checked })} />
          Stream over broker WebSockets where available (Dhan, Fyers, Kotak Neo); otherwise poll
        </label>
        {form.simulated_depth && <p className="small muted">Demo mode: instruments without a broker get a simulated order book, clearly marked SIMULATED.</p>}
      </Section>
      <div className="grid two">
        <Section title="Scanner and options">
          <label className="field">
            Scanner universe (one per line; empty scans the watchlist)
            <textarea rows={4} value={universe} onChange={(e) => setUniverse(e.target.value)} disabled={!editable} />
          </label>
          <label className="field inline" style={{ marginTop: 8 }}>
            <input type="checkbox" disabled={!editable} checked={form.scanner_enabled} onChange={(e) => setForm({ ...form, scanner_enabled: e.target.checked })} />
            Run the scanner automatically
          </label>
          <label className="field" style={{ marginTop: 8 }}>
            Option chains to follow (e.g. NIFTY, BANKNIFTY)
            <input value={underlyings} onChange={(e) => setUnderlyings(e.target.value)} disabled={!editable} />
          </label>
        </Section>
        <Section title="AI explanations">
          <label className="field inline">
            <input type="checkbox" disabled={!editable} checked={form.explain_events} onChange={(e) => setForm({ ...form, explain_events: e.target.checked })} />
            Explain important events automatically (uses the configured AI model; otherwise rule-based text)
          </label>
          <label className="field" style={{ marginTop: 8 }}>
            From severity
            <select value={form.explain_min_severity} disabled={!editable} onChange={(e) => setForm({ ...form, explain_min_severity: e.target.value })}>
              {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
        </Section>
      </div>
      <Section title="Recording and replay">
        <div className="alert small">{form.data_use_notice}</div>
        {!form.recording.acknowledged_by && (
          <label className="field inline" style={{ marginTop: 8 }}>
            <input type="checkbox" disabled={!editable} checked={acknowledge} onChange={(e) => setAcknowledge(e.target.checked)} />
            I have read this and will keep recordings for my own use
          </label>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <label className="field inline">
            <input
              type="checkbox"
              disabled={!editable || (!form.recording.acknowledged_by && !acknowledge)}
              checked={form.recording.enabled}
              onChange={(e) => setForm({ ...form, recording: { ...form.recording, enabled: e.target.checked } })}
            />
            Record order books and trades of watched instruments
          </label>
          <label className="field inline small">
            Keep for
            <input type="number" min={1} max={3650} style={{ width: 80 }} disabled={!editable} value={form.recording.retention_days} onChange={(e) => setForm({ ...form, recording: { ...form.recording, retention_days: Number(e.target.value) } })} />
            days
          </label>
        </div>
        {form.recording.acknowledged_by && <p className="small muted">Acknowledged by {form.recording.acknowledged_by}.</p>}
      </Section>
      {editable && (
        <div className="row end">
          <button className="primary" type="submit">Save settings</button>
        </div>
      )}
    </form>
  );
}
