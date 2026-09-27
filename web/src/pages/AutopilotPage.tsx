import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  get,
  post,
  put,
  type AutopilotConfig,
  type AutopilotDecision,
  type AutopilotStatus,
  type EvaluationSummary,
  type Instrument,
  type SelectedStrategy,
} from "../api";
import { useApp } from "../app-state";
import { Badge, Confirm, Dialog, Empty, ModeBadge, Section, useData } from "../components/ui";
import { num, pct, ratio, signed, time, tone } from "../format";

const LineChart = lazy(() => import("../components/LineChart").then((m) => ({ default: m.LineChart })));

const INTERVALS: [number, string][] = [
  [86400, "Daily bars (swing, hold for days)"],
  [3600, "1-hour bars"],
  [900, "15-minute bars"],
  [300, "5-minute bars"],
];

const DECISION_KIND: Record<string, string> = {
  DEPLOY: "good",
  PROMOTE: "live",
  RETIRE: "bad",
  READY_FOR_LIVE: "warn",
  SKIP: "",
  KEEP: "",
  CYCLE: "ai",
  ARM: "live",
  DISARM: "warn",
  ERROR: "bad",
};

export function AutopilotPage() {
  const { can, run, notify } = useApp();
  const status = useData(() => get<AutopilotStatus>("/autopilot"), [], 3000);
  const decisions = useData(() => get<AutopilotDecision[]>("/autopilot/decisions?limit=60"), [], 5000);
  const [editing, setEditing] = useState(false);
  const [arming, setArming] = useState(false);
  const [disarming, setDisarming] = useState(false);
  const s = status.data;

  const refresh = () => {
    status.reload();
    decisions.reload();
  };
  const runNow = async () => {
    const out = await run(() => post<{ started: boolean }>("/autopilot:run"));
    if (out && !out.started) notify("A research cycle is already running.");
    refresh();
  };
  const toggle = async (enabled: boolean) => {
    await run(() => put("/autopilot/config", { enabled }), enabled ? "Autopilot scheduled" : "Autopilot paused");
    refresh();
  };

  if (!s) return <div className="muted">Loading…</div>;
  const latest = s.latest_run;
  const active = s.managed.filter((m) => m.status !== "RETIRED");
  const progress = s.progress;

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>
          AI Autopilot <Badge kind="ai">AI</Badge>
        </h1>
        <div className="row">
          {s.config.enabled ? <Badge kind="good">Scheduled</Badge> : <Badge>Manual</Badge>}
          {s.live.armed ? <Badge kind="live">● Live armed · cap {num(s.live.capital_cap, 0)}</Badge> : <Badge kind="paper">◌ Paper only</Badge>}
        </div>
      </div>

      <HowItWorks />

      <div className="grid two" style={{ alignItems: "start" }}>
        <Section
          title="Research"
          actions={
            <>
              {can("autopilot:configure") && <button onClick={() => setEditing(true)}>Settings</button>}
              {can("autopilot:run") && (
                <button className="primary" onClick={runNow} disabled={progress.running || !s.config.universe.length}>
                  {progress.running ? "Researching…" : "Run research now"}
                </button>
              )}
            </>
          }
        >
          {progress.running ? (
            <div className="stack" aria-live="polite">
              <progress max={progress.total || 1} value={progress.done || 0} style={{ width: "100%" }} />
              <span className="small muted">
                {progress.done ?? 0} / {progress.total ?? "…"} backtests · {progress.message}
              </span>
            </div>
          ) : !s.config.universe.length ? (
            <div className="alert warn">
              Choose the stocks the autopilot may trade under <button className="link" onClick={() => setEditing(true)}>Settings</button>.
            </div>
          ) : null}
          <dl className="kv small">
            <dt>Universe</dt>
            <dd>{s.config.universe.length ? s.config.universe.join(", ") : "—"}</dd>
            <dt>Budget</dt>
            <dd>
              {num(s.config.capital, 0)} across up to {s.config.max_positions} stocks (max {pct(s.config.max_weight, 0)} each), {s.config.product === "CNC" ? "delivery" : "intraday"}
            </dd>
            <dt>Bars</dt>
            <dd>{INTERVALS.find(([v]) => v === s.config.interval_seconds)?.[1] ?? `${s.config.interval_seconds}s`}</dd>
            <dt>Last run</dt>
            <dd>{time(s.last_run_at)}</dd>
            <dt>Next run</dt>
            <dd>{s.config.enabled ? time(s.next_run_at) : "only when you press Run"}</dd>
          </dl>
          {can("autopilot:configure") && (
            <label className="field inline" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={s.config.enabled} onChange={(e) => toggle(e.target.checked)} /> Research and rebalance automatically{" "}
              {s.config.interval_seconds >= 86400 ? "after each NSE close" : `every ${s.config.cycle_hours} h`}
            </label>
          )}
        </Section>

        <Section title="Live trading">
          {s.live.armed ? (
            <div className="stack">
              <p style={{ margin: 0 }}>
                The autopilot may promote strategies with a clean paper record to <strong>{s.live.account_id}</strong>, using at most{" "}
                <strong>{num(s.live.capital_cap, 0)}</strong> in total. Armed by {s.live.armed_by} {time(s.live.armed_at)}.
              </p>
              {can("autopilot:disarm") && (
                <div>
                  <button className="danger" onClick={() => setDisarming(true)}>
                    Disarm and close live positions
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="stack">
              <p style={{ margin: 0 }}>
                Everything runs on the <strong>{s.paper_account}</strong> paper account. A strategy becomes eligible for real orders after{" "}
                {s.config.min_paper_days} days and {s.config.min_paper_trades} trades on paper without breaching its limits.
              </p>
              {can("autopilot:arm") && (
                <div>
                  <button className="primary" onClick={() => setArming(true)}>
                    Arm live trading…
                  </button>
                </div>
              )}
            </div>
          )}
        </Section>
      </div>

      {latest && <LatestRun run={latest} />}

      <Section title={`AI deployments (${active.length})`} actions={<NavLink to="/strategies">All deployments</NavLink>}>
        {active.length === 0 ? (
          <Empty>No strategy has earned capital yet. The autopilot stays in cash until one passes every test.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Strategy</th>
                  <th>Mode</th>
                  <th className="num">Capital</th>
                  <th className="num">P&amp;L</th>
                  <th className="num">Expected Sharpe</th>
                  <th>Since</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {active.map((m) => (
                  <tr key={m.deployment_id}>
                    <td>{m.instrument_id}</td>
                    <td className="small">{m.label}</td>
                    <td><ModeBadge mode={m.mode} /></td>
                    <td className="num">{num(m.capital, 0)}</td>
                    <td className={`num ${tone(m.pnl)}`}>{signed(m.pnl)}</td>
                    <td className="num">{ratio(m.expected.sharpe ?? null)}</td>
                    <td className="small">{time(m.created_at)}</td>
                    <td className="small">{m.status === "CLOSING" ? "closing out" : m.live_deployment ? "live copy running" : "active"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Decision log">
        {(decisions.data ?? []).length === 0 ? (
          <Empty>Decisions appear here after the first research cycle.</Empty>
        ) : (
          <ol className="timeline">
            {(decisions.data ?? []).map((d) => (
              <li key={d.decision_id}>
                <div className="row">
                  <Badge kind={DECISION_KIND[d.kind] ?? ""}>{d.kind.replaceAll("_", " ")}</Badge>
                  <strong>{d.title}</strong>
                  <span className="small muted">{time(d.at)}{d.actor !== "autopilot" ? ` · ${d.actor}` : ""}</span>
                </div>
                {d.reasons.length > 0 && (
                  <ul className="small muted">
                    {d.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </Section>

      {editing && <SettingsDialog config={s.config} onClose={() => { setEditing(false); refresh(); }} />}
      {arming && <ArmDialog onClose={() => { setArming(false); refresh(); }} />}
      {disarming && (
        <Confirm
          title="Disarm live trading?"
          danger
          confirmLabel="Disarm and close"
          body={<p>Live autopilot deployments sell their positions at market and stop. Paper trading continues.</p>}
          onCancel={() => setDisarming(false)}
          onConfirm={async () => {
            setDisarming(false);
            await run(() => post("/autopilot/live:disarm", { flatten: true }), "Live trading disarmed");
            refresh();
          }}
        />
      )}
    </div>
  );
}

function HowItWorks() {
  return (
    <details className="card" style={{ boxShadow: "none" }}>
      <summary>
        <strong>How the autopilot decides</strong> <span className="small muted">— research, test, trade, watch</span>
      </summary>
      <ol className="small" style={{ marginBottom: 0 }}>
        <li>
          <strong>Research.</strong> For every stock in the universe it tries 27 strategies (29 on intraday bars): trend following
          (moving averages, MACD, Supertrend, momentum), mean reversion (RSI, Bollinger bands, RSI(2) pullbacks in an uptrend), breakouts
          (price channels, volume surges, the opening range) and machine-learning models.
        </li>
        <li>
          <strong>Walk-forward backtest.</strong> Each strategy trades history it was never tuned on, period by period, with ML models
          retrained only on the past, Fyers-style charges and slippage.
        </li>
        <li>
          <strong>Reject luck.</strong> Trying many strategies guarantees some look good by chance, so a result must beat what the best of
          that many skill-less strategies would reach, and also make money in a final untouched period.
        </li>
        <li>
          <strong>Trade and watch.</strong> Winners get capital on paper, sized by risk. Strategies that stop working or hit their drawdown
          limit are closed out. Proven ones move to real orders only if you arm live trading, within your cap. Risk limits and the kill
          switch always apply.
        </li>
      </ol>
    </details>
  );
}

function LatestRun({ run }: { run: NonNullable<AutopilotStatus["latest_run"]> }) {
  const [chartFor, setChartFor] = useState<SelectedStrategy | null>(run.selected[0] ?? null);
  useEffect(() => setChartFor(run.selected[0] ?? null), [run.run_id, run.selected]);
  const passed = run.leaderboard.filter((e) => e.passed).length;
  const synthetic = run.data_source.includes("synthetic");
  return (
    <>
      {run.error && <div className="alert error">Last research cycle failed: {run.error}</div>}
      {synthetic && (
        <div className="alert warn">
          The last cycle used <strong>synthetic demo data</strong> because no broker history was available. Connect Fyers under{" "}
          <NavLink to="/connections">Connections</NavLink> so research runs on real NSE prices.
        </div>
      )}
      {run.summary && (
        <Section title="Briefing" actions={<Badge kind="ai">AI-generated</Badge>}>
          <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{run.summary}</p>
        </Section>
      )}
      <div className="grid three">
        <div className="card kpi">
          <span className="label">Strategies tested</span>
          <span className="value">{run.trials}</span>
        </div>
        <div className="card kpi">
          <span className="label">Passed every test</span>
          <span className="value">{passed}</span>
        </div>
        <div className="card kpi">
          <span className="label">Selected for capital</span>
          <span className="value">{run.selected.length}</span>
        </div>
      </div>
      {run.selected.length > 0 && (
        <Section title="Selected strategies" actions={<span className="small muted">out-of-sample, after charges · {time(run.started_at)}</span>}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Strategy</th>
                  <th className="num">Capital</th>
                  <th className="num">Return</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Max drawdown</th>
                  <th className="num">Holdout</th>
                  <th className="num">Luck-adjusted confidence</th>
                  <th><span className="sr-only">Chart</span></th>
                </tr>
              </thead>
              <tbody>
                {run.selected.map((e) => (
                  <tr key={e.instrument_id} aria-selected={chartFor?.instrument_id === e.instrument_id}>
                    <td>{e.instrument_id}</td>
                    <td className="small">{e.label}</td>
                    <td className="num">{num(e.capital, 0)}</td>
                    <td className={`num ${tone(e.validation.return)}`}>{pct(e.validation.return)}</td>
                    <td className="num">{ratio(e.validation.sharpe)}</td>
                    <td className="num">{pct(e.validation.max_drawdown)}</td>
                    <td className={`num ${tone(e.holdout.return)}`}>{pct(e.holdout.return)}</td>
                    <td className="num">{pct(e.dsr, 0)}</td>
                    <td>
                      <button className="small" onClick={() => setChartFor(e)}>Equity</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {chartFor && (
            <div style={{ marginTop: 12 }}>
              <div className="small muted" style={{ marginBottom: 4 }}>
                {chartFor.instrument_id} · {chartFor.label}: growth of 1 over the out-of-sample periods (last period = holdout)
              </div>
              <Suspense fallback={<div className="muted small">Loading chart…</div>}>
                <LineChart points={chartFor.equity.map(([t, v]) => ({ time: t, value: v }))} label={`${chartFor.instrument_id} out-of-sample equity`} />
              </Suspense>
            </div>
          )}
        </Section>
      )}
      <Leaderboard rows={run.leaderboard} skipped={run.skipped} />
    </>
  );
}

function Leaderboard({ rows, skipped }: { rows: EvaluationSummary[]; skipped: Record<string, string> }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? rows : rows.slice(0, 12);
  return (
    <Section
      title="Leaderboard"
      actions={rows.length > 12 && <button className="small" onClick={() => setShowAll((v) => !v)}>{showAll ? "Show top 12" : `Show all ${rows.length}`}</button>}
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Stock</th>
              <th>Strategy</th>
              <th className="num">Return</th>
              <th className="num">Sharpe</th>
              <th className="num">Drawdown</th>
              <th className="num">Trades</th>
              <th className="num">Holdout</th>
              <th className="num">Confidence</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={`${e.instrument_id}-${e.candidate}`}>
                <td className="small">{e.instrument_id}</td>
                <td className="small">{e.label}</td>
                <td className={`num ${tone(e.validation.return)}`}>{pct(e.validation.return)}</td>
                <td className="num">{ratio(e.validation.sharpe)}</td>
                <td className="num">{pct(e.validation.max_drawdown)}</td>
                <td className="num">{e.validation.trades}</td>
                <td className={`num ${tone(e.holdout.return)}`}>{pct(e.holdout.return)}</td>
                <td className="num">{pct(e.dsr, 0)}</td>
                <td className="small" style={{ whiteSpace: "normal", minWidth: 240 }}>
                  {e.passed ? <Badge kind="good">passed</Badge> : <span className="muted">{e.reasons.join("; ")}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(skipped).length > 0 && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          Skipped: {Object.entries(skipped).map(([k, v]) => `${k} (${v})`).join("; ")}
        </p>
      )}
    </Section>
  );
}

function SettingsDialog({ config, onClose }: { config: AutopilotConfig; onClose: () => void }) {
  const { run } = useApp();
  const instruments = useData(() => get<Instrument[]>("/instruments"), []);
  const [form, setForm] = useState<AutopilotConfig>(config);
  const [query, setQuery] = useState("");
  const set = <K extends keyof AutopilotConfig>(key: K, value: AutopilotConfig[K]) => setForm((f) => ({ ...f, [key]: value }));
  const choices = useMemo(
    () =>
      (instruments.data ?? [])
        .filter((i) => i.asset_class === "EQUITY" && (!query || i.instrument_id.toLowerCase().includes(query.toLowerCase())))
        .slice(0, 200),
    [instruments.data, query],
  );
  const numberField = (key: keyof AutopilotConfig, label: string, hint?: string) => (
    <label className="field" title={hint}>
      {label}
      <input inputMode="decimal" value={String(form[key])} onChange={(e) => set(key, e.target.value as never)} />
    </label>
  );
  const toNumbers = (): AutopilotConfig => ({
    ...form,
    max_positions: Number(form.max_positions),
    max_weight: Number(form.max_weight),
    min_sharpe: Number(form.min_sharpe),
    max_drawdown: Number(form.max_drawdown),
    min_dsr: Number(form.min_dsr),
    min_trades: Number(form.min_trades),
    history_bars: Number(form.history_bars),
    cycle_hours: Number(form.cycle_hours),
    max_deployment_drawdown: Number(form.max_deployment_drawdown),
    min_paper_days: Number(form.min_paper_days),
    min_paper_trades: Number(form.min_paper_trades),
  });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const saved = await run(() => put("/autopilot/config", toNumbers()), "Autopilot settings saved");
    if (saved) onClose();
  };

  return (
    <Dialog title="Autopilot settings" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="small muted" style={{ marginBottom: 6 }}>
            Universe ({form.universe.length} selected) — the stocks the autopilot may trade
          </legend>
          <input placeholder="Search NSE stocks…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search stocks" />
          <div className="chips" style={{ maxHeight: 160, overflow: "auto", marginTop: 6 }}>
            {choices.map((i) => (
              <label key={i.instrument_id} className="field inline small">
                <input
                  type="checkbox"
                  checked={form.universe.includes(i.instrument_id)}
                  onChange={(e) =>
                    set("universe", e.target.checked ? [...form.universe, i.instrument_id] : form.universe.filter((x) => x !== i.instrument_id))
                  }
                />{" "}
                {i.instrument_id}
              </label>
            ))}
            {choices.length === 0 && <span className="small muted">No equities found. Connect Fyers to load NSE stocks.</span>}
          </div>
        </fieldset>
        <div className="form-grid">
          {numberField("capital", "Paper budget (₹)")}
          {numberField("max_positions", "Max stocks at once")}
          {numberField("max_weight", "Max share per stock (0–1)")}
          <label className="field">
            Product
            <select value={form.product} onChange={(e) => set("product", e.target.value as AutopilotConfig["product"])}>
              <option value="CNC">Delivery (CNC)</option>
              <option value="INTRADAY">Intraday (closed by 15:10)</option>
            </select>
          </label>
          <label className="field">
            Bar size
            <select value={form.interval_seconds} onChange={(e) => set("interval_seconds", Number(e.target.value))}>
              {INTERVALS.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </label>
          {numberField("stop_loss", "Stop-loss (fraction)", "0.08 = exit 8% below entry; 0 disables")}
          {numberField("trailing_stop", "Trailing stop (fraction)", "0.05 = exit 5% below the highest close since entry; 0 disables")}
          {numberField("history_bars", "History (bars)")}
          <label className="field">
            Data
            <select value={form.data_source} onChange={(e) => set("data_source", e.target.value as AutopilotConfig["data_source"])}>
              <option value="auto">Broker history, else demo data</option>
              <option value="venue">Broker history only</option>
              <option value="synthetic">Synthetic demo data</option>
            </select>
          </label>
        </div>
        <details>
          <summary className="small">Evidence required and safety limits</summary>
          <div className="form-grid" style={{ marginTop: 8 }}>
            {numberField("min_sharpe", "Min out-of-sample Sharpe")}
            {numberField("max_drawdown", "Max backtest drawdown (0–1)")}
            {numberField("min_dsr", "Min luck-adjusted confidence (0–1)")}
            {numberField("min_trades", "Min trades in validation")}
            {numberField("max_deployment_drawdown", "Close out at drawdown of capital (0–1)")}
            {numberField("min_paper_days", "Paper days before live")}
            {numberField("min_paper_trades", "Paper trades before live")}
            {numberField("cycle_hours", "Cycle every (hours, intraday bars)")}
          </div>
          <label className="field inline" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={form.explain_with_claude} onChange={(e) => set("explain_with_claude", e.target.checked)} /> Ask
            Claude for a plain-language briefing after each cycle
          </label>
        </details>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Save</button>
        </div>
      </form>
    </Dialog>
  );
}

function ArmDialog({ onClose }: { onClose: () => void }) {
  const { accounts, run } = useApp();
  const live = accounts.filter((a) => a.mode === "LIVE");
  const [accountId, setAccountId] = useState(live[0]?.account_id ?? "");
  const [cap, setCap] = useState("");
  const [understood, setUnderstood] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(() => post("/autopilot/live:arm", { account_id: accountId, capital_cap: cap }), "Live trading armed");
    if (out) onClose();
  };
  return (
    <Dialog title="Arm live trading" onClose={onClose}>
      {live.length === 0 ? (
        <div className="stack">
          <p style={{ margin: 0 }}>Connect and sign in to your Fyers account first; it appears here as a live account.</p>
          <div className="row end">
            <NavLink to="/connections" className="button" onClick={onClose}>Go to Connections</NavLink>
          </div>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <div className="alert warn">
            The autopilot will place <strong>real orders</strong> for strategies that have proven themselves on paper, without asking each
            time. Past performance, including backtests and paper trading, does not guarantee future results.
          </div>
          <div className="form-grid">
            <label className="field">
              Broker account
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {live.map((a) => (
                  <option key={a.account_id} value={a.account_id}>{a.name} ({a.account_id})</option>
                ))}
              </select>
            </label>
            <label className="field">
              Maximum capital to use (₹)
              <input required inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} placeholder="e.g. 50000" />
            </label>
          </div>
          <label className="field inline">
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand real money will be
            traded automatically and that I can disarm at any time.
          </label>
          <div className="row end">
            <button type="button" onClick={onClose}>Cancel</button>
            <button className="danger" type="submit" disabled={!understood || !cap}>Arm live trading</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
