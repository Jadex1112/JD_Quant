import { useEffect, useState } from "react";
import { get, post, type Instrument, type ModelVersion } from "../api";
import { useApp } from "../app-state";
import { Badge, Dialog, Empty, Section, StatusBadge, useData } from "../components/ui";
import { pct, ratio, time } from "../format";

interface ModelGroup {
  model: string;
  versions: ModelVersion[];
}

interface Prediction {
  inference_id: string;
  model: string;
  version: number;
  value: number;
  features: Record<string, number>;
}

interface Optimization {
  method: string;
  weights: Record<string, number>;
  expected_return: number;
  expected_volatility: number;
  sharpe: number | null;
  risk_contributions: Record<string, number>;
}

const NEXT_STAGE: Record<string, string> = { NONE: "STAGING", STAGING: "SHADOW", SHADOW: "PRODUCTION" };

export function AiPage() {
  const { can, run } = useApp();
  const models = useData(() => get<ModelGroup[]>("/models"), []);
  const instruments = useData(() => get<Instrument[]>("/instruments"), []);
  const [training, setTraining] = useState(false);
  const [promoting, setPromoting] = useState<ModelVersion | null>(null);
  const [card, setCard] = useState<ModelVersion | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [drift, setDrift] = useState<{ model: string; psi: Record<string, number | null> } | null>(null);

  const predict = async (group: ModelGroup) => {
    const production = group.versions.find((v) => v.stage === "PRODUCTION");
    const out = await run(() =>
      post<Prediction>(`/models/${group.model}:predict`, { instrument_id: production?.instrument_id ?? instruments.data?.[0]?.instrument_id }),
    );
    if (out) setPrediction(out);
  };
  const checkDrift = async (name: string) => {
    const out = await run(() => get<Record<string, number | null>>(`/models/${name}/drift`));
    if (out) setDrift({ model: name, psi: out });
  };

  return (
    <div className="stack">
      <h1>
        AI models <Badge kind="ai">AI</Badge>
      </h1>
      <div className="alert">
        Models are trained on historical bars with a purged time split, and must pass through staging and shadow before
        production. Predictions are statistical estimates, not advice; a production model only influences trading
        through a strategy deployment that uses it, which still goes through risk checks.
      </div>
      <Section
        title="Registry"
        actions={
          can("model:train") && (
            <button className="primary" onClick={() => setTraining(true)}>
              Train model
            </button>
          )
        }
      >
        {(models.data ?? []).length === 0 ? (
          <Empty>No models yet.</Empty>
        ) : (
          (models.data ?? []).map((g) => (
            <div key={g.model} style={{ marginBottom: 16 }}>
              <div className="row" style={{ marginBottom: 6 }}>
                <h3 style={{ margin: 0 }}>{g.model}</h3>
                <span className="spacer" style={{ flex: 1 }} />
                {g.versions.some((v) => v.stage === "PRODUCTION") && (
                  <>
                    <button className="small" onClick={() => predict(g)}>Predict latest</button>
                    <button className="small" onClick={() => checkDrift(g.model)}>Check drift</button>
                  </>
                )}
                {can("model:rollback") && g.versions.some((v) => v.stage === "PRODUCTION" && v.rollback_target) && (
                  <button
                    className="small"
                    onClick={async () => {
                      await run(() => post(`/models/${g.model}:rollback`), "Rolled back to previous production version");
                      models.reload();
                    }}
                  >
                    Roll back
                  </button>
                )}
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Version</th>
                      <th>Stage</th>
                      <th>Algorithm</th>
                      <th>Instrument</th>
                      <th className="num">Test AUC</th>
                      <th className="num">Test accuracy</th>
                      <th className="num">Net return (test)</th>
                      <th>Warnings</th>
                      <th>Created</th>
                      <th><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.versions.map((v) => {
                      const test = v.report.metrics?.test ?? {};
                      return (
                        <tr key={v.version}>
                          <td>v{v.version}</td>
                          <td><StatusBadge status={v.stage} /></td>
                          <td className="small">{v.algorithm}</td>
                          <td className="small">{v.instrument_id}</td>
                          <td className="num">{ratio(test.auc ?? null, 3)}</td>
                          <td className="num">{pct(test.accuracy ?? null, 1)}</td>
                          <td className="num">{pct(test.strategy_total_return ?? null)}</td>
                          <td className="small">{(v.report.warnings ?? []).join("; ") || "—"}</td>
                          <td className="small">{time(v.created_at)}</td>
                          <td>
                            <span className="row">
                              <button className="small" onClick={() => setCard(v)}>Card</button>
                              {can("model:promote") && NEXT_STAGE[v.stage] && (
                                <button className="small" onClick={() => setPromoting(v)}>
                                  → {NEXT_STAGE[v.stage].toLowerCase()}
                                </button>
                              )}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))
        )}
      </Section>

      {prediction && (
        <Section title={`Prediction · ${prediction.model} v${prediction.version}`} actions={<Badge kind="ai">AI-generated</Badge>}>
          <p style={{ marginTop: 0 }}>
            Score <strong>{prediction.value.toFixed(4)}</strong>{" "}
            <span className="muted small">(probability of an up move for classifiers; expected return for regressors)</span>
          </p>
          <div className="small muted">
            {Object.entries(prediction.features)
              .map(([k, v]) => `${k}=${v.toFixed(4)}`)
              .join(" · ")}
          </div>
          <div className="small muted">Inference {prediction.inference_id} logged.</div>
        </Section>
      )}
      {drift && (
        <Section title={`Feature drift · ${drift.model}`}>
          {Object.keys(drift.psi).length === 0 ? (
            <Empty>Not enough recent inferences to measure drift yet.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Feature</th>
                  <th className="num">PSI</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(drift.psi).map(([k, v]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td className="num">{ratio(v, 3)}</td>
                    <td>{v === null ? "—" : v > 0.25 ? <Badge kind="bad">Drift</Badge> : v > 0.1 ? <Badge kind="warn">Watch</Badge> : <Badge kind="good">Stable</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {can("portfolio:view") && <Optimizer instruments={instruments.data ?? []} />}

      {training && (
        <TrainDialog
          instruments={instruments.data ?? []}
          onClose={() => {
            setTraining(false);
            models.reload();
          }}
        />
      )}
      {promoting && (
        <PromoteDialog
          version={promoting}
          onClose={() => {
            setPromoting(null);
            models.reload();
          }}
        />
      )}
      {card && (
        <Dialog title={`${card.model} v${card.version} model card`} onClose={() => setCard(null)}>
          <pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: "60vh", overflow: "auto" }}>{card.model_card}</pre>
          <div className="row end">
            <button onClick={() => setCard(null)}>Close</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function TrainDialog({ instruments, onClose }: { instruments: Instrument[]; onClose: () => void }) {
  const { run } = useApp();
  const [name, setName] = useState("btc-direction");
  const [instrumentId, setInstrumentId] = useState(instruments[0]?.instrument_id ?? "");
  const [algorithm, setAlgorithm] = useState("logistic_regression");
  const [horizon, setHorizon] = useState("1");
  const [bars, setBars] = useState("2000");
  const [source, setSource] = useState("synthetic");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const out = await run(
      () =>
        post<ModelVersion>("/models:train", {
          name,
          instrument_id: instrumentId,
          algorithm,
          horizon: Number(horizon),
          data: { source, bars: Number(bars) },
        }),
      "Model trained and registered",
    );
    setBusy(false);
    if (out) onClose();
  };
  return (
    <Dialog title="Train model" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            Name
            <input required pattern="[A-Za-z0-9_\-]+" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            Instrument
            <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
              {instruments.map((i) => (
                <option key={i.instrument_id}>{i.instrument_id}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Algorithm
            <select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
              <option value="logistic_regression">Logistic regression (direction)</option>
              <option value="ridge_regression">Ridge regression (return)</option>
            </select>
          </label>
          <label className="field">
            Horizon (bars)
            <input inputMode="numeric" value={horizon} onChange={(e) => setHorizon(e.target.value)} />
          </label>
          <label className="field">
            Training bars
            <input inputMode="numeric" value={bars} onChange={(e) => setBars(e.target.value)} />
          </label>
          <label className="field">
            Data source
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="synthetic">Synthetic (seeded)</option>
              <option value="venue">Venue history (live connection)</option>
            </select>
          </label>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Features: 1 and 5-bar returns, SMA ratio, RSI, volatility and z-score. Training is deterministic for the same data.
        </p>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Training…" : "Train"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function PromoteDialog({ version, onClose }: { version: ModelVersion; onClose: () => void }) {
  const { run } = useApp();
  const stage = NEXT_STAGE[version.stage];
  const [reason, setReason] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(
      () => post(`/models/${version.model}/versions/${version.version}:promote`, { stage, reason }),
      `Promoted to ${stage}`,
    );
    if (out) onClose();
  };
  return (
    <Dialog title={`Promote ${version.model} v${version.version} to ${stage}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {stage === "PRODUCTION" && (
          <div className="alert warn">
            Production models feed live strategy decisions. Promotion requires an evaluation report and a minimum AUC; in
            multi-user setups a different person than the trainer must approve.
          </div>
        )}
        <label className="field">
          Reason
          <input required={stage === "PRODUCTION"} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Promote</button>
        </div>
      </form>
    </Dialog>
  );
}

function Optimizer({ instruments }: { instruments: Instrument[] }) {
  const { run } = useApp();
  const [selected, setSelected] = useState<string[]>([]);
  const [method, setMethod] = useState("RISK_PARITY");
  const [maxWeight, setMaxWeight] = useState("1");
  const [result, setResult] = useState<Optimization | null>(null);
  useEffect(() => {
    if (!selected.length && instruments.length >= 2) setSelected(instruments.slice(0, 3).map((i) => i.instrument_id));
  }, [instruments, selected.length]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(() =>
      post<Optimization>("/portfolio:optimize", { instruments: selected, method, max_weight: Number(maxWeight) }),
    );
    if (out) setResult(out);
  };
  return (
    <Section title="Portfolio optimizer">
      <form className="stack" onSubmit={submit}>
        <fieldset className="row" style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="small muted">Instruments</legend>
          {instruments.map((i) => (
            <label key={i.instrument_id} className="field inline small">
              <input
                type="checkbox"
                checked={selected.includes(i.instrument_id)}
                onChange={(e) =>
                  setSelected(e.target.checked ? [...selected, i.instrument_id] : selected.filter((x) => x !== i.instrument_id))
                }
              />{" "}
              {i.instrument_id}
            </label>
          ))}
        </fieldset>
        <div className="form-grid">
          <label className="field">
            Method
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="EQUAL_WEIGHT">Equal weight</option>
              <option value="INVERSE_VOLATILITY">Inverse volatility</option>
              <option value="MIN_VARIANCE">Minimum variance</option>
              <option value="MAX_SHARPE">Maximum Sharpe</option>
              <option value="RISK_PARITY">Risk parity</option>
            </select>
          </label>
          <label className="field">
            Max weight per asset
            <input inputMode="decimal" value={maxWeight} onChange={(e) => setMaxWeight(e.target.value)} />
          </label>
        </div>
        <div className="row end">
          <button className="primary" type="submit" disabled={selected.length < 2}>
            Optimize
          </button>
        </div>
      </form>
      {result && (
        <div className="stack" style={{ marginTop: 12 }}>
          <div className="small">
            Expected return {pct(result.expected_return)} · volatility {pct(result.expected_volatility)} · Sharpe{" "}
            {ratio(result.sharpe)} <span className="muted">(annualized, from synthetic history)</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Instrument</th>
                <th className="num">Weight</th>
                <th className="num">Risk contribution</th>
                <th style={{ width: "40%" }}><span className="sr-only">Weight bar</span></th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(result.weights).map(([k, w]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="num">{pct(w, 1)}</td>
                  <td className="num">{pct(result.risk_contributions[k] ?? null, 1)}</td>
                  <td>
                    <div style={{ height: 8, borderRadius: 4, background: "var(--accent)", width: `${Math.max(0, w) * 100}%` }} />
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
