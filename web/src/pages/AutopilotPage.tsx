import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  get,
  post,
  put,
  type AutopilotConfig,
  type AutopilotDecision,
  type AutopilotStatus,
  type AnalystConcern,
  type ArmedAccount,
  type EvaluationSummary,
  type Protection,
  type SelectedStrategy,
  type UniverseOption,
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
  ROLL: "",
  HALT: "bad",
  ERROR: "bad",
};

const SEVERITY: Record<string, string> = { high: "bad", medium: "warn", low: "" };

/** A basket strategy trades several instruments; show them compactly. */
function instrumentsOf(e: { instrument_id: string; instruments?: string[] }): string {
  const list = e.instruments ?? [e.instrument_id];
  return list.length > 1 ? `Basket: ${list.map((i) => i.split(":").pop()).join(", ")}` : e.instrument_id;
}

export function AutopilotPage() {
  const { can, run, notify } = useApp();
  const status = useData(() => get<AutopilotStatus>("/autopilot"), [], 3000);
  const decisions = useData(() => get<AutopilotDecision[]>("/autopilot/decisions?limit=60"), [], 5000);
  const [editing, setEditing] = useState(false);
  const [arming, setArming] = useState(false);
  const [disarming, setDisarming] = useState<string | null | undefined>(undefined); // account, null = all
  const [resetting, setResetting] = useState<"PAPER" | "LIVE" | null>(null);
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
  const armed = Object.values(s.live.accounts);
  const totalCap = armed.reduce((sum, a) => sum + Number(a.capital_cap), 0);
  const halted = Object.entries(s.halted_until);

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>
          AI Autopilot <Badge kind="ai">AI</Badge>
        </h1>
        <div className="row">
          {s.config.enabled ? <Badge kind="good">Scheduled</Badge> : <Badge>Manual</Badge>}
          {armed.length ? (
            <Badge kind="live">
              ● Live armed · {armed.length} account{armed.length > 1 ? "s" : ""} · cap ₹{num(totalCap, 0)}
            </Badge>
          ) : (
            <Badge kind="paper">◌ Paper only</Badge>
          )}
        </div>
      </div>

      <HowItWorks />

      {halted.length > 0 && (
        <div className="alert error">
          Book drawdown limit hit: new positions are paused until{" "}
          {halted.map(([mode, until]) => `${mode} ${time(until)}`).join(", ")}. Existing positions were cut to cash.
        </div>
      )}

      <ProtectionPanel
        protection={s.protection}
        showLive={armed.length > 0}
        canReset={can("autopilot:arm")}
        onReset={setResetting}
        config={s.config}
      />

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
              Choose what the autopilot may trade (stocks, gold, crypto, commodities, currency) under <button className="link" onClick={() => setEditing(true)}>Settings</button>.
            </div>
          ) : null}
          <dl className="kv small">
            <dt>Universe</dt>
            <dd>{s.config.universe.length ? s.config.universe.join(", ") : "—"}</dd>
            <dt>Budget</dt>
            <dd>
              ₹{num(s.config.capital, 0)} across up to {s.config.max_positions} positions (max {pct(s.config.max_weight, 0)} each),{" "}
              {s.config.product === "CNC" ? "positional" : "intraday"}, each sized to {pct(Number(s.config.vol_target), 0)} yearly volatility
            </dd>
            <dt>Bars</dt>
            <dd>{INTERVALS.find(([v]) => v === s.config.interval_seconds)?.[1] ?? `${s.config.interval_seconds}s`}</dd>
            <dt>AI analyst</dt>
            <dd>
              {!s.config.use_analyst
                ? "off"
                : !s.analyst_available
                  ? "not configured — set NVIDIA_API_KEY (or an Anthropic key) on the server"
                  : s.config.analyst_can_veto
                    ? "reviews every cycle and may veto risky picks"
                    : "reviews every cycle (advisory)"}
            </dd>
            <dt>Last run</dt>
            <dd>{time(s.last_run_at)}</dd>
            <dt>Next run</dt>
            <dd>{s.config.enabled ? time(s.next_run_at) : "only when you press Run"}</dd>
          </dl>
          {can("autopilot:configure") && (
            <label className="field inline" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={s.config.enabled} onChange={(e) => toggle(e.target.checked)} /> Research and rebalance automatically{" "}
              {s.config.interval_seconds >= 86400 ? "after each daily close" : `every ${s.config.cycle_hours} h`}
            </label>
          )}
        </Section>

        <Section
          title="Live trading"
          actions={
            <>
              {can("autopilot:arm") && (
                <button className={armed.length ? "" : "primary"} onClick={() => setArming(true)}>
                  {armed.length ? "Arm another account…" : "Arm live trading…"}
                </button>
              )}
              {can("autopilot:disarm") && armed.length > 1 && (
                <button className="danger" onClick={() => setDisarming(null)}>Disarm all</button>
              )}
            </>
          }
        >
          <p style={{ marginTop: 0 }}>
            Everything starts on the <strong>{s.paper_account}</strong> paper account. A strategy becomes eligible for real orders after{" "}
            {s.config.min_paper_days} days and {s.config.min_paper_trades} trades on paper without breaching its limits, and then only on an
            account you armed for that market.
          </p>
          {armed.length === 0 ? (
            <Empty>No broker account is armed. The autopilot trades on paper only.</Empty>
          ) : (
            <ArmedList accounts={armed} canDisarm={can("autopilot:disarm")} onDisarm={setDisarming} />
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
                  <th>Instrument</th>
                  <th>Strategy</th>
                  <th>Mode</th>
                  <th className="num">Capital</th>
                  <th className="num">P&amp;L (₹)</th>
                  <th className="num">Expected Sharpe</th>
                  <th>Since</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {active.map((m) => (
                  <tr key={m.deployment_id}>
                    <td>
                      {instrumentsOf(m)}
                      {m.universe_id !== m.instrument_id && m.instruments.length === 1 && (
                        <div className="small muted">rolling {m.universe_id}</div>
                      )}
                    </td>
                    <td className="small">{m.label}</td>
                    <td>
                      <ModeBadge mode={m.mode} />
                      {m.mode === "LIVE" && <div className="small muted">{m.account_id}</div>}
                    </td>
                    <td className="num">
                      {num(m.capital, 0)} {m.currency !== "INR" && <span className="small muted">{m.currency}</span>}
                    </td>
                    <td className={`num ${tone(m.pnl)}`}>{signed(m.pnl)}</td>
                    <td className="num">{ratio(m.expected.sharpe ?? null)}</td>
                    <td className="small">{time(m.created_at)}</td>
                    <td className="small">
                      {m.status === "CLOSING" ? "closing out" : m.rolled_to ? `rolled to ${m.rolled_to}` : m.live_deployment ? "live copy running" : "active"}
                    </td>
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
      {disarming !== undefined && (
        <Confirm
          title={disarming ? `Disarm ${disarming}?` : "Disarm every live account?"}
          danger
          confirmLabel="Disarm and close"
          body={<p>Live autopilot deployments on {disarming ?? "every account"} close their positions at market and stop. Paper trading continues.</p>}
          onCancel={() => setDisarming(undefined)}
          onConfirm={async () => {
            const account = disarming;
            setDisarming(undefined);
            await run(() => post("/autopilot/live:disarm", { account_id: account, flatten: true }), "Live trading disarmed");
            refresh();
          }}
        />
      )}
      {resetting && (
        <Confirm
          title={`Resume ${resetting === "LIVE" ? "live" : "paper"} trading?`}
          danger={resetting === "LIVE"}
          confirmLabel="Reset protection"
          body={
            <p>
              The loss floor stopped the autopilot. Resetting starts a new floor {pct(s.config.loss_floor, 0)} below the current equity and
              lets the autopilot open positions again at the next cycle. Consider why the floor was hit before you continue.
            </p>
          }
          onCancel={() => setResetting(null)}
          onConfirm={async () => {
            const mode = resetting;
            setResetting(null);
            await run(() => post("/autopilot/protection:reset", { mode }), "Protection reset");
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
        <strong>How the autopilot decides</strong> <span className="small muted">— research, test, protect, trade, watch</span>
      </summary>
      <ol className="small" style={{ marginBottom: 0 }}>
        <li>
          <strong>Research like a systematic fund.</strong> For every instrument (NSE stocks and ETFs, gold, crypto, MCX commodities and
          currency futures) it tries around 30 strategies: multi-speed trend following as CTAs run it, moving averages, MACD, Supertrend,
          momentum, mean reversion, breakouts, machine-learning models — plus cross-sectional momentum and short-term reversal across
          baskets. Futures may also go short.
        </li>
        <li>
          <strong>Walk-forward backtest with every charge.</strong> Each strategy trades history it was never tuned on, with brokerage,
          STT/CTT, exchange fees, stamp duty, GST, SEBI fees (or the crypto exchange fee) and slippage. Strategies whose charges eat more
          than half their gross profit are rejected.
        </li>
        <li>
          <strong>Reject luck.</strong> Trying many strategies guarantees some look good by chance, so a result must beat what the best of
          that many skill-less strategies would reach, and also make money in a final untouched period.
        </li>
        <li>
          <strong>AI review.</strong> An LLM analyst (NVIDIA Nemotron or Claude) reads the results and writes a briefing with concerns.
          If you allow it, a high-severity concern vetoes that strategy — it can only remove risk, never add trades.
        </li>
        <li>
          <strong>Protect capital.</strong> Positions are sized to a volatility target. The book has a loss floor that rises to lock in
          gains, a daily loss limit, and a drawdown halt. At the floor it goes to cash until you reset it.
        </li>
        <li>
          <strong>Trade and watch.</strong> Winners get capital on paper. Strategies that stop working are closed out; futures roll
          before expiry. Proven ones move to real orders only on accounts you arm, within your cap. No system can guarantee profits.
        </li>
      </ol>
    </details>
  );
}

function ProtectionPanel({
  protection,
  showLive,
  canReset,
  onReset,
  config,
}: {
  protection: AutopilotStatus["protection"];
  showLive: boolean;
  canReset: boolean;
  onReset: (mode: "PAPER" | "LIVE") => void;
  config: AutopilotStatus["config"];
}) {
  const modes: ("PAPER" | "LIVE")[] = showLive ? ["PAPER", "LIVE"] : ["PAPER"];
  return (
    <Section
      title="Capital protection"
      actions={
        <span className="small muted">
          floor {pct(config.loss_floor, 0)} below budget · locks in {pct(config.lock_in_gains, 0)} of gains · daily limit{" "}
          {pct(config.daily_loss_limit, 0)}
        </span>
      }
    >
      <div className={`grid ${modes.length > 1 ? "two" : ""}`}>
        {modes.map((mode) => (
          <ProtectionCard key={mode} mode={mode} p={protection[mode]} canReset={canReset} onReset={() => onReset(mode)} />
        ))}
      </div>
    </Section>
  );
}

function ProtectionCard({ mode, p, canReset, onReset }: { mode: string; p: Protection; canReset: boolean; onReset: () => void }) {
  const exposure = Number(p.exposure);
  const gain = Number(p.equity) - Number(p.budget);
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <ModeBadge mode={mode} />
        {p.floor_hit ? (
          <Badge kind="bad">Floor hit · in cash</Badge>
        ) : exposure < 1 ? (
          <Badge kind="warn">Reduced sizes · {pct(exposure, 0)}</Badge>
        ) : (
          <Badge kind="good">Full sizes</Badge>
        )}
      </div>
      <dl className="kv small">
        <dt>Budget</dt>
        <dd>₹{num(p.budget, 0)}</dd>
        <dt>Equity</dt>
        <dd>
          ₹{num(p.equity, 0)} <span className={tone(gain)}>({signed(gain)})</span>
        </dd>
        <dt>Loss floor</dt>
        <dd>₹{num(p.floor, 0)}{Number(p.floor) > Number(p.budget) && <span className="small pos"> · gains locked</span>}</dd>
        <dt>Cushion</dt>
        <dd>₹{num(p.cushion, 0)}</dd>
      </dl>
      <label className="small muted">
        Position sizes allowed
        <progress max={1} value={exposure} style={{ width: "100%" }} aria-label={`${mode} exposure ${pct(exposure, 0)}`} />
      </label>
      {p.floor_hit && (
        <div className="alert error small">
          The book reached its loss floor, so every position was closed and nothing new opens.{" "}
          {canReset ? (
            <button className="link" onClick={onReset}>Reset protection…</button>
          ) : (
            "An owner can reset protection."
          )}
        </div>
      )}
    </div>
  );
}

function ArmedList({
  accounts,
  canDisarm,
  onDisarm,
}: {
  accounts: ArmedAccount[];
  canDisarm: boolean;
  onDisarm: (account: string) => void;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Account</th>
            <th className="num">Cap (₹)</th>
            <th>Futures</th>
            <th>Armed</th>
            {canDisarm && <th><span className="sr-only">Actions</span></th>}
          </tr>
        </thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.account_id}>
              <td>{a.account_id}</td>
              <td className="num">{num(a.capital_cap, 0)}</td>
              <td className="small">{a.allow_futures ? <Badge kind="warn">allowed</Badge> : "cash only"}</td>
              <td className="small">{a.armed_by} · {time(a.armed_at)}</td>
              {canDisarm && (
                <td>
                  <button className="small danger" onClick={() => onDisarm(a.account_id)}>Disarm</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Concerns({ concerns }: { concerns: AnalystConcern[] }) {
  if (!concerns.length) return <p className="small muted" style={{ marginBottom: 0 }}>No concerns raised.</p>;
  return (
    <ul className="small" style={{ marginBottom: 0, paddingLeft: 18 }}>
      {concerns.map((c, i) => (
        <li key={i} style={{ marginBottom: 4 }}>
          <Badge kind={SEVERITY[c.severity] ?? ""}>{c.severity}</Badge> {c.instrument_id && <strong>{c.instrument_id}: </strong>}
          {c.concern}
        </li>
      ))}
    </ul>
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
          The last cycle used <strong>synthetic demo data</strong> because no broker history was available. Connect Fyers or Binance under{" "}
          <NavLink to="/connections">Connections</NavLink> so research runs on real prices.
        </div>
      )}
      {(run.summary || run.analyst) && (
        <Section
          title="Analyst briefing"
          actions={
            <>
              <Badge kind="ai">AI-generated</Badge>
              {run.analyst && <span className="small muted">{run.analyst}</span>}
            </>
          }
        >
          {run.summary && <p style={{ marginTop: 0, whiteSpace: "pre-wrap" }}>{run.summary}</p>}
          <Concerns concerns={run.concerns ?? []} />
          <p className="small muted" style={{ marginBottom: 0 }}>
            The analyst reviews what the backtests selected; it never picks trades.
          </p>
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
                  <th>Instrument</th>
                  <th>Strategy</th>
                  <th className="num">Capital</th>
                  <th className="num">Return</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Max drawdown</th>
                  <th className="num">Charges</th>
                  <th className="num">Holdout</th>
                  <th className="num">Luck-adjusted confidence</th>
                  <th><span className="sr-only">Chart</span></th>
                </tr>
              </thead>
              <tbody>
                {run.selected.map((e) => (
                  <tr key={`${e.instrument_id}-${e.candidate}`} aria-selected={chartFor === e}>
                    <td>{instrumentsOf(e)}</td>
                    <td className="small">{e.label}</td>
                    <td className="num">{num(e.capital, 0)}</td>
                    <td className={`num ${tone(e.validation.return)}`}>{pct(e.validation.return)}</td>
                    <td className="num">{ratio(e.validation.sharpe)}</td>
                    <td className="num">{pct(e.validation.max_drawdown)}</td>
                    <td className="num"><ChargeShare stats={e.validation} /></td>
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
                {instrumentsOf(chartFor)} · {chartFor.label}: growth of 1 over the out-of-sample periods (last period = holdout)
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

/** Charges as a share of gross profit (or of capital when there was no gross profit). */
function ChargeShare({ stats }: { stats: EvaluationSummary["validation"] }) {
  if (stats.charges === undefined) return <>—</>;
  if (stats.charges_share === null || stats.charges_share === undefined) {
    return <span className="small muted" title={`₹${num(stats.charges, 0)} in charges; no gross profit`}>₹{num(stats.charges, 0)}</span>;
  }
  return (
    <span className={stats.charges_share > 0.5 ? "neg" : ""} title={`₹${num(stats.charges, 0)} in charges`}>
      {pct(stats.charges_share, 1)} of gross
    </span>
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
              <th>Instrument</th>
              <th>Strategy</th>
              <th className="num">Return</th>
              <th className="num">Sharpe</th>
              <th className="num">Drawdown</th>
              <th className="num">Trades</th>
              <th className="num" title="Charges as a share of gross profit">Charges</th>
              <th className="num">Holdout</th>
              <th className="num">Confidence</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={`${e.instrument_id}-${e.candidate}`}>
                <td className="small">{instrumentsOf(e)}</td>
                <td className="small">{e.label}</td>
                <td className={`num ${tone(e.validation.return)}`}>{pct(e.validation.return)}</td>
                <td className="num">{ratio(e.validation.sharpe)}</td>
                <td className="num">{pct(e.validation.max_drawdown)}</td>
                <td className="num">{e.validation.trades}</td>
                <td className="num"><ChargeShare stats={e.validation} /></td>
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
  const options = useData(() => get<UniverseOption[]>("/autopilot/universe"), []);
  const [form, setForm] = useState<AutopilotConfig>(config);
  const [query, setQuery] = useState("");
  const [usdt, setUsdt] = useState(String(config.fx_rates.USDT ?? "85"));
  const set = <K extends keyof AutopilotConfig>(key: K, value: AutopilotConfig[K]) => setForm((f) => ({ ...f, [key]: value }));
  const groups = useMemo(() => {
    const out = new Map<string, UniverseOption[]>();
    for (const o of options.data ?? []) {
      if (query && !o.label.toLowerCase().includes(query.toLowerCase())) continue;
      out.set(o.group, [...(out.get(o.group) ?? []), o]);
    }
    return [...out.entries()];
  }, [options.data, query]);
  const numberField = (key: keyof AutopilotConfig, label: string, hint?: string) => (
    <label className="field" title={hint}>
      {label}
      <input inputMode="decimal" value={String(form[key])} onChange={(e) => set(key, e.target.value as never)} />
    </label>
  );
  const NUMERIC: (keyof AutopilotConfig)[] = [
    "max_positions",
    "max_weight",
    "min_sharpe",
    "max_drawdown",
    "min_dsr",
    "min_trades",
    "history_bars",
    "cycle_hours",
    "max_deployment_drawdown",
    "min_paper_days",
    "min_paper_trades",
    "loss_floor",
    "lock_in_gains",
    "daily_loss_limit",
    "max_cost_share",
    "max_participation",
    "portfolio_drawdown_limit",
    "halt_cooldown_days",
    "roll_days",
  ];
  const toNumbers = (): AutopilotConfig => {
    const out = { ...form, fx_rates: { ...form.fx_rates, USDT: usdt, USDC: usdt, USD: usdt } } as Record<string, unknown>;
    for (const key of NUMERIC) out[key] = Number(form[key]);
    return out as unknown as AutopilotConfig;
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const saved = await run(() => put("/autopilot/config", toNumbers()), "Autopilot settings saved");
    if (saved) onClose();
  };
  const toggle = (id: string, on: boolean) => set("universe", on ? [...form.universe, id] : form.universe.filter((x) => x !== id));

  return (
    <Dialog title="Autopilot settings" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="small muted" style={{ marginBottom: 6 }}>
            Universe ({form.universe.length} selected) — what the autopilot may trade
          </legend>
          <input placeholder="Search stocks, gold, crypto, futures…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search instruments" />
          <div style={{ maxHeight: 220, overflow: "auto", marginTop: 6 }}>
            {groups.map(([group, items]) => (
              <div key={group} style={{ marginBottom: 8 }}>
                <div className="small muted" style={{ fontWeight: 600 }}>{group}</div>
                <div className="chips">
                  {items.slice(0, 120).map((o) => (
                    <label key={o.id} className="field inline small" title={o.id}>
                      <input type="checkbox" checked={form.universe.includes(o.id)} onChange={(e) => toggle(o.id, e.target.checked)} /> {o.label}
                    </label>
                  ))}
                </div>
              </div>
            ))}
            {groups.length === 0 && (
              <span className="small muted">{options.loading ? "Loading…" : "Nothing found. Connect Fyers or Binance to load instruments."}</span>
            )}
          </div>
          <p className="small muted" style={{ margin: "4px 0 0" }}>
            Futures appear as rolling front months (e.g. MCX:GOLDM1!) and roll before expiry. XAU/USD is available as PAXG/USDT
            (tokenized gold) on Binance; in INR use gold ETFs or MCX gold.
          </p>
        </fieldset>
        <div className="form-grid">
          {numberField("capital", "Paper budget (₹)")}
          {numberField("max_positions", "Max positions at once")}
          {numberField("max_weight", "Max share per position (0–1)")}
          <label className="field">
            Product
            <select value={form.product} onChange={(e) => set("product", e.target.value as AutopilotConfig["product"])}>
              <option value="CNC">Positional (delivery / carry-forward)</option>
              <option value="INTRADAY">Intraday (closed before each market's cutoff)</option>
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
          {numberField("vol_target", "Volatility target per position (yearly)", "0.2 = size each position so it moves about 20% a year; never leveraged")}
          {numberField("stop_loss", "Stop-loss (fraction)", "0.08 = exit 8% against entry; 0 disables")}
          {numberField("trailing_stop", "Trailing stop (fraction)", "0.05 = exit 5% from the best close since entry; 0 disables")}
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
        <details open>
          <summary className="small">Capital protection</summary>
          <div className="form-grid" style={{ marginTop: 8 }}>
            {numberField("loss_floor", "Max loss of budget (0–1)", "0.10 = never lose more than 10% of the budget; at the floor everything goes to cash")}
            {numberField("lock_in_gains", "Lock in share of peak gains (0–1)", "0.5 = once in profit, the floor rises to keep half the best gain")}
            {numberField("daily_loss_limit", "Daily loss limit (0–1)", "Enforced by the risk engine per account; after it, orders may only reduce positions")}
            {numberField("portfolio_drawdown_limit", "Book drawdown halt (0–1)", "Cut everything to cash if the book falls this far from its peak")}
            {numberField("halt_cooldown_days", "Pause after a halt (days)")}
            {numberField("max_deployment_drawdown", "Close a strategy at drawdown of its capital (0–1)")}
          </div>
        </details>
        <details>
          <summary className="small">Charges, evidence and AI analyst</summary>
          <div className="form-grid" style={{ marginTop: 8 }}>
            {numberField("max_cost_share", "Max charges share of gross profit (0–1)", "Reject strategies whose fees and taxes eat more than this")}
            {numberField("max_participation", "Max share of daily traded value (0–1)", "Liquidity limit on real data")}
            {numberField("min_sharpe", "Min out-of-sample Sharpe")}
            {numberField("max_drawdown", "Max backtest drawdown (0–1)")}
            {numberField("min_dsr", "Min luck-adjusted confidence (0–1)")}
            {numberField("min_trades", "Min trades in validation")}
            {numberField("min_paper_days", "Paper days before live")}
            {numberField("min_paper_trades", "Paper trades before live")}
            {numberField("cycle_hours", "Cycle every (hours, intraday bars)")}
            {numberField("roll_days", "Roll futures days before expiry")}
            <label className="field" title="Used to size USDT-quoted crypto from the INR budget">
              ₹ per USDT
              <input inputMode="decimal" value={usdt} onChange={(e) => setUsdt(e.target.value)} />
            </label>
          </div>
          <label className="field inline" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={form.use_analyst} onChange={(e) => set("use_analyst", e.target.checked)} /> AI analyst reviews each
            cycle (NVIDIA when NVIDIA_API_KEY is set on the server, else Claude)
          </label>
          <label className="field inline">
            <input
              type="checkbox"
              checked={form.analyst_can_veto}
              disabled={!form.use_analyst}
              onChange={(e) => set("analyst_can_veto", e.target.checked)}
            />{" "}
            Let a high-severity concern veto a strategy (it can only remove trades)
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
  const [futures, setFutures] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(
      () => post("/autopilot/live:arm", { account_id: accountId, capital_cap: cap, allow_futures: futures }),
      "Live trading armed",
    );
    if (out) onClose();
  };
  return (
    <Dialog title="Arm live trading" onClose={onClose}>
      {live.length === 0 ? (
        <div className="stack">
          <p style={{ margin: 0 }}>Connect and sign in to a broker (Fyers for NSE/MCX, Binance for crypto) first; it appears here as a live account.</p>
          <div className="row end">
            <NavLink to="/connections" className="button" onClick={onClose}>Go to Connections</NavLink>
          </div>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <div className="alert warn">
            The autopilot will place <strong>real orders</strong> on this account for strategies that have proven themselves on paper,
            without asking each time. Past performance, including backtests and paper trading, does not guarantee future results; losses
            are limited by the protections, not prevented.
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
            <input type="checkbox" checked={futures} onChange={(e) => setFutures(e.target.checked)} /> Also trade futures (MCX commodities,
            currency) on this account
          </label>
          {futures && (
            <div className="alert warn small">
              Futures are leveraged and may go short. Their order quantities are in lots — place a one-lot test order on this account and
              check the position at your broker before relying on it.
            </div>
          )}
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
