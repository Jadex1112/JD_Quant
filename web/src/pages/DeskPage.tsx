import { useEffect, useState } from "react";
import { get, post, type Instrument } from "../api";
import { useApp } from "../app-state";
import { Badge, Empty, Section, StatusBadge, useData } from "../components/ui";
import { pct, signed, time, tone } from "../format";

interface Step {
  role: string;
  at: string;
  output: Record<string, unknown>;
}

interface Report {
  report_id: string;
  instrument_id: string;
  requested_at: string;
  model: string;
  status: "RUNNING" | "DONE" | "FAILED";
  steps?: Step[];
  error: string | null;
  rating?: string;
  confidence?: number | null;
  summary?: string;
  trade?: Record<string, unknown>;
  history_source?: string;
  horizon: number;
  interval_seconds: number;
  outcome?: { return_pct: number; hit: boolean | null } | null;
  disclaimer: string;
}

interface Scorecard {
  scored: number;
  directional: number;
  hit_rate?: number;
  average_signed_return_pct?: number;
  verdict: string;
}

const RATING_KIND: Record<string, string> = { BUY: "good", OVERWEIGHT: "good", HOLD: "", UNDERWEIGHT: "bad", SELL: "bad" };
const ROLES = [
  "technical analyst",
  "market_structure analyst",
  "news analyst",
  "forecast analyst",
  "bull researcher",
  "bear researcher",
  "research manager",
  "trader",
  "risk team",
  "portfolio manager",
];

function Value({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">—</span>;
    return (
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {value.map((v, i) => <li key={i}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</li>)}
      </ul>
    );
  }
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  if (typeof value === "number") return <>{Number.isInteger(value) ? value : value.toFixed(3)}</>;
  if (typeof value === "object") return <>{JSON.stringify(value)}</>;
  return <>{String(value)}</>;
}

export function DeskPage() {
  const { run, can } = useApp();
  const instruments = useData(() => get<Instrument[]>("/instruments"), []);
  const models = useData(
    () => get<{ default: string | null; presets: { id: string; label: string }[]; switchable: boolean }>("/strategy-lab/models"),
    [],
  );
  const list = useData(() => get<{ reports: Report[]; scorecard: Scorecard }>("/research-desk/reports"), [], 10000);
  const [form, setForm] = useState({ instrument_id: "", interval_seconds: 86400, horizon: 5, debate_rounds: 1, model: "", use_forecast: true });
  const [openId, setOpenId] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  useEffect(() => {
    const all = instruments.data ?? [];
    if (!form.instrument_id && all.length) setForm((f) => ({ ...f, instrument_id: (all.find((i) => i.instrument_id === "OANDA:XAU_USD") ?? all[0]).instrument_id }));
  }, [instruments.data, form.instrument_id]);

  useEffect(() => {
    if (!openId) return;
    let stop = false;
    const load = async () => {
      try {
        const r = await get<Report>(`/research-desk/reports/${openId}`);
        if (stop) return;
        setReport(r);
        if (r.status === "RUNNING") setTimeout(load, 2000);
        else list.reload();
      } catch {
        /* the list shows what exists */
      }
    };
    load();
    return () => {
      stop = true;
    };
  }, [openId]);

  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(() => post<Report>("/research-desk/reports", { ...form, model: form.model || null }), "The desk is working on it");
    if (out) {
      setOpenId(out.report_id);
      list.reload();
    }
  };

  const done = new Set((report?.steps ?? []).map((s) => s.role));
  const card = list.data?.scorecard;
  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>AI research desk <Badge kind="ai">AI</Badge></h1>
      <p className="small muted" style={{ margin: 0 }}>
        Like a trading firm in miniature: analysts read the platform's data, bull and bear researchers debate, a trader
        proposes, a risk team objects, and a portfolio manager gives a rating. It is advice for you to weigh: nothing is
        traded from it, and every rating is scored against what the price then did.
      </p>
      {can("ai.copilot:use") && (
        <Section title="Ask the desk">
          <form className="stack" onSubmit={start}>
            <div className="form-grid">
              <label className="field">
                Instrument
                <select value={form.instrument_id} onChange={(e) => setForm({ ...form, instrument_id: e.target.value })}>
                  {(instruments.data ?? []).map((i) => <option key={i.instrument_id}>{i.instrument_id}</option>)}
                </select>
              </label>
              <label className="field">
                Bars
                <select value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) })}>
                  <option value={3600}>1 hour</option>
                  <option value={14400}>4 hours</option>
                  <option value={86400}>1 day</option>
                </select>
              </label>
              <label className="field">
                Horizon (bars)
                <input type="number" min={1} max={60} value={form.horizon} onChange={(e) => setForm({ ...form, horizon: Number(e.target.value) })} />
              </label>
              <label className="field">
                Debate rounds
                <select value={form.debate_rounds} onChange={(e) => setForm({ ...form, debate_rounds: Number(e.target.value) })}>
                  {[1, 2, 3].map((n) => <option key={n}>{n}</option>)}
                </select>
              </label>
              {models.data?.switchable && (
                <label className="field">
                  AI model
                  <select value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}>
                    <option value="">Default ({models.data.default})</option>
                    {models.data.presets.filter((p) => p.id !== models.data!.default).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                </label>
              )}
            </div>
            <label className="field inline small">
              <input type="checkbox" checked={form.use_forecast} onChange={(e) => setForm({ ...form, use_forecast: e.target.checked })} />
              Include a Kronos forecast as one analyst (when Kronos is installed)
            </label>
            <div className="row end">
              <span className="small muted">About {6 + 2 * form.debate_rounds + (form.use_forecast ? 1 : 0)} model calls; a reasoning model can take a few minutes.</span>
              <button className="primary" type="submit" disabled={!form.instrument_id}>Run the desk</button>
            </div>
          </form>
        </Section>
      )}
      {report && (
        <Section title={`${report.instrument_id} · ${report.model}`} actions={<StatusBadge status={report.status} />}>
          {report.history_source?.startsWith("synthetic") && (
            <div className="alert warn small">Synthetic demo history: this run shows how the desk works and is not scored.</div>
          )}
          {report.status === "RUNNING" && (
            <div className="chips small" style={{ marginBottom: 8 }}>
              {ROLES.map((r) => (
                <span key={r} className={done.has(r) ? "pos" : "muted"}>{done.has(r) ? "✓" : "…"} {r.replace("_", " ")}</span>
              ))}
            </div>
          )}
          {report.status === "FAILED" && <div className="alert warn">{report.error}</div>}
          {report.rating && (
            <div className="card" style={{ boxShadow: "none", marginBottom: 12 }}>
              <div className="row">
                <Badge kind={RATING_KIND[report.rating]}>{report.rating}</Badge>
                <span className="small muted">confidence {pct(report.confidence ?? null, 0)} · horizon {report.horizon} bars</span>
              </div>
              <p style={{ marginBottom: 0 }}>{report.summary}</p>
              <p className="small muted" style={{ marginBottom: 0 }}>{report.disclaimer}</p>
            </div>
          )}
          <ol className="timeline">
            {(report.steps ?? []).map((s, i) => (
              <li key={i}>
                <strong style={{ textTransform: "capitalize" }}>{s.role.replace("_", " ")}</strong>
                <dl className="kv small" style={{ marginTop: 4 }}>
                  {Object.entries(s.output).map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{k.replaceAll("_", " ")}</dt>
                      <dd><Value value={v} /></dd>
                    </div>
                  ))}
                </dl>
              </li>
            ))}
          </ol>
        </Section>
      )}
      <Section title="Track record">
        {card && <p><strong>{card.verdict}</strong>{card.hit_rate != null && ` Direction right ${pct(card.hit_rate, 0)}, average signed move ${signed(card.average_signed_return_pct)}%.`}</p>}
        {(list.data?.reports ?? []).length === 0 ? (
          <Empty>No reports yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Instrument</th><th>Model</th><th>Status</th><th>Rating</th><th>Move after horizon</th><th>Right?</th></tr></thead>
              <tbody>
                {list.data!.reports.map((r) => (
                  <tr key={r.report_id} onClick={() => setOpenId(r.report_id)} style={{ cursor: "pointer" }}>
                    <td className="small">{time(r.requested_at)}</td>
                    <td className="small">{r.instrument_id}</td>
                    <td className="small">{r.model}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td>{r.rating ? <Badge kind={RATING_KIND[r.rating]}>{r.rating}</Badge> : "—"}</td>
                    <td className={tone(r.outcome?.return_pct)}>{r.outcome ? `${signed(r.outcome.return_pct)}%` : "—"}</td>
                    <td>{r.outcome?.hit == null ? "—" : r.outcome.hit ? "yes" : "no"}</td>
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
