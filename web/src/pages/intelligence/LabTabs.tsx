import { useState } from "react";
import { get, post, put } from "../../api";
import { useApp } from "../../app-state";
import { Lines } from "../../components/Bars";
import { Badge, Empty, Section, StatusBadge, useData } from "../../components/ui";
import { pct, signed, time, tone } from "../../format";
import { Kpi, px } from "./shared";

// ---- Kronos forecasts ------------------------------------------------------------------------------------

interface ForecastStatus {
  available: boolean;
  problem: string | null;
  model: string;
  models: string[];
  paths: number;
  caveat: string;
}

interface Forecast {
  forecast_id: string;
  instrument_id: string;
  model: string;
  made_at: string;
  history_source: string;
  last_close: number;
  target_time: string;
  horizon: number;
  paths_count: number;
  prob_up: number;
  median_return_pct: number;
  p10: number;
  p50: number;
  p90: number;
  band: { step: number; time: string; p10: number; p50: number; p90: number }[];
  history?: { time: string; close: number }[];
  status: string;
  outcome: { actual_close: number; return_pct: number; direction_hit: boolean | null; inside_band: boolean } | null;
}

interface Scorecard {
  scored: number;
  pending: number;
  hit_rate?: number | null;
  brier?: number;
  band_coverage?: number;
  verdict: string;
}

const INTERVALS: [number, string][] = [
  [300, "5 minutes"],
  [900, "15 minutes"],
  [3600, "1 hour"],
  [14400, "4 hours"],
  [86400, "1 day"],
];

export function ForecastTab() {
  const { run, can } = useApp();
  const status = useData(() => get<ForecastStatus>("/forecasts/status"), []);
  const settings = useData(() => get<{ watch: string[] }>("/intelligence/settings"), []);
  const [instrument, setInstrument] = useState("");
  const [interval, setIntervalSeconds] = useState(3600);
  const [horizon, setHorizon] = useState(12);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Forecast | null>(null);
  const history = useData(() => get<{ forecasts: Forecast[]; scorecard: Scorecard }>("/forecasts?limit=50"), [], 30000);
  const s = status.data;
  const current = instrument || settings.data?.watch[0] || "OANDA:XAU_USD";

  const go = async () => {
    setBusy(true);
    const out = await run(() => post<Forecast>("/forecasts", { instrument_id: current, interval_seconds: interval, horizon }));
    setBusy(false);
    if (out) {
      setResult(out);
      history.reload();
    }
  };

  const toX = (iso: string) => new Date(iso).getTime() / 3.6e6;
  return (
    <div className="stack">
      <Section
        title="Kronos price forecast"
        actions={s && <Badge kind={s.available ? "good" : "warn"}>{s.available ? `${s.model} · ${s.paths} paths` : "not installed"}</Badge>}
      >
        {s && !s.available && (
          <div className="alert warn small" style={{ marginBottom: 8 }}>
            Kronos needs PyTorch on the server: <code>pip install "jdquant[forecast]"</code>, then restart. The model
            weights download from Hugging Face on first use. ({s.problem})
          </div>
        )}
        <p className="small muted">
          Kronos (a foundation model trained on candlesticks from 45 exchanges) samples several possible futures. It
          gives the chance price is higher after the horizon and a 10–90% range, not a single guess. {s?.caveat}
        </p>
        <div className="row">
          <input aria-label="Instrument" list="forecast-watch" value={current} onChange={(e) => setInstrument(e.target.value)} style={{ minWidth: 200 }} />
          <datalist id="forecast-watch">{(settings.data?.watch ?? []).map((w) => <option key={w} value={w} />)}</datalist>
          <select aria-label="Bar size" value={interval} onChange={(e) => setIntervalSeconds(Number(e.target.value))}>
            {INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <label className="field inline small">
            bars ahead
            <input type="number" min={1} max={120} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} style={{ width: 70 }} />
          </label>
          <button className="primary" onClick={go} disabled={busy || !s?.available}>{busy ? "Forecasting…" : "Forecast"}</button>
          {s && can("marketdata:subscribe") && (
            <>
              <select
                aria-label="Kronos model"
                value={s.model}
                onChange={async (e) => {
                  if (await run(() => put("/forecasts/settings", { model: e.target.value }), "Model changed")) status.reload();
                }}
              >
                {s.models.map((m) => <option key={m}>{m}</option>)}
              </select>
            </>
          )}
        </div>
        {result && (
          <div className="stack" style={{ marginTop: 12 }}>
            {result.history_source.startsWith("synthetic") && (
              <div className="alert warn small">Synthetic demo history: this forecast shows how the page works and says nothing about a market. It is not scored.</div>
            )}
            <div className="metric-grid">
              <Kpi label="Chance higher" value={pct(result.prob_up, 0)} tone={result.prob_up > 0.5 ? "pos" : result.prob_up < 0.5 ? "neg" : ""} />
              <Kpi label="Median move" value={`${signed(result.median_return_pct)}%`} tone={tone(result.median_return_pct)} />
              <Kpi label="10–90% range" value={`${px(result.p10)} – ${px(result.p90)}`} />
              <Kpi label="Last close" value={px(result.last_close)} />
              <Kpi label="Target time" value={new Date(result.target_time).toLocaleString()} />
            </div>
            <Lines
              label={`Kronos forecast for ${result.instrument_id}`}
              series={[
                { name: "history", kind: "muted", points: (result.history ?? []).map((h) => [toX(h.time), h.close]) },
                { name: "median path", kind: "main", points: [[toX(result.band[0].time) - interval / 3600, result.last_close], ...result.band.map((b) => [toX(b.time), b.p50] as [number, number])] },
                { name: "90th percentile", kind: "band", points: result.band.map((b) => [toX(b.time), b.p90]) },
                { name: "10th percentile", kind: "band", points: result.band.map((b) => [toX(b.time), b.p10]) },
              ]}
            />
            <div className="legend"><span className="muted">history</span><span className="main">median of {result.paths_count} paths</span><span className="band">10th and 90th percentile</span></div>
          </div>
        )}
      </Section>
      <Section title="Forward scorecard">
        {history.data && (
          <>
            <p><strong>{history.data.scorecard.verdict}</strong></p>
            {history.data.scorecard.scored > 0 && (
              <div className="metric-grid">
                <Kpi label="Scored" value={history.data.scorecard.scored} />
                <Kpi label="Direction right" value={pct(history.data.scorecard.hit_rate ?? null, 0)} title="Share of forecasts whose more likely direction came true (50% is a coin flip)" />
                <Kpi label="Brier score" value={history.data.scorecard.brier?.toFixed(3) ?? "—"} title="0 is perfect; 0.25 is what always saying 50% scores" />
                <Kpi label="Inside the 10–90% band" value={pct(history.data.scorecard.band_coverage ?? null, 0)} title="About 80% if the band is honest" />
              </div>
            )}
            {history.data.forecasts.length === 0 ? (
              <Empty>No forecasts on real market data yet.</Empty>
            ) : (
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table>
                  <thead><tr><th>Made</th><th>Instrument</th><th>Horizon</th><th>Chance higher</th><th>Median move</th><th>Status</th><th>Actual move</th><th>Right?</th></tr></thead>
                  <tbody>
                    {history.data.forecasts.map((f) => (
                      <tr key={f.forecast_id}>
                        <td className="small">{time(f.made_at)}</td>
                        <td className="small">{f.instrument_id}</td>
                        <td>{f.horizon}</td>
                        <td>{pct(f.prob_up, 0)}</td>
                        <td className={tone(f.median_return_pct)}>{signed(f.median_return_pct)}%</td>
                        <td><StatusBadge status={f.status} /></td>
                        <td className={tone(f.outcome?.return_pct)}>{f.outcome ? `${signed(f.outcome.return_pct)}%` : "—"}</td>
                        <td>{f.outcome?.direction_hit == null ? "—" : f.outcome.direction_hit ? "yes" : "no"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
        <p className="small muted" style={{ marginTop: 8 }}>
          The <strong>kronos_forecast</strong> strategy (Strategies → New deployment) trades these forecasts on a paper
          account. It cannot be backtested, because the model may have seen past periods in training.
        </p>
      </Section>
    </div>
  );
}

// ---- options payoff lab ----------------------------------------------------------------------------------

interface Leg {
  kind: string;
  side: string;
  strike: number;
  price: number;
  quantity: number;
  iv: number;
  days: number;
}

interface Payoff {
  spot: number;
  days_to_expiry: number;
  net_premium: number;
  breakevens: number[];
  max_profit: number | null;
  max_loss: number | null;
  max_profit_in_range: number;
  max_loss_in_range: number;
  greeks: { delta: number; gamma: number; theta: number; vega: number };
  probability_of_profit: number | null;
  curve: { price: number; expiry: number; today: number }[];
  scenario_moves_pct: number[];
  scenarios: { days_passed: number; iv_change_pts: number; pnl: number[] }[];
  notes: string[];
}

const PRESETS: [string, string][] = [
  ["long_straddle", "Long straddle"],
  ["short_strangle", "Short strangle"],
  ["bull_call_spread", "Bull call spread"],
  ["bear_put_spread", "Bear put spread"],
  ["iron_condor", "Iron condor"],
  ["covered_call", "Covered call"],
];

export function PayoffTab() {
  const { run } = useApp();
  const [spot, setSpot] = useState(24000);
  const [step, setStep] = useState(50);
  const [lots, setLots] = useState(75);
  const [days, setDays] = useState(7);
  const [iv, setIv] = useState(14);
  const [legs, setLegs] = useState<Leg[]>([]);
  const [result, setResult] = useState<Payoff | null>(null);

  const loadPreset = async (name: string) => {
    const out = await run(() => post<{ legs: Leg[] }>("/intelligence/payoff/preset", { name, spot, step, lots, days, iv }));
    if (out) {
      setLegs(out.legs);
      setResult(null);
    }
  };
  const analyze = async () => {
    const out = await run(() => post<Payoff>("/intelligence/payoff", { legs, spot }));
    if (out) setResult(out);
  };
  const setLeg = (i: number, patch: Partial<Leg>) => setLegs(legs.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const num = (v: number | null | undefined, unlimited = "unlimited") => (v == null ? unlimited : signed(v, 0));

  return (
    <div className="stack">
      <Section title="Options payoff lab">
        <p className="small muted">
          Build a position leg by leg (or start from a preset priced at the volatility you give) and see what it makes or
          loses at expiry and today, its breakevens, Greeks and scenarios.
        </p>
        <div className="form-grid">
          <label className="field">Spot<input type="number" value={spot} onChange={(e) => setSpot(Number(e.target.value))} /></label>
          <label className="field">Strike step<input type="number" value={step} onChange={(e) => setStep(Number(e.target.value))} /></label>
          <label className="field">Quantity per leg (units)<input type="number" value={lots} onChange={(e) => setLots(Number(e.target.value))} /></label>
          <label className="field">Days to expiry<input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
          <label className="field">Implied volatility %<input type="number" value={iv} onChange={(e) => setIv(Number(e.target.value))} /></label>
        </div>
        <div className="chips" style={{ margin: "8px 0" }}>
          {PRESETS.map(([k, label]) => <button key={k} className="small" onClick={() => loadPreset(k)}>{label}</button>)}
        </div>
        {legs.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Side</th><th>Kind</th><th>Strike</th><th>Entry price</th><th>Quantity</th><th>IV %</th><th>Days</th><th /></tr></thead>
              <tbody>
                {legs.map((l, i) => (
                  <tr key={i}>
                    <td>
                      <select aria-label={`Leg ${i + 1} side`} value={l.side} onChange={(e) => setLeg(i, { side: e.target.value })}>
                        <option>BUY</option><option>SELL</option>
                      </select>
                    </td>
                    <td>
                      <select aria-label={`Leg ${i + 1} kind`} value={l.kind} onChange={(e) => setLeg(i, { kind: e.target.value })}>
                        <option value="CE">Call</option><option value="PE">Put</option><option value="FUT">Future</option><option value="SPOT">Underlying</option>
                      </select>
                    </td>
                    {(["strike", "price", "quantity", "iv", "days"] as const).map((k) => (
                      <td key={k}>
                        <input aria-label={`Leg ${i + 1} ${k}`} type="number" step="any" style={{ width: 90 }} value={l[k]} onChange={(e) => setLeg(i, { [k]: Number(e.target.value) })} />
                      </td>
                    ))}
                    <td><button className="small" aria-label={`Remove leg ${i + 1}`} onClick={() => setLegs(legs.filter((_, j) => j !== i))}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button
            className="small"
            onClick={() => setLegs([...legs, { kind: "CE", side: "BUY", strike: Math.round(spot / step) * step, price: 0, quantity: lots, iv, days }])}
          >
            + Add leg
          </button>
          <button className="primary" onClick={analyze} disabled={!legs.length}>Analyse</button>
        </div>
      </Section>
      {result && (
        <Section title="Payoff">
          <div className="metric-grid">
            <Kpi label={result.net_premium >= 0 ? "Credit received" : "Debit paid"} value={signed(Math.abs(result.net_premium), 0)} />
            <Kpi label="Max profit" value={num(result.max_profit)} tone="pos" />
            <Kpi label="Max loss" value={num(result.max_loss)} tone="neg" />
            <Kpi label="Breakevens" value={result.breakevens.map((b) => px(b)).join(" · ") || "none"} />
            <Kpi label="Chance of profit" value={pct(result.probability_of_profit, 0)} title="Lognormal at the legs' implied volatility" />
            <Kpi label="Delta / Gamma" value={`${result.greeks.delta.toFixed(1)} / ${result.greeks.gamma.toFixed(3)}`} />
            <Kpi label="Theta per day" value={signed(result.greeks.theta, 0)} tone={tone(result.greeks.theta)} />
            <Kpi label="Vega per vol point" value={signed(result.greeks.vega, 0)} />
          </div>
          <Lines
            label="Profit and loss by underlying price"
            zero
            marks={[{ x: result.spot, name: "spot" }]}
            series={[
              { name: "at expiry", kind: "main", points: result.curve.map((c) => [c.price, c.expiry]) },
              { name: "today", kind: "alt", points: result.curve.map((c) => [c.price, c.today]) },
            ]}
          />
          <div className="legend"><span className="main">at expiry</span><span className="alt">today</span></div>
          <h3 style={{ marginTop: 16 }}>Scenarios (P&amp;L)</h3>
          <div className="table-wrap">
            <table className="heat">
              <thead>
                <tr><th>Days passed</th><th>IV change</th>{result.scenario_moves_pct.map((m) => <th key={m}>{m > 0 ? "+" : ""}{m}%</th>)}</tr>
              </thead>
              <tbody>
                {result.scenarios.map((s) => (
                  <tr key={`${s.days_passed}${s.iv_change_pts}`}>
                    <td>{s.days_passed}</td>
                    <td>{s.iv_change_pts > 0 ? "+" : ""}{s.iv_change_pts} pts</td>
                    {s.pnl.map((v, i) => <td key={i} className={tone(v)}>{signed(v, 0)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="small muted">{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </Section>
      )}
    </div>
  );
}
