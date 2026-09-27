import { useState } from "react";
import { del, get, post } from "../api";
import { useApp } from "../app-state";
import { Dialog, Empty, Section, useData } from "../components/ui";
import { time } from "../format";

interface SessionRow {
  session_id: string;
  created_at: string;
  last_activity_at: string;
  ip_address: string;
  user_agent: string;
  current: boolean;
}

interface ApiKey {
  key_id: string;
  name: string;
  scopes: string[];
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
}

export function AccountPage() {
  const { me, notify } = useApp();
  return (
    <div className="stack">
      <h1>My account</h1>
      <Section title="Profile">
        <dl className="small" style={{ margin: 0, display: "grid", gridTemplateColumns: "max-content 1fr", gap: "4px 16px" }}>
          <dt className="muted">Name</dt>
          <dd style={{ margin: 0 }}>{me.display_name}</dd>
          <dt className="muted">Email</dt>
          <dd style={{ margin: 0 }}>{me.email}</dd>
          <dt className="muted">Roles</dt>
          <dd style={{ margin: 0 }}>{me.roles.join(", ")}</dd>
          <dt className="muted">Signed in with</dt>
          <dd style={{ margin: 0 }}>{me.auth_method}</dd>
        </dl>
      </Section>
      <div className="grid two" style={{ alignItems: "start" }}>
        <MfaSection />
        <PasswordSection onChanged={() => notify("Password changed. All sessions were signed out — sign in with the new password.")} />
      </div>
      <SessionsSection />
      <ApiKeysSection />
    </div>
  );
}

function MfaSection() {
  const { me, run } = useApp();
  const [enroll, setEnroll] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [enabled, setEnabled] = useState(me.mfa_enabled);

  const start = async () => {
    const out = await run(() => post<{ secret: string; provisioning_uri: string }>("/me/mfa"));
    if (out) setEnroll(out);
  };
  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(() => post<{ recovery_codes: string[] }>("/me/mfa:confirm", { code }), "Two-factor authentication enabled");
    if (out) {
      setEnroll(null);
      setCodes(out.recovery_codes);
      setEnabled(true);
    }
  };

  return (
    <Section title="Two-factor authentication">
      {enabled ? (
        <p style={{ marginTop: 0 }}>
          ✓ Enabled. You'll be asked for a code at sign-in and before sensitive actions such as releasing a kill switch.
        </p>
      ) : enroll ? (
        <form className="stack" onSubmit={confirm}>
          <p className="small" style={{ margin: 0 }}>
            Add this key to your authenticator app (Google Authenticator, 1Password, …), then enter the 6-digit code it shows.
          </p>
          <code style={{ fontSize: 16, letterSpacing: 1, overflowWrap: "anywhere" }}>{enroll.secret}</code>
          <a className="small" href={enroll.provisioning_uri}>Open in authenticator app</a>
          <label className="field">
            Code
            <input required inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <div className="row end">
            <button className="primary" type="submit">Verify and enable</button>
          </div>
        </form>
      ) : (
        <>
          <p style={{ marginTop: 0 }}>
            Not enabled. Privileged actions — releasing kill switches, approving live strategies, changing risk limits, managing
            venue keys — require it.
          </p>
          <button className="primary" onClick={start}>Set up two-factor authentication</button>
        </>
      )}
      {codes && (
        <Dialog title="Save your recovery codes" onClose={() => setCodes(null)}>
          <p className="small">Each code works once if you lose your authenticator. They won't be shown again.</p>
          <pre style={{ columns: 2 }}>{codes.join("\n")}</pre>
          <div className="row end">
            <button
              onClick={() => {
                navigator.clipboard?.writeText(codes.join("\n")).catch(() => undefined);
              }}
            >
              Copy
            </button>
            <button className="primary" onClick={() => setCodes(null)}>I've saved them</button>
          </div>
        </Dialog>
      )}
    </Section>
  );
}

function PasswordSection({ onChanged }: { onChanged: () => void }) {
  const { run } = useApp();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const mismatch = repeat.length > 0 && next !== repeat;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = await run(async () => {
      await post("/me/password", { current_password: current, new_password: next });
      return true;
    });
    if (ok) {
      setCurrent("");
      setNext("");
      setRepeat("");
      onChanged();
    }
  };
  return (
    <Section title="Password">
      <form className="stack" onSubmit={submit}>
        <label className="field">
          Current password
          <input required type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field">
          New password (12+ characters)
          <input required minLength={12} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label className="field">
          Repeat new password
          <input required type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} aria-invalid={mismatch} />
        </label>
        {mismatch && <div className="small neg">Passwords don't match.</div>}
        <div className="row end">
          <button className="primary" type="submit" disabled={mismatch}>Change password</button>
        </div>
      </form>
    </Section>
  );
}

function SessionsSection() {
  const { run } = useApp();
  const sessions = useData(() => get<SessionRow[]>("/me/sessions"), []);
  return (
    <Section title="Active sessions">
      {(sessions.data ?? []).length === 0 ? (
        <Empty>No sessions.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Started</th>
                <th>Last active</th>
                <th>IP address</th>
                <th>Browser</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {(sessions.data ?? []).map((s) => (
                <tr key={s.session_id}>
                  <td className="small">{time(s.created_at)}</td>
                  <td className="small">{time(s.last_activity_at)}</td>
                  <td className="small">{s.ip_address}</td>
                  <td className="small" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.user_agent}>
                    {s.user_agent}
                  </td>
                  <td>
                    {s.current ? (
                      <span className="small muted">this session</span>
                    ) : (
                      <button
                        className="small"
                        onClick={async () => {
                          await run(async () => {
                            await del(`/me/sessions/${s.session_id}`);
                            return true;
                          }, "Session signed out");
                          sessions.reload();
                        }}
                      >
                        Sign out
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
  );
}

const SCOPES = [
  { value: "marketdata:view", label: "Read market data" },
  { value: "position:view", label: "Read positions" },
  { value: "order:view", label: "Read orders" },
  { value: "order:create", label: "Place orders" },
  { value: "order:cancel", label: "Cancel orders" },
  { value: "backtest:run", label: "Run backtests" },
];

function ApiKeysSection() {
  const { run, can } = useApp();
  const keys = useData(() => get<ApiKey[]>("/api-keys"), []);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["marketdata:view", "position:view"]);
  const [days, setDays] = useState("90");
  const [created, setCreated] = useState<string | null>(null);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const out = await run(() => post<ApiKey & { api_key: string }>("/api-keys", { name, scopes, days: Number(days) }), "API key created");
    if (out) {
      setCreated(out.api_key);
      setName("");
      keys.reload();
    }
  };
  return (
    <Section title="API keys">
      <p className="small muted" style={{ marginTop: 0 }}>
        For scripts and integrations. Send as the <code>X-API-Key</code> header. Keys can never perform privileged actions and
        only get scopes you hold yourself.
      </p>
      <form className="row" onSubmit={create} style={{ marginBottom: 12, alignItems: "end" }}>
        <label className="field">
          Name
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="research notebook" />
        </label>
        <label className="field">
          Expires in (days)
          <input inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} style={{ width: 90 }} />
        </label>
        <fieldset className="row" style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="sr-only">Scopes</legend>
          {SCOPES.filter((s) => can(s.value)).map((s) => (
            <label key={s.value} className="field inline small">
              <input
                type="checkbox"
                checked={scopes.includes(s.value)}
                onChange={(e) => setScopes(e.target.checked ? [...scopes, s.value] : scopes.filter((x) => x !== s.value))}
              />{" "}
              {s.label}
            </label>
          ))}
        </fieldset>
        <button className="primary" type="submit" disabled={!scopes.length}>
          Create key
        </button>
      </form>
      {(keys.data ?? []).length === 0 ? (
        <Empty>No API keys.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Scopes</th>
                <th>Created</th>
                <th>Expires</th>
                <th>Last used</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {(keys.data ?? []).map((k) => (
                <tr key={k.key_id} className={k.revoked_at ? "muted" : ""}>
                  <td>{k.name}</td>
                  <td className="small">{k.scopes.join(", ")}</td>
                  <td className="small">{time(k.created_at)}</td>
                  <td className="small">{time(k.expires_at)}</td>
                  <td className="small">{time(k.last_used_at)}</td>
                  <td>
                    {k.revoked_at ? (
                      <span className="small">revoked</span>
                    ) : (
                      <button
                        className="small danger"
                        onClick={async () => {
                          await run(async () => {
                            await del(`/api-keys/${k.key_id}`);
                            return true;
                          }, "API key revoked");
                          keys.reload();
                        }}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {created && (
        <Dialog title="Copy your API key" onClose={() => setCreated(null)}>
          <p className="small">This is the only time the key is shown.</p>
          <code style={{ display: "block", overflowWrap: "anywhere", padding: 8, background: "var(--surface-2)", borderRadius: 6 }}>{created}</code>
          <div className="row end" style={{ marginTop: 12 }}>
            <button onClick={() => navigator.clipboard?.writeText(created).catch(() => undefined)}>Copy</button>
            <button className="primary" onClick={() => setCreated(null)}>Done</button>
          </div>
        </Dialog>
      )}
    </Section>
  );
}
