import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get, post, put, type StrategyTemplate } from "../api";
import { useApp } from "../app-state";
import { Badge, Confirm, Dialog, Empty, Section, StatusBadge, useData } from "../components/ui";
import { pct, ratio, signed, time, tone } from "../format";

const STAGES = ["DEVELOPMENT", "BACKTEST", "VALIDATION", "PAPER", "APPROVED", "LIVE"];

interface Settings {
  validation_folds: number;
  min_validation_trades: number;
  min_paper_days: number;
  min_paper_trades: number;
}

interface Row {
  strategy_id: string;
  name: string;
  template: string;
  instrument_id: string;
  interval_seconds: number;
  live_version: string | null;
  latest_version: string;
  latest_stage: string;
  versions: number;
}

interface Version {
  version: string;
  parameters: Record<string, unknown>;
  notes: string;
  created_by: string;
  created_at: string;
  stage: string;
  backtest: { at: string; data: string; bars: number; metrics: Record<string, number | null>; trades: number; final_equity: string } | null;
  validation: {
    at: string;
    passed: boolean;
    periods: { period: number; from: string; to: string; return: number; trades: number }[];
    checks: { name: string; ok: boolean; detail: string }[];
  } | null;
  paper_deployment_id: string | null;
  paper_started_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  live_deployments: string[];
  history: { stage: string; at: string; by: string; note: string }[];
  paper_record: { days: number; trades: number; net_pnl: number; win_rate?: number | null };
  deployments: Record<string, string>;
}

interface Definition {
  strategy_id: string;
  name: string;
  template: string;
  instrument_id: string;
  interval_seconds: number;
  description: string;
  created_by: string;
  created_at: string;
  live_version: string | null;
  versions: Version[];
  settings: Settings;
}

const interval = (s: number) => (s % 86400 === 0 ? `${s / 86400}d` : s % 3600 === 0 ? `${s / 3600}h` : `${s / 60}m`);

export function PipelinePage() {
  const { can } = useApp();
  const [params, setParams] = useSearchParams();
  const list = useData(() => get<{ strategies: Row[]; settings: Settings }>("/strategies"), [], 10000);
  const [creating, setCreating] = useState(false);
  const [editingSettings, setEditingSettings] = useState(false);
  const selected = params.get("strategy");
  const open = (id: string | null) => {
    const p = new URLSearchParams(params);
    if (id) p.set("strategy", id);
    else p.delete("strategy");
    setParams(p);
  };
  const s = list.data?.settings;
  return (
    <div className="stack">
      <h1>Strategy pipeline</h1>
      <div className="stages" aria-label="Stages">
        {STAGES.map((st, i) => (
          <span key={st} className="row" style={{ gap: 4 }}>
            <span className="stage">{st}</span>
            {i < STAGES.length - 1 && <span className="muted">→</span>}
          </span>
        ))}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Every strategy version passes each gate in order: a backtest; walk-forward validation on real broker history
        {s && ` (${s.validation_folds} periods, most and the last one profitable, at least ${s.min_validation_trades} trades)`};
        paper trading with live prices{s && ` (at least ${s.min_paper_days} days and ${s.min_paper_trades} trades)`}; approval by
        a person; then a live account. Nothing written by the AI can skip a stage, and none of this guarantees profit.
      </p>
      <Section
        title="Strategies"
        actions={
          <>
            {can("deployment:approve") && <button onClick={() => setEditingSettings(true)}>Gates…</button>}
            {can("deployment:create") && <button className="primary" onClick={() => setCreating(true)}>New strategy</button>}
          </>
        }
      >
        {(list.data?.strategies ?? []).length === 0 ? (
          <Empty>No strategies registered. Create one from a template, or use “Add to pipeline” on a strategy-lab result.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>Name</th><th>Template</th><th>Instrument</th><th>Latest version</th><th>Stage</th><th>Live</th></tr></thead>
              <tbody>
                {list.data!.strategies.map((r) => (
                  <tr key={r.strategy_id} onClick={() => open(r.strategy_id)} style={{ cursor: "pointer" }} aria-selected={selected === r.strategy_id}>
                    <td><button className="link" onClick={() => open(r.strategy_id)}>{r.strategy_id}</button></td>
                    <td>{r.name}</td>
                    <td className="small">{r.template}</td>
                    <td className="small">{r.instrument_id} · {interval(r.interval_seconds)}</td>
                    <td>v{r.latest_version} <span className="small muted">({r.versions})</span></td>
                    <td><StatusBadge status={r.latest_stage} /></td>
                    <td>{r.live_version ? <Badge kind="live">● v{r.live_version}</Badge> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      {selected && <StrategyDetail key={selected} strategyId={selected} onChange={list.reload} onClose={() => open(null)} />}
      {creating && (
        <CreateDialog
          onClose={(id) => {
            setCreating(false);
            if (id) {
              list.reload();
              open(id);
            }
          }}
        />
      )}
      {editingSettings && s && <SettingsDialog settings={s} onClose={() => { setEditingSettings(false); list.reload(); }} />}
    </div>
  );
}

function StageTrack({ stage }: { stage: string }) {
  const at = STAGES.indexOf(stage);
  return (
    <div className="stages">
      {stage === "RETIRED" ? (
        <span className="stage">RETIRED</span>
      ) : (
        STAGES.map((s, i) => (
          <span key={s} className={`stage ${i < at ? "done" : i === at ? "current" : ""}`}>{s}</span>
        ))
      )}
    </div>
  );
}

function StrategyDetail({ strategyId, onChange, onClose }: { strategyId: string; onChange: () => void; onClose: () => void }) {
  const { run, can, accounts } = useApp();
  const detail = useData(() => get<Definition>(`/strategies/${strategyId}`), [strategyId], 10000);
  const [newVersion, setNewVersion] = useState(false);
  const [target, setTarget] = useState<{ version: string; kind: "paper" | "live" } | null>(null);
  const [confirm, setConfirm] = useState<{ title: string; body: string; label: string; danger?: boolean; action: () => Promise<unknown> } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const d = detail.data;
  if (!d) return <Empty>{detail.error ? "Strategy not found." : "Loading…"}</Empty>;

  const act = async (key: string, fn: () => Promise<unknown>, success: string) => {
    setBusy(key);
    const out = await run(fn, success);
    setBusy(null);
    if (out !== undefined) {
      detail.reload();
      onChange();
    }
  };
  const path = (v: string, step: string) => `/strategies/${strategyId}/versions/${v}/${step}`;
  const versions = [...d.versions].reverse();

  return (
    <Section
      title={`${d.strategy_id} · ${d.name}`}
      actions={
        <>
          {d.live_version && can("deployment:approve") && (
            <button
              onClick={() =>
                setConfirm({
                  title: "Roll back",
                  body: `Stop live v${d.live_version} and put the previous approved version live on the same accounts?`,
                  label: "Roll back",
                  danger: true,
                  action: () => post(`/strategies/${strategyId}/rollback`),
                })
              }
            >
              Roll back
            </button>
          )}
          {can("deployment:create") && <button onClick={() => setNewVersion(true)}>New version</button>}
          <button className="small" onClick={onClose} aria-label="Close strategy">✕</button>
        </>
      }
    >
      <p className="small muted">
        {d.template} on {d.instrument_id} ({interval(d.interval_seconds)} bars) · registered {time(d.created_at)}
        {d.description && ` · ${d.description.slice(0, 200)}`}
      </p>
      <div className="stack">
        {versions.map((v) => {
          const gate = d.settings;
          const paperReady = v.paper_record.days >= gate.min_paper_days && v.paper_record.trades >= gate.min_paper_trades;
          return (
            <div key={v.version} className="card stack" style={{ boxShadow: "none" }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="row">
                  <strong>v{v.version}</strong>
                  {d.live_version === v.version && <Badge kind="live">● LIVE</Badge>}
                  <span className="small muted">{v.notes} · {time(v.created_at)}</span>
                </div>
                <StageTrack stage={v.stage} />
              </div>
              <div className="small">
                {Object.entries(v.parameters).filter(([k]) => k !== "spec").map(([k, val]) => `${k}=${String(val)}`).join(" · ") || "default parameters"}
                {"spec" in v.parameters && " · typed rules from the strategy lab"}
              </div>

              <div className="grid two">
                <div>
                  <h3>Backtest</h3>
                  {v.backtest ? (
                    <>
                      <div className="small">
                        {v.backtest.data !== "broker history" && <Badge kind="warn">{v.backtest.data}</Badge>} {v.backtest.bars} bars · {v.backtest.trades} trades · {time(v.backtest.at)}
                      </div>
                      <dl className="kv small">
                        <dt>Return</dt><dd className={tone(v.backtest.metrics.total_return)}>{pct(v.backtest.metrics.total_return)}</dd>
                        <dt>CAGR</dt><dd>{pct(v.backtest.metrics.cagr)}</dd>
                        <dt>Sharpe / Sortino</dt><dd>{ratio(v.backtest.metrics.sharpe_ratio)} / {ratio(v.backtest.metrics.sortino_ratio)}</dd>
                        <dt>Max drawdown</dt><dd>{pct(v.backtest.metrics.max_drawdown)}</dd>
                        <dt>Profit factor</dt><dd>{ratio(v.backtest.metrics.profit_factor)}</dd>
                        <dt>Win rate</dt><dd>{pct(v.backtest.metrics.win_rate, 0)}</dd>
                        <dt>Losses in a row</dt><dd>{v.backtest.metrics.max_consecutive_losses ?? "—"}</dd>
                      </dl>
                    </>
                  ) : (
                    <p className="small muted">Not run.</p>
                  )}
                </div>
                <div>
                  <h3>Walk-forward validation</h3>
                  {v.validation ? (
                    <>
                      <div className={v.validation.passed ? "pos small" : "neg small"}>{v.validation.passed ? "Passed" : "Failed"} · {time(v.validation.at)}</div>
                      <ul className="small" style={{ paddingLeft: 18, margin: "4px 0" }}>
                        {v.validation.checks.map((c) => (
                          <li key={c.name} className={c.ok ? "" : "neg"}>{c.ok ? "✓" : "✗"} {c.name}: {c.detail}</li>
                        ))}
                      </ul>
                      <div className="small muted">
                        {v.validation.periods.map((p) => `P${p.period} ${signed(p.return * 100)}% (${p.trades})`).join(" · ")}
                      </div>
                    </>
                  ) : (
                    <p className="small muted">Not run. Needs at least 400 bars of real history from a connected broker.</p>
                  )}
                </div>
              </div>

              {(v.paper_deployment_id || v.live_deployments.length > 0) && (
                <div className="small">
                  <strong>Paper record:</strong> {v.paper_record.days} days · {v.paper_record.trades} trades ·{" "}
                  <span className={tone(v.paper_record.net_pnl)}>{signed(v.paper_record.net_pnl)}</span>
                  {v.paper_record.win_rate != null && ` · ${pct(v.paper_record.win_rate, 0)} won`}
                  {" "}(approval needs {gate.min_paper_days} days and {gate.min_paper_trades} trades)
                  {Object.entries(v.deployments).map(([dep, state]) => (
                    <span key={dep} style={{ marginLeft: 8 }}>
                      {dep} <StatusBadge status={state} />
                    </span>
                  ))}
                </div>
              )}
              {v.approved_at && <div className="small">Approved {time(v.approved_at)}{v.approved_by === v.created_by ? " (single-user mode: approved by its author)" : " by a second person"}</div>}

              {v.stage !== "RETIRED" && (
                <div className="row">
                  {can("backtest:run") && (
                    <button disabled={busy !== null} onClick={() => act(`bt${v.version}`, () => post(path(v.version, "backtest"), { bars: 2000 }), "Backtest done")}>
                      {busy === `bt${v.version}` ? "Backtesting…" : "Backtest"}
                    </button>
                  )}
                  {can("backtest:run") && ["BACKTEST", "VALIDATION"].includes(v.stage) && (
                    <button disabled={busy !== null} onClick={() => act(`va${v.version}`, () => post(path(v.version, "validate"), { bars: 3000 }), "Validation finished")}>
                      {busy === `va${v.version}` ? "Validating…" : "Validate (walk-forward)"}
                    </button>
                  )}
                  {can("deployment:start") && v.stage === "VALIDATION" && (
                    <button className="primary" onClick={() => setTarget({ version: v.version, kind: "paper" })}>Start paper trading</button>
                  )}
                  {can("deployment:approve") && v.stage === "PAPER" && (
                    <button
                      className="primary"
                      disabled={!paperReady}
                      title={paperReady ? undefined : "The paper record is not long enough yet"}
                      onClick={() =>
                        setConfirm({
                          title: `Approve v${v.version}`,
                          body: `Approve v${v.version} for live trading after ${v.paper_record.days} days and ${v.paper_record.trades} paper trades (${signed(v.paper_record.net_pnl)})? A second person must approve a strategy they did not write.`,
                          label: "Approve",
                          action: () => post(path(v.version, "approve"), {}),
                        })
                      }
                    >
                      Approve for live
                    </button>
                  )}
                  {can("deployment:approve") && ["APPROVED", "LIVE"].includes(v.stage) && (
                    <button className="danger" onClick={() => setTarget({ version: v.version, kind: "live" })}>Go live…</button>
                  )}
                  {can("deployment:retire") && (
                    <button
                      onClick={() =>
                        setConfirm({
                          title: `Retire v${v.version}`,
                          body: "Stops its paper and live deployments. The version and its history are kept.",
                          label: "Retire",
                          danger: true,
                          action: () => post(path(v.version, "retire")),
                        })
                      }
                    >
                      Retire
                    </button>
                  )}
                </div>
              )}
              <details>
                <summary className="small">History ({v.history.length})</summary>
                <ol className="timeline small" style={{ marginTop: 8 }}>
                  {v.history.map((h, i) => (
                    <li key={i}><strong>{h.stage}</strong> · {time(h.at)} · {h.by} · {h.note}</li>
                  ))}
                </ol>
              </details>
            </div>
          );
        })}
      </div>
      {newVersion && (
        <VersionDialog
          template={d.template}
          parameters={d.versions[d.versions.length - 1].parameters}
          onClose={async (values) => {
            setNewVersion(false);
            if (values) await act("nv", () => post(`/strategies/${strategyId}/versions`, values), "New version created");
          }}
        />
      )}
      {target && (
        <AccountDialog
          kind={target.kind}
          accounts={accounts.filter((a) => a.mode === (target.kind === "paper" ? "PAPER" : "LIVE") && a.status === "ACTIVE")}
          version={target.version}
          onClose={async (accountId) => {
            const t = target;
            setTarget(null);
            if (accountId)
              await act(t.kind, () => post(path(t.version, t.kind), { account_id: accountId }), t.kind === "paper" ? "Paper trading started" : "Live trading started");
          }}
        />
      )}
      {confirm && (
        <Confirm
          title={confirm.title}
          body={confirm.body}
          confirmLabel={confirm.label}
          danger={confirm.danger}
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            const c = confirm;
            setConfirm(null);
            await act("confirm", c.action, `${c.label}: done`);
          }}
        />
      )}
    </Section>
  );
}

function ParameterFields({ template, values, onChange }: { template?: StrategyTemplate; values: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  if (!template) return null;
  return (
    <div className="form-grid">
      {Object.entries(template.parameters)
        .filter(([k]) => k !== "spec")
        .map(([k, p]) => (
          <label key={k} className="field" title={p.description}>
            {k}
            {p.type === "bool" ? (
              <input type="checkbox" checked={Boolean(values[k] ?? p.default)} onChange={(e) => onChange({ ...values, [k]: e.target.checked })} />
            ) : (
              <input
                value={String(values[k] ?? p.default ?? "")}
                onChange={(e) => onChange({ ...values, [k]: p.type === "int" || p.type === "float" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value })}
              />
            )}
          </label>
        ))}
    </div>
  );
}

function CreateDialog({ onClose }: { onClose: (strategyId?: string) => void }) {
  const { run } = useApp();
  const templates = useData(() => get<StrategyTemplate[]>("/strategy-templates"), []);
  const choices = (templates.data ?? []).filter((t) => !["autopilot", "ai_trader", "rotation", "rules"].includes(t.name));
  const [form, setForm] = useState({ name: "", template: "", instrument_id: "", interval_seconds: 3600, description: "" });
  const [parameters, setParameters] = useState<Record<string, unknown>>({});
  useEffect(() => {
    if (!form.template && choices.length) setForm((f) => ({ ...f, template: choices[0].name }));
  }, [choices, form.template]);
  const template = choices.find((t) => t.name === form.template);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = Object.fromEntries(Object.entries(parameters).filter(([, v]) => v !== ""));
    const created = await run(() => post<Definition>("/strategies", { ...form, parameters: clean }), "Strategy registered");
    if (created) onClose(created.strategy_id);
  };
  return (
    <Dialog title="New strategy" onClose={() => onClose()}>
      <form className="stack" onSubmit={submit}>
        <div className="form-grid">
          <label className="field">Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Trend on gold" /></label>
          <label className="field">
            Template
            <select value={form.template} onChange={(e) => { setForm({ ...form, template: e.target.value }); setParameters({}); }}>
              {choices.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </select>
          </label>
          <label className="field">Instrument<input required value={form.instrument_id} onChange={(e) => setForm({ ...form, instrument_id: e.target.value })} placeholder="OANDA:XAU_USD" /></label>
          <label className="field">
            Bars
            <select value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) })}>
              {[[300, "5 minutes"], [900, "15 minutes"], [3600, "1 hour"], [14400, "4 hours"], [86400, "1 day"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        </div>
        {template && <p className="small muted">{template.description}</p>}
        <ParameterFields template={template} values={parameters} onChange={setParameters} />
        <label className="field">Notes<textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
        <div className="row end">
          <button type="button" onClick={() => onClose()}>Cancel</button>
          <button className="primary" type="submit">Register</button>
        </div>
      </form>
    </Dialog>
  );
}

function VersionDialog({ template: name, parameters, onClose }: { template: string; parameters: Record<string, unknown>; onClose: (v?: { parameters: Record<string, unknown>; notes: string }) => void }) {
  const templates = useData(() => get<StrategyTemplate[]>("/strategy-templates"), []);
  const template = templates.data?.find((t) => t.name === name);
  const [values, setValues] = useState<Record<string, unknown>>(parameters);
  const [notes, setNotes] = useState("");
  return (
    <Dialog title="New version" onClose={() => onClose()}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onClose({ parameters: Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "")), notes });
        }}
      >
        <p className="small muted">A new version starts again at DEVELOPMENT; the current live version keeps running until the new one is approved and put live.</p>
        <ParameterFields template={template} values={values} onChange={setValues} />
        <label className="field">What changed and why<textarea required rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <div className="row end">
          <button type="button" onClick={() => onClose()}>Cancel</button>
          <button className="primary" type="submit">Create version</button>
        </div>
      </form>
    </Dialog>
  );
}

function AccountDialog({
  kind,
  accounts,
  version,
  onClose,
}: {
  kind: "paper" | "live";
  accounts: { account_id: string; name: string; mode: string; venue: string }[];
  version: string;
  onClose: (accountId?: string) => void;
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.account_id ?? "");
  const [understood, setUnderstood] = useState(false);
  return (
    <Dialog title={kind === "paper" ? `Paper trade v${version}` : `Put v${version} live`} onClose={() => onClose()}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onClose(accountId);
        }}
      >
        {accounts.length === 0 ? (
          <div className="alert warn">No active {kind} account. {kind === "live" ? "Connect a broker with a live account under Connections." : ""}</div>
        ) : (
          <label className="field">
            Account
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.name} ({a.venue})</option>)}
            </select>
          </label>
        )}
        {kind === "live" && (
          <>
            <div className="alert warn small">
              This trades real money. The account's risk limits, guards, circuit breakers and kill switches apply to every order.
              Past and paper results do not guarantee future results. Any version of this strategy already live is stopped first.
            </div>
            <label className="field inline">
              <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand
            </label>
          </>
        )}
        <div className="row end">
          <button type="button" onClick={() => onClose()}>Cancel</button>
          <button className={kind === "live" ? "danger" : "primary"} type="submit" disabled={!accountId || (kind === "live" && !understood)}>
            {kind === "paper" ? "Start paper trading" : "Go live"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function SettingsDialog({ settings, onClose }: { settings: Settings; onClose: () => void }) {
  const { run } = useApp();
  const [form, setForm] = useState(settings);
  const field = (k: keyof Settings, label: string) => (
    <label className="field">
      {label}
      <input type="number" min={0} step="any" value={form[k]} onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />
    </label>
  );
  return (
    <Dialog title="Pipeline gates" onClose={onClose}>
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => put("/strategies/settings", form), "Gates saved")) onClose();
        }}
      >
        <div className="form-grid">
          {field("validation_folds", "Walk-forward periods")}
          {field("min_validation_trades", "Minimum validation trades")}
          {field("min_paper_days", "Minimum paper days")}
          {field("min_paper_trades", "Minimum paper trades")}
        </div>
        <p className="small muted">Lowering the gates makes approval easier, not strategies better.</p>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Save</button>
        </div>
      </form>
    </Dialog>
  );
}

