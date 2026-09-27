import { useEffect, useState } from "react";
import { accountTrades, get, post, type Deployment, type Instrument, type StrategyTemplate } from "../api";
import { useApp } from "../app-state";
import { Confirm, Dialog, Empty, ModeBadge, Section, StatusBadge, useData } from "../components/ui";

// Which lifecycle actions make sense from each state; the server still enforces the state machine.
const STOP = { action: "stop", label: "Stop", permission: "deployment:stop" };
const FLATTEN = { action: "flatten", label: "Flatten", permission: "deployment:flatten", danger: true };
const RETIRE = { action: "retire", label: "Retire", permission: "deployment:retire" };
const ACTIONS: Record<string, { action: string; label: string; permission: string; danger?: boolean }[]> = {
  DRAFT: [{ action: "approve", label: "Approve", permission: "deployment:approve" }],
  READY: [{ action: "start", label: "Start", permission: "deployment:start" }, RETIRE],
  RUNNING: [{ action: "pause", label: "Pause", permission: "deployment:pause" }, STOP, FLATTEN],
  PAUSED: [{ action: "resume", label: "Resume", permission: "deployment:start" }, STOP, FLATTEN],
  HALTED: [STOP],
  FAILED: [STOP],
  STOPPED: [{ action: "start", label: "Restart", permission: "deployment:start" }, FLATTEN, RETIRE],
};

export function StrategiesPage() {
  const { can, run } = useApp();
  const deployments = useData(() => get<Deployment[]>("/deployments"), [], 3000);
  const templates = useData(
    () => (can("strategy:view") ? get<StrategyTemplate[]>("/strategy-templates") : Promise.resolve([])),
    [],
  );
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState<{ d: Deployment; action: string; label: string; danger?: boolean } | null>(null);

  const act = async (d: Deployment, action: string, label: string) => {
    await run(() => post(`/deployments/${d.deployment_id}:${action}`), `${label}: ${d.strategy_name}`);
    deployments.reload();
  };

  return (
    <div className="stack">
      <h1>Strategies</h1>
      <Section
        title="Deployments"
        actions={
          can("deployment:create") && (
            <button className="primary" onClick={() => setCreating(true)}>
              New deployment
            </button>
          )
        }
      >
        {(deployments.data ?? []).length === 0 ? (
          <Empty>No deployments yet. Backtest a template under Backtests, then deploy it to a paper account.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Instruments</th>
                  <th>Account</th>
                  <th>Mode</th>
                  <th>State</th>
                  <th>Parameters</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {(deployments.data ?? []).map((d) => (
                  <tr key={d.deployment_id}>
                    <td>
                      <strong>{d.strategy_name}</strong> <span className="small muted">v{d.strategy_version}</span>
                      <div className="small muted">{d.deployment_id}</div>
                    </td>
                    <td className="small">{d.instruments.join(", ")}</td>
                    <td className="small">{d.account_id}</td>
                    <td><ModeBadge mode={d.mode} /></td>
                    <td title={d.state_reason ?? undefined}>
                      <StatusBadge status={d.state} />
                      {d.state_reason && <div className="small muted">{d.state_reason}</div>}
                    </td>
                    <td className="small">
                      {Object.entries(d.parameters)
                        .map(([k, v]) => `${k}=${String(v)}`)
                        .join(" · ")}
                    </td>
                    <td>
                      <span className="row">
                        {(ACTIONS[d.state] ?? [])
                          .filter((a) => can(a.permission))
                          .map((a) => (
                            <button
                              key={a.action}
                              className={`small ${a.danger ? "danger" : ""}`}
                              onClick={() =>
                                a.danger || d.mode === "LIVE"
                                  ? setPending({ d, action: a.action, label: a.label, danger: a.danger })
                                  : act(d, a.action, a.label)
                              }
                            >
                              {a.label}
                            </button>
                          ))}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Strategy templates">
        <div className="grid three">
          {(templates.data ?? []).map((t) => (
            <div key={t.name} className="card" style={{ boxShadow: "none" }}>
              <h3 style={{ marginTop: 0 }}>
                {t.name} <span className="small muted">v{t.version}</span>
              </h3>
              <p className="small">{t.description}</p>
              <ul className="small muted" style={{ paddingLeft: 18, margin: 0 }}>
                {Object.entries(t.parameters).map(([k, p]) => (
                  <li key={k}>
                    <code>{k}</code> = {String(p.default)} — {p.description}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Section>

      {creating && (
        <CreateDeployment
          templates={templates.data ?? []}
          onClose={() => {
            setCreating(false);
            deployments.reload();
          }}
        />
      )}
      {pending && (
        <Confirm
          title={`${pending.label} ${pending.d.strategy_name}?`}
          danger
          confirmLabel={pending.label}
          body={
            <p>
              {pending.action === "flatten"
                ? "Cancels this deployment's open orders and closes its positions at market."
                : `This affects a ${pending.d.mode.toLowerCase()} deployment on ${pending.d.account_id}.`}
            </p>
          }
          onCancel={() => setPending(null)}
          onConfirm={() => {
            act(pending.d, pending.action, pending.label);
            setPending(null);
          }}
        />
      )}
    </div>
  );
}

function CreateDeployment({ templates, onClose }: { templates: StrategyTemplate[]; onClose: () => void }) {
  const { accounts, run } = useApp();
  const instruments = useData(() => get<Instrument[]>("/instruments"), []);
  const [strategy, setStrategy] = useState(templates[0]?.name ?? "");
  const [accountId, setAccountId] = useState(accounts.find((a) => a.mode === "PAPER")?.account_id ?? accounts[0]?.account_id ?? "");
  const [instrumentId, setInstrumentId] = useState("");
  const [barInterval, setBarInterval] = useState("60");
  const template = templates.find((t) => t.name === strategy);
  const [params, setParams] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!template) return;
    setParams(Object.fromEntries(Object.entries(template.parameters).map(([k, p]) => [k, String(p.default ?? "")])));
  }, [template]);
  const account = accounts.find((a) => a.account_id === accountId);
  const available = (instruments.data ?? []).filter((i) => accountTrades(account, i));
  useEffect(() => {
    if (available.length && !available.some((i) => i.instrument_id === instrumentId)) setInstrumentId(available[0].instrument_id);
  }, [available, instrumentId]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parameters: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) {
      const spec = template?.parameters[k];
      parameters[k] = spec?.type === "bool" ? v === "true" : spec?.type === "int" ? Number.parseInt(v, 10) : v;
    }
    const created = await run(
      () =>
        post("/deployments", {
          strategy,
          account_id: accountId,
          instruments: [instrumentId],
          parameters,
          interval_seconds: Number(barInterval),
        }),
      "Deployment created — approve it to start",
    );
    if (created) onClose();
  };

  return (
    <Dialog title="New deployment" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            Strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {templates.map((t) => (
                <option key={t.name}>{t.name}</option>
              ))}
            </select>
          </label>
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
            <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
              {available.map((i) => (
                <option key={i.instrument_id}>{i.instrument_id}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Bar interval (seconds)
            <input inputMode="numeric" value={barInterval} onChange={(e) => setBarInterval(e.target.value)} />
          </label>
        </div>
        {template && Object.keys(template.parameters).length > 0 && (
          <fieldset className="form-grid" style={{ border: "none", padding: 0, margin: 0 }}>
            <legend className="small muted" style={{ marginBottom: 6 }}>Parameters</legend>
            {Object.entries(template.parameters).map(([k, p]) => (
              <label className="field" key={k} title={p.description}>
                {k}
                {p.type === "bool" ? (
                  <select value={params[k] ?? ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })}>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : (
                  <input value={params[k] ?? ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })} />
                )}
              </label>
            ))}
          </fieldset>
        )}
        {account?.mode === "LIVE" && (
          <div className="alert warn">This deployment will trade real money once approved and started.</div>
        )}
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit" disabled={!strategy || !accountId || !instrumentId}>
            Create
          </button>
        </div>
      </form>
    </Dialog>
  );
}
