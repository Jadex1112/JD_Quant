import { useEffect, useState } from "react";
import { get, post, put, type KillSwitch, type RiskProfile } from "../api";
import { useApp } from "../app-state";
import { Badge, Dialog, Empty, Section, StatusBadge, useData } from "../components/ui";
import { signed, time, tone } from "../format";

const LIMIT_TYPES = [
  "MAX_ORDER_QUANTITY",
  "MAX_ORDER_NOTIONAL",
  "PRICE_DEVIATION",
  "MAX_POSITION_QUANTITY",
  "MAX_POSITION_NOTIONAL",
  "MAX_OPEN_ORDERS",
  "MAX_ORDER_RATE",
  "MAX_DAILY_LOSS",
];
const BREACH_ACTIONS = ["REJECT", "REDUCE_ONLY", "KILL_SWITCH_BLOCK_NEW", "KILL_SWITCH_CANCEL", "KILL_SWITCH_FLATTEN"];

export function RiskPage() {
  const { can, accounts, run } = useApp();
  const profiles = useData(() => get<RiskProfile[]>("/risk-profiles"), []);
  const switches = useData(
    () => (can("killswitch:view") ? get<KillSwitch[]>("/kill-switches") : Promise.resolve([])),
    [],
    4000,
  );
  const status = useData(
    () =>
      Promise.all(
        accounts.map((a) =>
          get<{ account_id: string; daily_pnl: string; reduce_only: boolean }>(`/risk/status?account_id=${encodeURIComponent(a.account_id)}`),
        ),
      ),
    [accounts],
    5000,
  );
  const [draft, setDraft] = useState<RiskProfile[] | null>(null);
  const [release, setRelease] = useState<KillSwitch | null>(null);
  useEffect(() => {
    if (profiles.data && draft === null) setDraft(structuredClone(profiles.data));
  }, [profiles.data, draft]);

  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(profiles.data);
  const editable = can("risk.limit:approve");
  const update = (pi: number, fn: (p: RiskProfile) => void) =>
    setDraft((d) => {
      if (!d) return d;
      const next = structuredClone(d);
      fn(next[pi]);
      return next;
    });

  const save = async () => {
    if (!draft) return;
    const saved = await run(() => put<RiskProfile[]>("/risk-profiles", draft), "Risk profiles saved");
    if (saved) {
      profiles.setData(saved);
      setDraft(structuredClone(saved));
    }
  };

  return (
    <div className="stack">
      <h1>Risk</h1>
      <div className="grid three">
        {(status.data ?? []).map((s) => (
          <div className="card kpi" key={s.account_id}>
            <span className="label">Today's P&amp;L · {s.account_id}</span>
            <span className={`value ${tone(s.daily_pnl)}`}>{signed(s.daily_pnl)}</span>
            {s.reduce_only && <StatusBadge status="REDUCE_ONLY" />}
          </div>
        ))}
      </div>

      <Section title="Kill switches">
        {(switches.data ?? []).length === 0 ? (
          <Empty>No kill switches have been triggered.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Triggered</th>
                  <th>Scope</th>
                  <th>Action</th>
                  <th>Reason</th>
                  <th>By</th>
                  <th>Status</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {(switches.data ?? []).map((k) => (
                  <tr key={k.kill_switch_id}>
                    <td className="small">{time(k.triggered_at)}</td>
                    <td>
                      {k.scope}
                      {k.target_id && <span className="small muted"> {k.target_id}</span>}
                    </td>
                    <td className="small">{k.action}</td>
                    <td className="small">{k.reason}</td>
                    <td className="small">{k.triggered_by}</td>
                    <td>
                      {k.active ? <Badge kind="bad">Active</Badge> : <span className="small muted">released {time(k.released_at)}</span>}
                    </td>
                    <td>
                      {k.active && can("killswitch:release") && (
                        <button className="small" onClick={() => setRelease(k)}>
                          Release
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title="Risk profiles"
        actions={
          editable && (
            <>
              {dirty && (
                <button onClick={() => setDraft(structuredClone(profiles.data ?? []))}>Discard</button>
              )}
              <button className="primary" disabled={!dirty} onClick={save}>
                Save changes
              </button>
            </>
          )
        }
      >
        {!editable && <p className="small muted">You can view limits. Changing them needs the risk.limit:approve permission.</p>}
        {(draft ?? []).map((p, pi) => (
          <fieldset key={pi} className="card" style={{ boxShadow: "none", marginBottom: 12 }} disabled={!editable}>
            <legend>
              <strong>{p.name}</strong>{" "}
              <span className="small muted">
                {p.scope}
                {p.target_id ? ` · ${p.target_id}` : ""}
              </span>
            </legend>
            <label className="field inline" style={{ marginBottom: 8 }}>
              <input type="checkbox" checked={p.active} onChange={(e) => update(pi, (x) => (x.active = e.target.checked))} /> Active
            </label>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Limit</th>
                    <th>Threshold</th>
                    <th>On breach</th>
                    <th><span className="sr-only">Remove</span></th>
                  </tr>
                </thead>
                <tbody>
                  {p.limits.map((l, li) => (
                    <tr key={li}>
                      <td>
                        <select
                          aria-label="Limit type"
                          value={l.limit_type}
                          onChange={(e) => update(pi, (x) => (x.limits[li].limit_type = e.target.value))}
                        >
                          {LIMIT_TYPES.map((t) => (
                            <option key={t}>{t}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          aria-label="Threshold"
                          inputMode="decimal"
                          value={l.threshold}
                          onChange={(e) => update(pi, (x) => (x.limits[li].threshold = e.target.value))}
                        />
                      </td>
                      <td>
                        <select
                          aria-label="Breach action"
                          value={l.action}
                          onChange={(e) => update(pi, (x) => (x.limits[li].action = e.target.value))}
                        >
                          {BREACH_ACTIONS.map((a) => (
                            <option key={a}>{a}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <button className="small" aria-label="Remove limit" onClick={() => update(pi, (x) => x.limits.splice(li, 1))}>
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button
                className="small"
                onClick={() => update(pi, (x) => x.limits.push({ limit_type: "MAX_ORDER_NOTIONAL", threshold: "10000", action: "REJECT" }))}
              >
                + Add limit
              </button>
              <label className="field inline small">
                Restricted instruments
                <input
                  value={p.restricted_instruments.join(", ")}
                  onChange={(e) =>
                    update(pi, (x) => (x.restricted_instruments = e.target.value.split(",").map((s) => s.trim()).filter(Boolean)))
                  }
                  placeholder="none"
                />
              </label>
            </div>
          </fieldset>
        ))}
        <p className="small muted" style={{ marginBottom: 0 }}>
          Every change is audited with before and after values. Tightening applies immediately to new orders.
        </p>
      </Section>

      {release && (
        <ReleaseDialog
          kill={release}
          onClose={() => {
            setRelease(null);
            switches.reload();
          }}
        />
      )}
    </div>
  );
}

function ReleaseDialog({ kill, onClose }: { kill: KillSwitch; onClose: () => void }) {
  const { run } = useApp();
  const [reason, setReason] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const done = await run(() => post(`/kill-switches/${kill.kill_switch_id}:release`, { reason }), "Kill switch released");
    if (done) onClose();
  };
  return (
    <Dialog title="Release kill switch" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="muted" style={{ margin: 0 }}>
          {kill.scope} {kill.target_id ?? ""} — triggered for “{kill.reason}”. Trading in this scope resumes once released.
          Stopped strategies stay stopped.
        </p>
        <label className="field">
          Reason for release
          <input required value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Release</button>
        </div>
      </form>
    </Dialog>
  );
}
