import { lazy, Suspense, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { get, post, type Backtest, type Deployment, type Instrument, type StrategyTemplate, type Tearsheet } from "../api";
import { useApp } from "../app-state";
import { Dialog, Empty, Section, useData } from "../components/ui";
import { num, pct, ratio, signed, time, tone } from "../format";

// The charting library is large; load it only when a result is shown.
const LineChart = lazy(() => import("../components/LineChart").then((m) => ({ default: m.LineChart })));

const METRICS: { key: string; label: string; format: (v: number | null) => string }[] = [
  { key: "total_return", label: "Total return", format: (v) => pct(v) },
  { key: "cagr", label: "CAGR", format: (v) => pct(v) },
  { key: "sharpe_ratio", label: "Sharpe", format: (v) => ratio(v) },
  { key: "sortino_ratio", label: "Sortino", format: (v) => ratio(v) },
  { key: "max_drawdown", label: "Max drawdown", format: (v) => pct(v) },
  { key: "annualized_volatility", label: "Volatility (ann.)", format: (v) => pct(v) },
  { key: "win_rate", label: "Win rate", format: (v) => pct(v, 1) },
  { key: "profit_factor", label: "Profit factor", format: (v) => ratio(v) },
  { key: "trade_count", label: "Trades", format: (v) => (v === null ? "—" : String(v)) },
];

export function ResearchPage() {
  const { run } = useApp();
  const templates = useData(() => get<StrategyTemplate[]>("/strategy-templates"), []);
  const instruments = useData(() => get<Instrument[]>("/instruments"), []);
  const [strategy, setStrategy] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const [bars, setBars] = useState("1000");
  const [intervalSeconds, setIntervalSeconds] = useState("3600");
  const [volatility, setVolatility] = useState("0.01");
  const [drift, setDrift] = useState("0.0002");
  const [seed, setSeed] = useState("7");
  const [capital, setCapital] = useState("100000");
  const [fees, setFees] = useState("10");
  const [slippage, setSlippage] = useState("1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Backtest | null>(null);
  const [dataSource, setDataSource] = useState<"synthetic" | "history">("synthetic");
  const [feesModel, setFeesModel] = useState<"flat" | "market">("market");
  const [tested, setTested] = useState<{ strategy: string; instrumentId: string; parameters: Record<string, unknown>; interval: number } | null>(
    null,
  );
  const [paperFor, setPaperFor] = useState(false);

  const template = (templates.data ?? []).find((t) => t.name === strategy);
  useEffect(() => {
    if (!strategy && templates.data?.length) setStrategy(templates.data[0].name);
  }, [templates.data, strategy]);
  useEffect(() => {
    if (!instrumentId && instruments.data?.length) setInstrumentId(instruments.data[0].instrument_id);
  }, [instruments.data, instrumentId]);
  useEffect(() => {
    if (template)
      setParams(Object.fromEntries(Object.entries(template.parameters).map(([k, p]) => [k, String(p.default ?? "")])));
  }, [template]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const parameters: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) {
      const type = template?.parameters[k]?.type;
      parameters[k] = type === "bool" ? v === "true" : type === "int" ? Number.parseInt(v, 10) : v;
    }
    const reference = instruments.data?.find((i) => i.instrument_id === instrumentId)?.reference_price;
    const out = await run(() =>
      post<Backtest>("/backtests", {
        strategy,
        instrument_id: instrumentId,
        parameters,
        initial_capital: capital,
        maker_fee_bps: fees,
        taker_fee_bps: fees,
        slippage_bps: slippage,
        data_source: dataSource,
        fees_model: feesModel,
        data: {
          start: "2025-01-01T00:00:00Z",
          bars: Number(bars),
          interval_seconds: Number(intervalSeconds),
          start_price: reference ?? "100",
          volatility: Number(volatility),
          drift: Number(drift),
          seed: Number(seed),
        },
      }),
    );
    if (out) {
      setResult(out);
      setTested({ strategy, instrumentId, parameters, interval: Number(intervalSeconds) });
    }
    setBusy(false);
  };

  return (
    <div className="stack">
      <h1>Backtests</h1>
      <Section title="Configure">
        <form className="stack" onSubmit={submit}>
          <div className="form-grid">
            <label className="field">
              Strategy
              <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                {(templates.data ?? []).map((t) => (
                  <option key={t.name}>{t.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Instrument
              <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
                {(instruments.data ?? []).map((i) => (
                  <option key={i.instrument_id}>{i.instrument_id}</option>
                ))}
              </select>
            </label>
            {template &&
              Object.entries(template.parameters).map(([k, p]) => (
                <label className="field" key={k} title={p.description}>
                  {k}
                  <input value={params[k] ?? ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })} />
                </label>
              ))}
          </div>
          <div className="form-grid">
            <label className="field">
              Price data
              <select value={dataSource} onChange={(e) => setDataSource(e.target.value as "synthetic" | "history")}>
                <option value="synthetic">Synthetic random walk (reproducible)</option>
                <option value="history">Broker history (real prices)</option>
              </select>
            </label>
            <label className="field">
              Charges
              <select value={feesModel} onChange={(e) => setFeesModel(e.target.value as "flat" | "market")}>
                <option value="market">The market&apos;s real charges (taxes, fees, spread)</option>
                <option value="flat">A flat fee in basis points</option>
              </select>
            </label>
          </div>
          <details>
            <summary className="small">Data and costs</summary>
            <div className="form-grid" style={{ marginTop: 10 }}>
              <Field label="Bars" value={bars} set={setBars} />
              <Field label="Bar interval (s)" value={intervalSeconds} set={setIntervalSeconds} />
              <Field label="Volatility per bar" value={volatility} set={setVolatility} />
              <Field label="Drift per bar" value={drift} set={setDrift} />
              <Field label="Random seed" value={seed} set={setSeed} />
              <Field label="Initial capital" value={capital} set={setCapital} />
              <Field label="Fee (bps)" value={fees} set={setFees} />
              <Field label="Slippage (bps)" value={slippage} set={setSlippage} />
            </div>
            <p className="small muted">
              Prices are a seeded synthetic random walk, so a run with the same inputs reproduces exactly (same hash).
            </p>
          </details>
          <div className="row end">
            <button className="primary" type="submit" disabled={busy || !strategy || !instrumentId}>
              {busy ? "Running…" : "Run backtest"}
            </button>
          </div>
        </form>
      </Section>

      {result && tested && (
        <div className="alert small row" style={{ justifyContent: "space-between" }}>
          <span>
            Like the result? Paper trade <strong>{tested.strategy}</strong> on {tested.instrumentId} with live prices, no real money.
            Want to describe a strategy in your own words instead? Try the <NavLink to="/lab">Strategy lab</NavLink>.
          </span>
          <button className="primary" onClick={() => setPaperFor(true)}>
            Paper trade this…
          </button>
        </div>
      )}
      {result ? <BacktestResult result={result} /> : <Empty>Run a backtest to see its equity curve and metrics.</Empty>}
      {paperFor && tested && <PaperDialog tested={tested} onClose={() => setPaperFor(false)} />}
    </div>
  );
}

function PaperDialog({
  tested,
  onClose,
}: {
  tested: { strategy: string; instrumentId: string; parameters: Record<string, unknown>; interval: number };
  onClose: () => void;
}) {
  const { accounts, run } = useApp();
  const paper = accounts.filter((a) => a.mode === "PAPER");
  const [accountId, setAccountId] = useState(paper[0]?.account_id ?? "");
  const [interval, setIntervalSeconds] = useState(String(tested.interval));
  const [done, setDone] = useState<Deployment | null>(null);
  const start = async () => {
    const deployment = await run(async () => {
      const created = await post<Deployment>("/deployments", {
        strategy: tested.strategy,
        account_id: accountId,
        instruments: [tested.instrumentId],
        parameters: tested.parameters,
        interval_seconds: Number(interval),
      });
      await post(`/deployments/${created.deployment_id}:approve`);
      return post<Deployment>(`/deployments/${created.deployment_id}:start`);
    }, "Paper trading started");
    if (deployment) setDone(deployment);
  };
  return (
    <Dialog title="Paper trade this strategy" onClose={onClose}>
      {done ? (
        <div className="stack">
          <p style={{ margin: 0 }}>
            <strong>{done.strategy_name}</strong> is running on <strong>{done.account_id}</strong> with live prices. Follow it under{" "}
            <NavLink to="/strategies" onClick={onClose}>Strategies</NavLink>.
          </p>
          <div className="row end">
            <button onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <div className="stack">
          <p className="small muted" style={{ margin: 0 }}>
            The same strategy and parameters, on {tested.instrumentId}, trading live prices on a paper account.
          </p>
          <div className="form-grid">
            <label className="field">
              Paper account
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {paper.map((a) => (
                  <option key={a.account_id} value={a.account_id}>{a.name} ({a.account_id})</option>
                ))}
              </select>
            </label>
            <Field label="Bar size (seconds)" value={interval} set={setIntervalSeconds} />
          </div>
          <div className="row end">
            <button onClick={onClose}>Cancel</button>
            <button className="primary" onClick={start} disabled={!accountId}>Start paper trading</button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function Field({ label, value, set }: { label: string; value: string; set: (v: string) => void }) {
  return (
    <label className="field">
      {label}
      <input inputMode="decimal" value={value} onChange={(e) => set(e.target.value)} />
    </label>
  );
}

function BacktestResult({ result }: { result: Backtest }) {
  const points = result.equity_curve.map(([t, v]) => ({ time: t, value: Number(v) }));
  return (
    <>
      <div className="grid three">
        <div className="card kpi">
          <span className="label">Final equity</span>
          <span className="value">{num(result.final_equity, 2)}</span>
        </div>
        {METRICS.map((m) => {
          const v = result.metrics[m.key] ?? null;
          return (
            <div className="card kpi" key={m.key}>
              <span className="label">{m.label}</span>
              <span className={`value ${m.key === "total_return" ? tone(v) : ""}`}>{m.format(v)}</span>
            </div>
          );
        })}
      </div>
      <Section title="Equity curve" actions={<span className="small muted">hash {result.reproducibility_hash.slice(0, 12)}</span>}>
        <Suspense fallback={<div className="muted small">Loading chart…</div>}>
          <LineChart points={points} label="Backtest equity" />
        </Suspense>
      </Section>
      {result.tearsheet && result.tearsheet.monthly.length > 0 && <TearsheetView sheet={result.tearsheet} />}
      <Section title={`Trades (${result.trades.length}) · ${result.order_count} orders · ${result.fill_count} fills`}>
        {result.trades.length === 0 ? (
          <Empty>The strategy did not complete any round-trip trades.</Empty>
        ) : (
          <div className="table-wrap" style={{ maxHeight: 360 }}>
            <table>
              <thead>
                <tr>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Direction</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry price</th>
                  <th className="num">Exit price</th>
                  <th className="num">Net P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((t, i) => (
                  <tr key={i}>
                    <td className="small">{time(t.entry_time)}</td>
                    <td className="small">{time(t.exit_time)}</td>
                    <td>{t.direction}</td>
                    <td className="num">{num(t.quantity)}</td>
                    <td className="num">{num(t.entry_price)}</td>
                    <td className="num">{num(t.exit_price)}</td>
                    <td className={`num ${tone(t.net_pnl)}`}>{signed(t.net_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Assumptions">
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          {result.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </Section>
    </>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function heat(value: number | null): React.CSSProperties {
  if (value == null) return {};
  const strength = Math.min(1, Math.abs(value) / 0.1) * 45;
  return { background: `color-mix(in srgb, var(${value >= 0 ? "--pos" : "--neg"}) ${strength.toFixed(0)}%, transparent)` };
}

function TearsheetView({ sheet }: { sheet: Tearsheet }) {
  const years = sheet.yearly.map((y) => y.year);
  const cell = (year: number, month: number) => sheet.monthly.find((m) => m.year === year && m.month === month)?.return ?? null;
  return (
    <Section title="Tearsheet">
      <div className="table-wrap">
        <table className="heat" aria-label="Monthly returns">
          <thead>
            <tr><th>Year</th>{MONTHS.map((m) => <th key={m}>{m}</th>)}<th>Year</th></tr>
          </thead>
          <tbody>
            {years.map((y) => (
              <tr key={y}>
                <td>{y}</td>
                {MONTHS.map((_, i) => {
                  const v = cell(y, i + 1);
                  return <td key={i} style={heat(v)}>{v == null ? "" : pct(v, 1)}</td>;
                })}
                <td style={heat(sheet.yearly.find((r) => r.year === y)?.return ?? null)}>
                  <strong>{pct(sheet.yearly.find((r) => r.year === y)?.return ?? null, 1)}</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sheet.drawdowns.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>Worst drawdowns</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Depth</th><th>Peak</th><th>Trough</th><th>Recovered</th><th>Days down</th><th>Days to recover</th></tr></thead>
              <tbody>
                {sheet.drawdowns.map((d) => (
                  <tr key={d.peak}>
                    <td className="neg">{pct(d.depth, 1)}</td>
                    <td className="small">{time(d.peak)}</td>
                    <td className="small">{time(d.trough)}</td>
                    <td className="small">{d.recovered ? time(d.recovered) : "not yet"}</td>
                    <td>{d.days_to_trough}</td>
                    <td>{d.days_to_recover ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  );
}
