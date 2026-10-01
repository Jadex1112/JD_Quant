import { lazy, Suspense, useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { get, post, type Deployment, type Instrument, type LabRun, type RuleSpec, type Translation } from "../api";
import { useApp } from "../app-state";
import { Badge, Dialog, Empty, Section, useData } from "../components/ui";
import { num, pct, ratio, signed, time, tone } from "../format";

import { RuleBuilder, emptyBuilder, fromSpec, toSpec, type BuilderState } from "./RuleBuilder";

const LineChart = lazy(() => import("../components/LineChart").then((m) => ({ default: m.LineChart })));

const EXAMPLES = [
  "Buy gold when the 20 EMA crosses above the 50 EMA and RSI(14) is above 50. Sell when the 20 EMA crosses back below. 1.5% stop-loss.",
  "London breakout on EUR/USD: between 07:00 and 11:00 London, buy when price closes above the highest high of the last 7 hours and sell short below the lowest low. 0.3% stop, 0.6% target, flat by the end of the day.",
  "Buy when RSI(2) drops below 10 while price is above the 200-period SMA; sell when RSI(2) rises above 70.",
];

const BARS: [number, string][] = [
  [900, "15 minutes"],
  [3600, "1 hour"],
  [14400, "4 hours"],
  [86400, "1 day"],
];

const GRADE: Record<string, string> = { High: "good", Medium: "warn", Low: "bad" };

export function LabPage() {
  const { run, notify } = useApp();
  const [mode, setMode] = useState<"words" | "visual">("words");
  const [builder, setBuilder] = useState<BuilderState>(emptyBuilder);
  const models = useData(
    () =>
      get<{ provider: string | null; default: string | null; presets: { id: string; label: string }[]; available: string[]; switchable: boolean; error?: string | null }>(
        "/strategy-lab/models",
      ),
    [],
  );
  const [model, setModel] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [view, setView] = useState<"rules" | "code">("rules");
  const [code, setCode] = useState("");
  const instruments = useData(() => get<Instrument[]>("/instruments"), []);
  const history = useData(() => get<LabRun[]>("/strategy-lab/runs"), []);
  const [text, setText] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const [interval, setIntervalSeconds] = useState(3600);
  const [bars, setBars] = useState("2000");
  const [capital, setCapital] = useState("100000");
  const [leverage, setLeverage] = useState("1");
  const [translation, setTranslation] = useState<Translation | null>(null);
  const [specText, setSpecText] = useState("");
  const [busy, setBusy] = useState<"" | "translate" | "backtest">("");
  const [result, setResult] = useState<LabRun | null>(null);

  useEffect(() => {
    const all = instruments.data ?? [];
    if (!instrumentId && all.length) setInstrumentId((all.find((i) => i.instrument_id === "OANDA:XAU_USD") ?? all[0]).instrument_id);
  }, [instruments.data, instrumentId]);

  const translate = async () => {
    setBusy("translate");
    const chosen = model === "custom" ? customModel.trim() : model;
    const out = await run(() =>
      post<Translation>("/strategy-lab/translate", { text, instrument_id: instrumentId, interval_seconds: interval, model: chosen || null }),
    );
    if (out) {
      setTranslation(out);
      setCode(out.code ?? "");
      setSpecText(JSON.stringify(out.spec, null, 2));
      setResult(null);
    }
    setBusy("");
  };

  const backtest = async () => {
    let spec: RuleSpec;
    try {
      spec = JSON.parse(specText);
    } catch {
      await run(async () => {
        throw new Error("The rules are not valid JSON.");
      });
      return;
    }
    setBusy("backtest");
    const out = await run(() =>
      post<LabRun>("/strategy-lab/backtests", {
        spec,
        instrument_id: instrumentId,
        interval_seconds: interval,
        bars: Number(bars),
        capital,
        leverage,
        text,
        assumptions: translation?.assumptions ?? [],
        unsupported: translation?.unsupported ?? [],
      }),
    );
    if (out) {
      setResult(out);
      history.reload();
    }
    setBusy("");
  };

  const checkBuilt = async () => {
    const out = await run(() => post<{ spec: RuleSpec; description: string[]; code: string }>("/strategy-lab/check", { spec: toSpec(builder) }));
    if (out) {
      setCode(out.code);
      setTranslation({ spec: out.spec, description: out.description, assumptions: [], unsupported: [], model: "visual builder" });
      setSpecText(JSON.stringify(out.spec, null, 2));
      setText("");
      setResult(null);
    }
  };

  const showCode = async () => {
    setView("code");
    try {
      const spec = JSON.parse(specText);
      const out = await run(() => post<{ code: string }>("/strategy-lab/code", { spec }));
      if (out) setCode(out.code);
    } catch {
      notify("The rules are not valid JSON.", true);
    }
  };

  const downloadCode = () => {
    const blob = new Blob([code], { type: "text/x-python" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(translation?.spec.name ? String(translation.spec.name) : "strategy").replace(/[^A-Za-z0-9]+/g, "_").toLowerCase()}.py`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const editVisually = () => {
    let loaded: BuilderState | null = null;
    try {
      loaded = fromSpec(JSON.parse(specText));
    } catch {
      loaded = null;
    }
    if (!loaded) {
      notify("These rules use nested groups the visual builder cannot show; edit them as JSON.", true);
      return;
    }
    setBuilder(loaded);
    setMode("visual");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const open = async (runId: string) => {
    const out = await run(() => get<LabRun>(`/strategy-lab/runs/${runId}`));
    if (out) {
      setResult(out);
      setText(out.text);
      setInstrumentId(out.instrument_id);
      setIntervalSeconds(out.interval_seconds);
      setSpecText(JSON.stringify(out.spec, null, 2));
      setTranslation({ spec: out.spec, description: out.description, assumptions: out.assumptions, unsupported: out.unsupported, model: out.model });
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>
        Strategy lab <Badge kind="ai">AI</Badge>
      </h1>
      <Section
        title={mode === "words" ? "1 · Describe your strategy" : "1 · Build your strategy"}
        actions={
          <div className="segmented" role="group" aria-label="How to write the strategy">
            <button className="small" aria-pressed={mode === "words"} onClick={() => setMode("words")}>In words (AI)</button>
            <button className="small" aria-pressed={mode === "visual"} onClick={() => setMode("visual")}>Visual builder</button>
          </div>
        }
      >
        <div className="stack">
          {mode === "visual" && <RuleBuilder state={builder} onChange={setBuilder} />}
          {mode === "words" && (
          <>
          <label className="field">
            <span className="sr-only">Strategy description</span>
            <textarea
              rows={4}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="In your own words: when to buy, when to sell, stops, times of day…"
            />
          </label>
          <div className="chips small">
            <span className="muted">Examples:</span>
            {EXAMPLES.map((e, i) => (
              <button key={i} type="button" className="link small" onClick={() => setText(e)}>
                {e.slice(0, 48)}…
              </button>
            ))}
          </div>
          </>
          )}
          <div className="form-grid">
            <label className="field">
              Market
              <select value={instrumentId} onChange={(e) => setInstrumentId(e.target.value)}>
                {(instruments.data ?? []).map((i) => (
                  <option key={i.instrument_id}>{i.instrument_id}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Bar size
              <select value={interval} onChange={(e) => setIntervalSeconds(Number(e.target.value))}>
                {BARS.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
            <label className="field">
              History (bars)
              <input inputMode="numeric" value={bars} onChange={(e) => setBars(e.target.value)} />
            </label>
            <label className="field">
              Capital (₹)
              <input inputMode="decimal" value={capital} onChange={(e) => setCapital(e.target.value)} />
            </label>
            <label className="field" title="Capped by the market: forex 30×, metals 20×, futures 10×, NSE intraday 5×, others 1×">
              Leverage (1 = none)
              <input inputMode="decimal" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
            </label>
          </div>
          <div className="row end">
            {mode === "words" && models.data?.switchable && (
              <>
                <label className="field inline small">
                  AI model
                  <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="AI model">
                    <option value="">Default ({models.data.default})</option>
                    {models.data.presets
                      .filter((p) => p.id !== models.data!.default)
                      .map((p) => (
                        <option key={p.id} value={p.id}>{p.label}</option>
                      ))}
                    <option value="custom">Other model id…</option>
                  </select>
                </label>
                {model === "custom" && (
                  <>
                    <input
                      aria-label="Model id"
                      list="nvidia-models"
                      placeholder="e.g. moonshotai/kimi-k3"
                      value={customModel}
                      onChange={(e) => setCustomModel(e.target.value)}
                      style={{ minWidth: 260 }}
                    />
                    <datalist id="nvidia-models">
                      {models.data.available.map((m) => <option key={m} value={m} />)}
                    </datalist>
                  </>
                )}
              </>
            )}
            {mode === "words" && models.data && !models.data.provider && (
              <span className="small muted">No AI configured: set NVIDIA_API_KEY on the server (or use the visual builder).</span>
            )}
            {mode === "words" ? (
              <button className="primary" onClick={translate} disabled={!text.trim() || !instrumentId || busy !== "" || (model === "custom" && !customModel.trim())}>
                {busy === "translate" ? "The AI is writing the rules…" : "Turn into rules with AI"}
              </button>
            ) : (
              <button className="primary" onClick={checkBuilt} disabled={!instrumentId || busy !== ""}>
                Check the rules
              </button>
            )}
          </div>
        </div>
      </Section>

      {(translation || specText) && (
        <Section title="2 · Check the rules" actions={translation?.model ? <span className="small muted">{translation.model}</span> : null}>
          <div className="stack">
            {translation && (
              <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {translation.description.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ol>
            )}
            {translation && translation.assumptions.length > 0 && (
              <div className="small">
                <strong>The AI assumed:</strong>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                  {translation.assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>
            )}
            {translation && translation.unsupported.length > 0 && (
              <div className="alert warn small">
                <strong>Not expressible as rules, so not tested:</strong> {translation.unsupported.join("; ")}
              </div>
            )}
            <div className="segmented" role="group" aria-label="Show the strategy as" style={{ alignSelf: "flex-start" }}>
              <button className="small" aria-pressed={view === "rules"} onClick={() => setView("rules")}>Rules (JSON)</button>
              <button className="small" aria-pressed={view === "code"} onClick={showCode}>As Python code</button>
            </div>
            {view === "rules" ? (
              <textarea
                rows={14}
                value={specText}
                onChange={(e) => setSpecText(e.target.value)}
                spellCheck={false}
                style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                aria-label="Rules as JSON"
              />
            ) : (
              <div className="stack" style={{ gap: 6 }}>
                <pre className="wrap card" style={{ boxShadow: "none", fontFamily: "monospace", maxHeight: 420, overflow: "auto" }} aria-label="Rules as Python code">
                  {code || "Generating…"}
                </pre>
                <div className="row">
                  <button className="small" onClick={downloadCode} disabled={!code}>Download .py</button>
                  <span className="small muted">
                    Generated from the rules without AI. The platform runs the rules themselves, which are checked before
                    anything runs; it never executes code an AI wrote.
                  </span>
                </div>
              </div>
            )}
            <div className="row end">
              <button onClick={editVisually} disabled={!specText}>Edit visually</button>
              <button className="primary" onClick={backtest} disabled={busy !== "" || !specText}>
                {busy === "backtest" ? "Backtesting…" : "3 · Backtest"}
              </button>
            </div>
          </div>
        </Section>
      )}

      {result && <LabResult result={result} onPaper={() => history.reload()} />}

      <Section title="Your tests">
        {(history.data ?? []).length === 0 ? (
          <Empty>Strategies you test appear here.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Market</th>
                  <th className="num">Return</th>
                  <th className="num">Trades</th>
                  <th>Confidence</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {(history.data ?? []).map((r) => (
                  <tr key={r.run_id}>
                    <td>
                      <button className="link" onClick={() => open(r.run_id)}>{String(r.spec.name ?? "Typed strategy")}</button>
                      {r.deployment_id && <Badge kind="paper">paper trading</Badge>}
                    </td>
                    <td className="small">{r.instrument_id}</td>
                    <td className={`num ${tone(r.results.return)}`}>{pct(r.results.return)}</td>
                    <td className="num">{r.results.trades}</td>
                    <td>
                      <Badge kind={GRADE[r.confidence.grade]}>{r.confidence.grade}</Badge> <span className="small muted">{r.confidence.score}</span>
                    </td>
                    <td className="small">{time(r.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

function LabResult({ result, onPaper }: { result: LabRun; onPaper: () => void }) {
  const { can, run } = useApp();
  const navigate = useNavigate();
  const [paper, setPaper] = useState(false);
  const r = result.results;
  const c = result.confidence;
  return (
    <>
      {result.data_source !== "broker history" && (
        <div className="alert warn">
          Tested on <strong>{result.data_source}</strong>: connect this market&apos;s broker under <NavLink to="/connections">Connections</NavLink> to
          test on real prices. Synthetic results say nothing about the real market, so confidence is capped at Low.
        </div>
      )}
      <div className="grid three">
        <div className="card kpi">
          <span className="label">Confidence the edge is real</span>
          <span className="value">
            {c.score}% <Badge kind={GRADE[c.grade]}>{c.grade}</Badge>
          </span>
          <span className="small muted">
            after allowing for {c.trials} variation{c.trials === 1 ? "" : "s"} you tested on this market
          </span>
        </div>
        <div className="card kpi">
          <span className="label">Return after charges</span>
          <span className={`value ${tone(r.return)}`}>{pct(r.return)}</span>
          <span className="small muted">buy and hold: {pct(r.benchmark_return)}</span>
        </div>
        <div className="card kpi">
          <span className="label">Chance of profit (resampled trades)</span>
          <span className="value">{c.probability_of_profit === null ? "—" : pct(c.probability_of_profit, 0)}</span>
          <span className="small muted">{r.trades} trades · win rate {pct(r.win_rate, 0)}</span>
        </div>
      </div>
      <Section
        title="Results"
        actions={
          <>
            {can("deployment:create") && (
              <button
                onClick={async () => {
                  const created = await run(
                    () => post<{ strategy_id: string }>("/strategies/from-lab", { run_id: result.run_id }),
                    "Added to the strategy pipeline",
                  );
                  if (created) navigate(`/pipeline?strategy=${created.strategy_id}`);
                }}
                title="Version it and take it through backtest, walk-forward validation, paper trading and approval before live"
              >
                Add to pipeline
              </button>
            )}
            <button className="primary" onClick={() => setPaper(true)} disabled={!!result.deployment_id}>
              {result.deployment_id ? "Paper trading" : "Paper trade this…"}
            </button>
          </>
        }
      >
        <div className="grid two" style={{ alignItems: "start" }}>
          <dl className="kv small">
            <dt>Sharpe ratio</dt>
            <dd>{ratio(r.sharpe)}</dd>
            <dt>Max drawdown</dt>
            <dd>{pct(r.max_drawdown)}</dd>
            <dt>Profit factor</dt>
            <dd>{ratio(r.profit_factor)}</dd>
            <dt>Charges</dt>
            <dd>
              {num(r.charges, 2)} {result.currency}
              {r.charges_share !== null && ` · ${pct(r.charges_share, 0)} of gross profit`}
            </dd>
            <dt>Leverage</dt>
            <dd>
              {r.leverage_used}× (market allows up to {r.leverage_cap}×)
            </dd>
            <dt>Periods</dt>
            <dd>{r.periods.map((p, i) => <span key={i} className={tone(p)} style={{ marginRight: 8 }}>{signed(p * 100)}%</span>)}</dd>
            <dt>Data</dt>
            <dd>
              {result.data_source}, {r.bars} bars of {result.interval_seconds >= 86400 ? "1 day" : `${result.interval_seconds / 60} min`}
            </dd>
          </dl>
          <ul className="small" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
            {c.checks.map((x) => (
              <li key={x.name} style={{ marginBottom: 4 }}>
                <span className={x.ok ? "pos" : "neg"}>{x.ok ? "✓" : "✗"}</span> <strong>{x.name}</strong> — <span className="muted">{x.detail}</span>
              </li>
            ))}
          </ul>
        </div>
        {result.equity && result.equity.length > 1 && (
          <div style={{ marginTop: 12 }}>
            <Suspense fallback={<div className="muted small">Loading chart…</div>}>
              <LineChart points={result.equity.map(([t, v]) => ({ time: t, value: v }))} label="Growth of 1 after charges" />
            </Suspense>
          </div>
        )}
      </Section>
      {result.review && (
        <Section title="AI review" actions={<span className="small muted">{result.model}</span>}>
          <p style={{ marginTop: 0 }}>{result.review.summary}</p>
          <div className="grid three small">
            <ReviewList title="Strengths" items={result.review.strengths} />
            <ReviewList title="Weaknesses" items={result.review.weaknesses} />
            <ReviewList title="Ideas to test next" items={result.review.suggestions} />
          </div>
          <p className="small muted" style={{ marginBottom: 0 }}>
            Each extra variation you test lowers the confidence of the best one: that is how the score stays honest.
          </p>
        </Section>
      )}
      {result.trades && result.trades.length > 0 && (
        <Section title={`Trades (${r.trades})`}>
          <div className="table-wrap" style={{ maxHeight: 320 }}>
            <table>
              <thead>
                <tr>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry price</th>
                  <th className="num">Exit price</th>
                  <th className="num">Net P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {[...result.trades].reverse().map((t, i) => (
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
        </Section>
      )}
      {paper && (
        <LabPaperDialog
          result={result}
          onClose={() => {
            setPaper(false);
            onPaper();
          }}
        />
      )}
    </>
  );
}

function ReviewList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <strong>{title}</strong>
      <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
        {items.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ul>
    </div>
  );
}

function LabPaperDialog({ result, onClose }: { result: LabRun; onClose: () => void }) {
  const { accounts, run } = useApp();
  const paper = accounts.filter((a) => a.mode === "PAPER");
  const [accountId, setAccountId] = useState(paper[0]?.account_id ?? "paper-main");
  const [done, setDone] = useState<Deployment | null>(null);
  const start = async () => {
    const out = await run(() => post<Deployment>(`/strategy-lab/runs/${result.run_id}:paper-trade`, { account_id: accountId }), "Paper trading started");
    if (out) {
      result.deployment_id = out.deployment_id;
      setDone(out);
    }
  };
  return (
    <Dialog title="Paper trade this strategy" onClose={onClose}>
      {done ? (
        <div className="stack">
          <p style={{ margin: 0 }}>
            The rules are running on <strong>{done.account_id}</strong> with live prices. Follow them under{" "}
            <NavLink to="/strategies" onClick={onClose}>Strategies</NavLink>.
          </p>
          <div className="row end">
            <button onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <div className="stack">
          <p className="small muted" style={{ margin: 0 }}>
            The same rules on {result.instrument_id}, {result.interval_seconds >= 86400 ? "daily" : `${result.interval_seconds / 60}-minute`} bars, with
            ₹{num(result.capital, 0)} at {result.leverage}× leverage — on paper, no real money.
          </p>
          <label className="field">
            Paper account
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              {paper.map((a) => (
                <option key={a.account_id} value={a.account_id}>{a.name} ({a.account_id})</option>
              ))}
            </select>
          </label>
          <div className="row end">
            <button onClick={onClose}>Cancel</button>
            <button className="primary" onClick={start}>Start paper trading</button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
