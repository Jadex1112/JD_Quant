import { useState } from "react";
import { NavLink } from "react-router-dom";
import { get, post, put } from "../api";
import { useApp } from "../app-state";
import { Badge, Dialog, Empty, ModeBadge, Section, StatusBadge, useData } from "../components/ui";
import { num, pct, signed, time, tone } from "../format";
import { Kpi } from "./intelligence/shared";

interface Status {
  at: string;
  bot: "RUNNING" | "IDLE" | "STOPPED";
  running_strategies: number;
  live_strategies: number;
  accounts: {
    account_id: string;
    name: string;
    mode: string;
    currency: string;
    open_positions: number;
    exposure: string;
    unrealized_pnl: string;
    realized_today: string;
    trades_today: number;
    wins_today: number;
    losses_today: number;
    daily_pnl: string;
    daily_loss_limit: string | null;
    daily_loss_used_pct: number | null;
    reduce_only: boolean;
  }[];
  strategies: { deployment_id: string; strategy: string; account_id: string; mode: string; state: string; reason: string | null; instruments: string[]; trades_today: number; pnl_today: string }[];
  kill_switches: { kill_switch_id: string; scope: string; target_id: string | null; action: string; reason: string; by: string; at: string }[];
  circuit: Circuit;
  signals: { total: number; by_status: Record<string, number>; blocked_reasons: Record<string, number> };
}

interface Circuit {
  settings: Record<string, number | boolean>;
  trips: { at: string; kind: string; scope: string; target_id: string | null; reason: string }[];
  reconciliation: Record<string, { at: string; supported?: boolean; venue?: string; error?: string; rows?: { instrument_id: string; platform: string; broker: string; state: string }[] }>;
  consecutive_losses: Record<string, number>;
}

interface Signal {
  signal_id: string;
  order_id: string;
  at: string;
  deployment_id: string;
  strategy: string;
  account_id: string;
  instrument_id: string;
  side: string;
  quantity: string;
  entry_type: string;
  limit_price: string | null;
  reason_codes: string[];
  confidence: number | null;
  stop_loss: string | null;
  target: string | null;
  exit_reason: string | null;
  status: string;
  blocked_reason: string | null;
  risk_checks: { limit: string; passed: boolean; projected: string | null; threshold: string | null }[];
  average_price: string | null;
}

interface Trade {
  trade_id: string;
  account_id: string;
  deployment_id: string | null;
  strategy: string;
  instrument_id: string;
  direction: string;
  quantity: string;
  entry_price: string;
  exit_price: string;
  opened_at: string;
  closed_at: string;
  holding_minutes: number;
  fees: string;
  net_pnl: string;
  return_pct: number | null;
  entry_reasons: string[];
  exit_reason: string;
  entry_context: Record<string, unknown> | null;
  paper: boolean;
  review: Review | null;
}

interface Review {
  summary: string;
  review: string;
  test_next?: string;
  normal_outcome?: string | null;
  by: string;
}

interface Journal {
  trades: Trade[];
  open: { account_id: string; instrument_id: string; direction: string; quantity: string; entry_price: string; opened_at: string; entry_reasons: string[] }[];
  patterns: {
    trades: number;
    net_pnl: number;
    win_rate: number | null;
    max_consecutive_losses: number;
    groups: Record<string, { group: string; trades: number; win_rate: number; total_pnl: number; average_pnl: number }[]>;
    observations: string[];
  };
}

const TABS = [
  ["signals", "Signals"],
  ["journal", "Trade journal"],
  ["execution", "Execution quality"],
  ["protections", "Protections"],
  ["reconcile", "Positions & reconciliation"],
] as const;

export function AutomationPage() {
  const { can } = useApp();
  const status = useData(() => get<Status>("/automation/status"), [], 4000);
  const [tab, setTab] = useState<string>("signals");
  const [stopping, setStopping] = useState(false);
  const s = status.data;
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>Bot control</h1>
        {can("killswitch:trigger") && (
          <button className="danger" onClick={() => setStopping(true)}>
            ⏻ STOP ALL
          </button>
        )}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Strategies decide by their own rules, the risk engine and guards can veto any order, and only the execution engine
        sends orders. The AI explains and reviews; it does not place trades from here.
      </p>
      {!s ? (
        <Empty>{status.error ? "Status unavailable." : "Loading…"}</Empty>
      ) : (
        <>
          <div className="metric-grid">
            <div className="card kpi">
              <span className="label">Bot</span>
              <span className={`bot-state ${s.bot === "STOPPED" ? "neg" : s.bot === "RUNNING" ? "pos" : ""}`}>{s.bot}</span>
            </div>
            <Kpi label="Running strategies" value={s.running_strategies} />
            <Kpi label="Live strategies" value={s.live_strategies} tone={s.live_strategies ? "neg" : ""} />
            <Kpi label="Signals" value={s.signals.total} />
            <Kpi label="Blocked signals" value={s.signals.by_status.BLOCKED ?? 0} />
            <Kpi label="Active kill switches" value={s.kill_switches.length} tone={s.kill_switches.length ? "neg" : ""} />
          </div>
          {s.kill_switches.length > 0 && (
            <div className="alert warn">
              {s.kill_switches.map((k) => (
                <div key={k.kill_switch_id}>
                  <strong>{k.scope}{k.target_id ? ` ${k.target_id}` : ""}</strong> · {k.action.replaceAll("_", " ").toLowerCase()} · {k.reason}{" "}
                  <span className="small muted">({k.by}, {time(k.at)})</span>
                </div>
              ))}
              <div className="small" style={{ marginTop: 4 }}>
                Release them on the <NavLink to="/risk">Risk</NavLink> page once the cause is fixed.
              </div>
            </div>
          )}
          <Section title="Accounts today">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Account</th><th>Mode</th><th>Day P&amp;L</th><th>Daily loss used</th><th>Trades (W/L)</th><th>Open positions</th><th>Exposure</th><th>Unrealized</th><th /></tr>
                </thead>
                <tbody>
                  {s.accounts.map((a) => (
                    <tr key={a.account_id}>
                      <td>{a.name} <span className="small muted">{a.account_id}</span></td>
                      <td><ModeBadge mode={a.mode} /></td>
                      <td className={tone(a.daily_pnl)}>{signed(a.daily_pnl)} {a.currency}</td>
                      <td>
                        {a.daily_loss_used_pct == null ? "no limit" : (
                          <span className={a.daily_loss_used_pct >= 80 ? "neg" : ""}>{a.daily_loss_used_pct.toFixed(0)}% of {num(a.daily_loss_limit)}</span>
                        )}
                      </td>
                      <td>{a.trades_today} ({a.wins_today}/{a.losses_today})</td>
                      <td>{a.open_positions}</td>
                      <td>{num(a.exposure, 2)}</td>
                      <td className={tone(a.unrealized_pnl)}>{signed(a.unrealized_pnl)}</td>
                      <td>{a.reduce_only && <Badge kind="warn">reduce only</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
          <Section title="Strategies" actions={<NavLink to="/pipeline">Strategy pipeline</NavLink>}>
            {s.strategies.length === 0 ? (
              <Empty>No strategies deployed.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Strategy</th><th>Account</th><th>Mode</th><th>State</th><th>Instruments</th><th>Trades today</th><th>P&amp;L today</th><th>Losses in a row</th></tr></thead>
                  <tbody>
                    {s.strategies.map((d) => (
                      <tr key={d.deployment_id}>
                        <td>{d.strategy} <div className="small muted">{d.deployment_id}</div></td>
                        <td className="small">{d.account_id}</td>
                        <td><ModeBadge mode={d.mode} /></td>
                        <td title={d.reason ?? undefined}><StatusBadge status={d.state} />{d.reason && <div className="small muted">{d.reason}</div>}</td>
                        <td className="small">{d.instruments.join(", ")}</td>
                        <td>{d.trades_today}</td>
                        <td className={tone(d.pnl_today)}>{signed(d.pnl_today)}</td>
                        <td>{s.circuit.consecutive_losses[d.deployment_id] ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </>
      )}
      <div className="tabs" role="tablist" aria-label="Automation views">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      {tab === "signals" && <SignalsTab />}
      {tab === "journal" && <JournalTab />}
      {tab === "execution" && <ExecutionTab />}
      {tab === "protections" && <ProtectionsTab circuit={s?.circuit} reload={status.reload} />}
      {tab === "reconcile" && <ReconcileTab circuit={s?.circuit} reload={status.reload} />}
      {stopping && <StopAllDialog onClose={() => { setStopping(false); status.reload(); }} />}
    </div>
  );
}

function StopAllDialog({ onClose }: { onClose: () => void }) {
  const { run } = useApp();
  const [action, setAction] = useState("CANCEL_OPEN");
  const [reason, setReason] = useState("Stop all automated trading");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (await run(() => post("/kill-switches", { scope: "GLOBAL", action, reason, target_id: null }), "All trading stopped")) onClose();
  };
  return (
    <Dialog title="Stop all trading" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="small">A global kill switch blocks every new order on every account, including the autopilot and all strategies. Choose what happens to what is already open:</p>
        {[
          ["BLOCK_NEW", "Block new orders only", "Working orders and positions stay as they are."],
          ["CANCEL_OPEN", "Block and cancel working orders", "Positions stay open with their broker-side stops, if any."],
          ["FLATTEN", "Block, cancel and close all positions", "Sends market orders to close every position. Use in an emergency."],
        ].map(([value, label, help]) => (
          <label key={value} className="field inline" style={{ alignItems: "flex-start" }}>
            <input type="radio" name="stop-action" value={value} checked={action === value} onChange={() => setAction(value)} />
            <span><strong>{label}</strong><div className="small muted">{help}</div></span>
          </label>
        ))}
        <label className="field">
          Reason
          <input required value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="danger" type="submit">Stop all</button>
        </div>
      </form>
    </Dialog>
  );
}

function SignalsTab() {
  const [status, setStatus] = useState("");
  const signals = useData(() => get<Signal[]>(`/automation/signals?limit=300${status ? `&status=${status}` : ""}`), [status], 5000);
  const [selected, setSelected] = useState<Signal | null>(null);
  return (
    <div className="grid two">
      <Section
        title="Signals"
        actions={
          <select aria-label="Signal status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {["GENERATED", "BLOCKED", "SUBMITTED", "FILLED", "PARTIAL", "CANCELLED", "REJECTED"].map((s) => <option key={s}>{s}</option>)}
          </select>
        }
      >
        {(signals.data ?? []).length === 0 ? (
          <Empty>No automated orders yet. Every order a strategy sends is recorded here with its reasons.</Empty>
        ) : (
          <div className="table-wrap" style={{ maxHeight: 560 }}>
            <table>
              <thead><tr><th>Time</th><th>Strategy</th><th>Instrument</th><th>Side</th><th>Reasons</th><th>Status</th></tr></thead>
              <tbody>
                {signals.data!.map((s) => (
                  <tr key={s.order_id} onClick={() => setSelected(s)} style={{ cursor: "pointer" }} aria-selected={selected?.order_id === s.order_id}>
                    <td className="small">{time(s.at)}</td>
                    <td className="small">{s.strategy}</td>
                    <td className="small">{s.instrument_id}</td>
                    <td className={s.side === "BUY" ? "pos" : "neg"}>{s.side} {num(s.quantity)}</td>
                    <td className="small">{s.exit_reason ? `exit: ${s.exit_reason}` : s.reason_codes.join(", ") || "—"}</td>
                    <td><StatusBadge status={s.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Why">
        {!selected ? (
          <p className="muted small">Select a signal to see why it was sent and what happened to it. Ask the copilot "why was this signal rejected?" for a written answer.</p>
        ) : (
          <div className="stack">
            <div>
              <strong>{selected.strategy || selected.deployment_id}</strong> {selected.side.toLowerCase()} {num(selected.quantity)} {selected.instrument_id}
              <div className="small muted">{selected.order_id} · {time(selected.at)}</div>
            </div>
            <dl className="kv">
              <dt>Reason codes</dt><dd>{selected.reason_codes.length ? selected.reason_codes.map((r) => <Badge key={r}>{r}</Badge>) : "none recorded"}</dd>
              <dt>Confidence</dt><dd>{selected.confidence == null ? "—" : pct(selected.confidence, 0)}</dd>
              <dt>Order</dt><dd>{selected.entry_type}{selected.limit_price ? ` @ ${selected.limit_price}` : ""}</dd>
              <dt>Stop / target</dt><dd>{selected.stop_loss ?? "—"} / {selected.target ?? "—"}</dd>
              {selected.exit_reason && <><dt>Exit reason</dt><dd>{selected.exit_reason}</dd></>}
              <dt>Outcome</dt><dd><StatusBadge status={selected.status} /> {selected.average_price && `filled at ${selected.average_price}`}</dd>
              {selected.blocked_reason && <><dt>Blocked because</dt><dd className="neg">{selected.blocked_reason}</dd></>}
            </dl>
            {selected.risk_checks.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Risk check</th><th>Result</th><th>Projected</th><th>Limit</th></tr></thead>
                  <tbody>
                    {selected.risk_checks.map((c) => (
                      <tr key={c.limit}>
                        <td>{c.limit.replaceAll("_", " ").toLowerCase()}</td>
                        <td className={c.passed ? "pos" : "neg"}>{c.passed ? "passed" : "failed"}</td>
                        <td>{num(c.projected)}</td>
                        <td>{num(c.threshold)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Section>
    </div>
  );
}

function JournalTab() {
  const { run, can } = useApp();
  const [days, setDays] = useState(30);
  const journal = useData(() => get<Journal>(`/automation/journal?days=${days}`), [days], 15000);
  const [selected, setSelected] = useState<Trade | null>(null);
  const [dimension, setDimension] = useState("entry_reason");
  const j = journal.data;
  const review = async (t: Trade) => {
    const r = await run(() => post<Review>(`/automation/journal/${t.trade_id}/review`));
    if (r) setSelected({ ...t, review: r });
  };
  if (!j) return <Empty>Loading…</Empty>;
  return (
    <div className="stack">
      <div className="metric-grid">
        <Kpi label="Closed trades" value={j.patterns.trades} />
        <Kpi label="Net P&L" value={signed(j.patterns.net_pnl)} tone={tone(j.patterns.net_pnl)} />
        <Kpi label="Win rate" value={pct(j.patterns.win_rate, 0)} />
        <Kpi label="Longest losing streak" value={j.patterns.max_consecutive_losses} />
        <Kpi label="Open trades" value={j.open.length} />
      </div>
      {j.patterns.observations.length > 0 && (
        <div className="alert small">
          <strong>Patterns in your losses and wins</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{j.patterns.observations.map((o) => <li key={o}>{o}</li>)}</ul>
        </div>
      )}
      <div className="grid two">
        <Section
          title="Closed trades"
          actions={
            <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[1, 7, 30, 90, 365].map((d) => <option key={d} value={d}>{d === 1 ? "Today" : `${d} days`}</option>)}
            </select>
          }
        >
          {j.trades.length === 0 ? (
            <Empty>No closed trades in this period.</Empty>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 520 }}>
              <table>
                <thead><tr><th>Closed</th><th>Instrument</th><th>Strategy</th><th>Direction</th><th>Net</th><th>Held</th><th>Exit</th></tr></thead>
                <tbody>
                  {j.trades.map((t) => (
                    <tr key={t.trade_id} onClick={() => setSelected(t)} style={{ cursor: "pointer" }}>
                      <td className="small">{time(t.closed_at)}</td>
                      <td className="small">{t.instrument_id} {t.paper && <Badge kind="paper">paper</Badge>}</td>
                      <td className="small">{t.strategy || "manual"}</td>
                      <td>{t.direction}</td>
                      <td className={tone(t.net_pnl)}>{signed(t.net_pnl)}</td>
                      <td className="small">{t.holding_minutes < 120 ? `${t.holding_minutes.toFixed(0)}m` : `${(t.holding_minutes / 60).toFixed(1)}h`}</td>
                      <td className="small">{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
        {selected ? (
          <Section title="Trade" actions={<button className="small" onClick={() => setSelected(null)}>Close</button>}>
            <dl className="kv">
              <dt>Trade</dt><dd>{selected.direction} {num(selected.quantity)} {selected.instrument_id}</dd>
              <dt>Entry → exit</dt><dd>{selected.entry_price} → {selected.exit_price}</dd>
              <dt>Opened / closed</dt><dd>{time(selected.opened_at)} / {time(selected.closed_at)}</dd>
              <dt>Net (fees)</dt><dd className={tone(selected.net_pnl)}>{signed(selected.net_pnl)} ({selected.fees}) {selected.return_pct != null && `· ${signed(selected.return_pct)}%`}</dd>
              <dt>Why in</dt><dd>{selected.entry_reasons.length ? selected.entry_reasons.map((r) => <Badge key={r}>{r}</Badge>) : "none recorded"}</dd>
              <dt>Why out</dt><dd>{selected.exit_reason}</dd>
              {selected.entry_context && Object.keys(selected.entry_context).length > 0 && (
                <>
                  <dt>Market at entry</dt>
                  <dd className="small">
                    {Object.entries(selected.entry_context)
                      .filter(([, v]) => v !== null && typeof v !== "object")
                      .map(([k, v]) => `${k.replaceAll("_", " ")}: ${typeof v === "number" ? num(v, 4) : String(v)}`)
                      .join(" · ")}
                  </dd>
                </>
              )}
            </dl>
            {selected.review ? (
              <div className="alert small" style={{ marginTop: 12 }}>
                <strong>{selected.review.summary}</strong>
                <div>{selected.review.review}</div>
                {selected.review.test_next && <div>Test next: {selected.review.test_next}</div>}
                {selected.review.normal_outcome && <div>{selected.review.normal_outcome}</div>}
                <div className="muted">Review by {selected.review.by === "template" ? "rules (no AI model configured)" : selected.review.by}</div>
              </div>
            ) : (
              can("ai.copilot:use") && <button style={{ marginTop: 12 }} onClick={() => review(selected)}>✦ Review this trade</button>
            )}
          </Section>
        ) : (
          <Section
            title="What works and what doesn't"
            actions={
              <select aria-label="Group by" value={dimension} onChange={(e) => setDimension(e.target.value)}>
                {Object.keys(j.patterns.groups).map((k) => <option key={k} value={k}>{k.replaceAll("_", " ")}</option>)}
              </select>
            }
          >
            {(j.patterns.groups[dimension] ?? []).length === 0 ? (
              <Empty>No trades yet.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>{dimension.replaceAll("_", " ")}</th><th>Trades</th><th>Win rate</th><th>Total</th><th>Average</th></tr></thead>
                  <tbody>
                    {j.patterns.groups[dimension].map((g) => (
                      <tr key={g.group}>
                        <td>{g.group}</td>
                        <td>{g.trades}</td>
                        <td>{pct(g.win_rate, 0)}</td>
                        <td className={tone(g.total_pnl)}>{signed(g.total_pnl)}</td>
                        <td className={tone(g.average_pnl)}>{signed(g.average_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="small muted">Small groups are noise; look for patterns that hold over many trades.</p>
          </Section>
        )}
      </div>
    </div>
  );
}

interface ExecRecord {
  order_id: string;
  venue: string;
  deployment_id: string | null;
  instrument_id: string;
  side: string;
  order_type: string;
  requested: string;
  created_at: string;
  expected_price: number | null;
  expected_basis: string | null;
  average_price: number | null;
  status: string;
  fill_ratio: number | null;
  slippage_bps: number | null;
  slippage_cost: number | null;
  seconds_to_complete: number | null;
}

interface ExecGroup {
  group: string;
  orders: number;
  average_slippage_bps: number | null;
  worst_slippage_bps: number | null;
  slippage_cost: number | null;
  average_fill_ratio: number | null;
  average_seconds_to_fill: number | null;
}

function ExecutionTab() {
  const data = useData(() => get<{ orders: ExecRecord[]; summary: { orders: number; by: Record<string, ExecGroup[]> } }>("/automation/execution?limit=200"), [], 10000);
  const [dim, setDim] = useState("venue");
  const d = data.data;
  if (!d) return <Empty>Loading…</Empty>;
  return (
    <div className="stack">
      <Section
        title="Slippage and fills"
        actions={
          <div className="segmented" role="group" aria-label="Group by">
            {["venue", "deployment_id", "instrument_id"].map((k) => (
              <button key={k} className="small" aria-pressed={dim === k} onClick={() => setDim(k)}>{k === "deployment_id" ? "strategy" : k.replace("_id", "")}</button>
            ))}
          </div>
        }
      >
        <p className="small muted">
          Slippage is measured against the price expected when the order was sent (the depth-walked book where available,
          otherwise the quote). Positive means worse than expected.
        </p>
        {(d.summary.by[dim] ?? []).length === 0 ? (
          <Empty>No filled orders yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Group</th><th>Orders</th><th>Average slippage</th><th>Worst</th><th>Cost</th><th>Fill ratio</th><th>Time to fill</th></tr></thead>
              <tbody>
                {d.summary.by[dim].map((g) => (
                  <tr key={g.group}>
                    <td>{g.group}</td>
                    <td>{g.orders}</td>
                    <td>{g.average_slippage_bps == null ? "—" : `${g.average_slippage_bps.toFixed(1)} bps`}</td>
                    <td>{g.worst_slippage_bps == null ? "—" : `${g.worst_slippage_bps.toFixed(1)} bps`}</td>
                    <td className={tone(-(g.slippage_cost ?? 0))}>{num(g.slippage_cost, 2)}</td>
                    <td>{pct(g.average_fill_ratio, 0)}</td>
                    <td>{g.average_seconds_to_fill == null ? "—" : `${g.average_seconds_to_fill.toFixed(1)}s`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Recent orders">
        <div className="table-wrap" style={{ maxHeight: 480 }}>
          <table>
            <thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th>Type</th><th>Expected</th><th>Filled at</th><th>Slippage</th><th>Fill</th><th>Venue</th></tr></thead>
            <tbody>
              {d.orders.map((o) => (
                <tr key={o.order_id}>
                  <td className="small">{time(o.created_at)}</td>
                  <td className="small">{o.instrument_id}</td>
                  <td className={o.side === "BUY" ? "pos" : "neg"}>{o.side} {num(o.requested)}</td>
                  <td className="small">{o.order_type}</td>
                  <td title={o.expected_basis ?? undefined}>{num(o.expected_price, 6)}</td>
                  <td>{num(o.average_price, 6)}</td>
                  <td className={tone(-(o.slippage_bps ?? 0))}>{o.slippage_bps == null ? "—" : `${o.slippage_bps.toFixed(1)} bps`}</td>
                  <td>{pct(o.fill_ratio, 0)}</td>
                  <td className="small">{o.venue}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

const GUARD_HELP: Record<string, string> = {
  duplicate_window_seconds: "Block a strategy order identical to one sent this many seconds ago (0 = off)",
  max_open_positions: "Most positions open at once per account (0 = no limit)",
  max_group_notional: "Most exposure in one sector or asset group, account currency (0 = no limit)",
  require_stop_for_automated: "Automated entries must state a stop-loss",
  max_expected_slippage_bps: "Block entries whose expected slippage for their size exceeds this",
};

const CIRCUIT_HELP: Record<string, string> = {
  enabled: "Trip kill switches automatically",
  stale_seconds: "Market data silent this long counts as a feed failure",
  rejection_limit: "Broker rejections within the window that stop an account",
  rejection_window_minutes: "Window for counting rejections",
  slippage_multiple: "Slippage this many times the usual trips the strategy",
  slippage_floor_bps: "…and at least this many basis points",
  consecutive_losses: "Losing trades in a row that pause a strategy",
  reconcile: "Compare positions with the broker every minute; stop an account on a shortfall",
  live_only: "Apply feed, rejection and slippage breakers to live accounts only",
};

function SettingsForm({ title, values, help, onSave }: { title: string; values: Record<string, number | boolean>; help: Record<string, string>; onSave: (v: Record<string, number | boolean>) => void }) {
  const [form, setForm] = useState(values);
  const { can } = useApp();
  const editable = can("risk.profile:update");
  return (
    <Section title={title}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(form);
        }}
      >
        {Object.entries(form).map(([k, v]) =>
          typeof v === "boolean" ? (
            <label key={k} className="field inline">
              <input type="checkbox" disabled={!editable} checked={v} onChange={(e) => setForm({ ...form, [k]: e.target.checked })} />
              {help[k] ?? k}
            </label>
          ) : (
            <label key={k} className="field">
              {help[k] ?? k}
              <input type="number" step="any" min={0} disabled={!editable} value={v} onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />
            </label>
          ),
        )}
        {editable && <div className="row end"><button className="primary" type="submit">Save</button></div>}
      </form>
    </Section>
  );
}

function ProtectionsTab({ circuit, reload }: { circuit?: Circuit; reload: () => void }) {
  const { run } = useApp();
  const guards = useData(() => get<{ settings: Record<string, number | boolean>; recent_blocks: { at: string; order_id: string; instrument_id: string; reason: string }[]; recent_warnings: { at: string; instrument_id: string; event: { kind?: string; day?: string } }[] }>("/automation/guards"), []);
  return (
    <div className="stack">
      <div className="grid two">
        {guards.data && (
          <SettingsForm
            key={JSON.stringify(guards.data.settings)}
            title="Pre-trade guards"
            values={guards.data.settings}
            help={GUARD_HELP}
            onSave={async (v) => {
              if (await run(() => put("/automation/guards", v), "Guards saved")) guards.reload();
            }}
          />
        )}
        {circuit && (
          <SettingsForm
            key={JSON.stringify(circuit.settings)}
            title="Circuit breakers"
            values={circuit.settings}
            help={CIRCUIT_HELP}
            onSave={async (v) => {
              if (await run(() => put("/automation/circuit", v), "Circuit breakers saved")) reload();
            }}
          />
        )}
      </div>
      <div className="grid two">
        <Section title="Breaker trips">
          {!circuit?.trips.length ? (
            <Empty>No trips. Feed failures, broker rejections, abnormal slippage, losing streaks and position mismatches trip kill switches here.</Empty>
          ) : (
            <ul className="event-list">
              {[...circuit.trips].reverse().map((t) => (
                <li key={t.at + t.kind + (t.target_id ?? "")}>
                  <span className="small muted">{time(t.at)}</span>
                  <Badge kind="bad">{t.kind.replaceAll("_", " ")}</Badge>
                  <span className="small">{t.scope}{t.target_id ? ` ${t.target_id}` : ""}</span>
                  <span>{t.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Guard decisions">
          {!guards.data || (guards.data.recent_blocks.length === 0 && guards.data.recent_warnings.length === 0) ? (
            <Empty>No orders blocked or flagged by guards.</Empty>
          ) : (
            <ul className="event-list">
              {[...guards.data.recent_blocks].reverse().map((b) => (
                <li key={b.order_id}>
                  <span className="small muted">{time(b.at)}</span>
                  <Badge kind="bad">blocked</Badge>
                  <span className="small">{b.instrument_id}</span>
                  <span>{b.reason}</span>
                </li>
              ))}
              {[...guards.data.recent_warnings].reverse().map((w, i) => (
                <li key={`w${i}`}>
                  <span className="small muted">{time(w.at)}</span>
                  <Badge kind="warn">warning</Badge>
                  <span className="small">{w.instrument_id}</span>
                  <span>corporate event {w.event?.kind?.toLowerCase()} on {w.event?.day}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </div>
  );
}

function ReconcileTab({ circuit, reload }: { circuit?: Circuit; reload: () => void }) {
  const { run } = useApp();
  const portfolio = useData(() => get<{ instruments: { instrument_id: string; net_quantity: string; accounts: { account_id: string; mode: string | null; deployment_id: string | null; quantity: string; average_price: string; unrealized_pnl: string | null }[] }[] }>("/automation/portfolio"), [], 10000);
  const results = Object.entries(circuit?.reconciliation ?? {});
  return (
    <div className="grid two">
      <Section title="All positions, every broker" actions={<NavLink to="/trading">Trading</NavLink>}>
        {!portfolio.data?.instruments.length ? (
          <Empty>No open positions.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Instrument</th><th>Account</th><th>Quantity</th><th>Average</th><th>Unrealized</th></tr></thead>
              <tbody>
                {portfolio.data.instruments.flatMap((i) =>
                  i.accounts.map((a, n) => (
                    <tr key={i.instrument_id + a.account_id + (a.deployment_id ?? "")}>
                      <td>{n === 0 ? <strong>{i.instrument_id}</strong> : ""}</td>
                      <td className="small">{a.account_id} {a.mode && <ModeBadge mode={a.mode} />}</td>
                      <td>{num(a.quantity)}</td>
                      <td>{num(a.average_price, 6)}</td>
                      <td className={tone(a.unrealized_pnl)}>{signed(a.unrealized_pnl)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Each account is listed separately; positions at different brokers are never netted.</p>
      </Section>
      <Section
        title="Broker reconciliation"
        actions={<button onClick={async () => { await run(() => post("/automation/reconcile"), "Reconciled"); reload(); }}>Reconcile now</button>}
      >
        <p className="small muted">
          Compares what JD Quant expects with what each live broker reports. When the broker holds less than expected, the
          account is stopped until you check it.
        </p>
        {results.length === 0 ? (
          <Empty>No live broker accounts reconciled yet.</Empty>
        ) : (
          results.map(([account, r]) => (
            <div key={account} style={{ marginBottom: 12 }}>
              <strong>{account}</strong> <span className="small muted">{r.venue} · {time(r.at)}</span>
              {r.error && <div className="alert warn small">{r.error}</div>}
              {r.supported === false && <div className="small muted">This broker does not report positions to the platform.</div>}
              {r.rows && (
                r.rows.length === 0 ? <div className="small pos">Positions match.</div> : (
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>Instrument</th><th>Platform</th><th>Broker</th><th>State</th></tr></thead>
                      <tbody>
                        {r.rows.map((row) => (
                          <tr key={row.instrument_id}>
                            <td>{row.instrument_id}</td>
                            <td>{num(row.platform)}</td>
                            <td>{num(row.broker)}</td>
                            <td><StatusBadge status={row.state} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </div>
          ))
        )}
      </Section>
    </div>
  );
}
