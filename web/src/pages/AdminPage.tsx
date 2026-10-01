import { useState } from "react";
import { get, post, put, type AuditEvent, type User } from "../api";
import { useApp } from "../app-state";
import { Dialog, Empty, Section, StatusBadge, useData } from "../components/ui";
import { time } from "../format";

interface Role {
  role: string;
  permissions: string[];
  privileged: string[];
}

export function AdminPage() {
  const { can, run, me } = useApp();
  const users = useData(() => get<User[]>("/users"), []);
  const roles = useData(() => (can("role:view") ? get<Role[]>("/roles") : Promise.resolve([])), []);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);

  const setStatus = async (u: User, status: string) => {
    await run(() => put(`/users/${u.user_id}/status`, { status }), `${u.display_name}: ${status.toLowerCase()}`);
    users.reload();
  };

  return (
    <div className="stack">
      <h1>Users &amp; audit</h1>
      <Section
        title="Users"
        actions={
          can("user:create") && (
            <button className="primary" onClick={() => setCreating(true)}>
              Add user
            </button>
          )
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Roles</th>
                <th>MFA</th>
                <th>Status</th>
                <th>Last login</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((u) => (
                <tr key={u.user_id}>
                  <td>{u.display_name}</td>
                  <td className="small">{u.email}</td>
                  <td className="small">{u.roles.join(", ")}</td>
                  <td>{u.mfa_enabled ? "✓" : <span className="muted">off</span>}</td>
                  <td><StatusBadge status={u.status} /></td>
                  <td className="small">{time(u.last_login_at)}</td>
                  <td>
                    <span className="row">
                      {can("role:assign") && (
                        <button className="small" onClick={() => setEditing(u)}>Roles</button>
                      )}
                      {can("user:suspend") && u.user_id !== me.user_id && (
                        u.status === "ACTIVE" ? (
                          <button className="small danger" onClick={() => setStatus(u, "SUSPENDED")}>Suspend</button>
                        ) : (
                          <button className="small" onClick={() => setStatus(u, "ACTIVE")}>Reactivate</button>
                        )
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {can("audit:view") && <AuditLog />}

      {creating && (
        <UserDialog
          roles={roles.data ?? []}
          onClose={() => {
            setCreating(false);
            users.reload();
          }}
        />
      )}
      {editing && (
        <UserDialog
          user={editing}
          roles={roles.data ?? []}
          onClose={() => {
            setEditing(null);
            users.reload();
          }}
        />
      )}
    </div>
  );
}

function UserDialog({ user, roles, onClose }: { user?: User; roles: Role[]; onClose: () => void }) {
  const { run } = useApp();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<string[]>(user?.roles ?? ["VIEWER"]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = user
      ? await run(() => put(`/users/${user.user_id}/roles`, { roles: selected }), "Roles updated")
      : await run(() => post("/users", { email, display_name: name, password, roles: selected }), "User created");
    if (ok) onClose();
  };
  return (
    <Dialog title={user ? `Roles for ${user.display_name}` : "Add user"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {!user && (
          <div className="form-grid">
            <label className="field">
              Email
              <input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              Display name
              <input required value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              Initial password (12+ characters)
              <input required type="password" minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
            </label>
          </div>
        )}
        <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
          <legend className="small muted" style={{ marginBottom: 6 }}>Roles</legend>
          <div className="stack" style={{ gap: 4, maxHeight: 300, overflow: "auto" }}>
            {roles.map((r) => (
              <label key={r.role} className="field inline" title={r.permissions.join(", ")}>
                <input
                  type="checkbox"
                  checked={selected.includes(r.role)}
                  onChange={(e) => setSelected(e.target.checked ? [...selected, r.role] : selected.filter((x) => x !== r.role))}
                />{" "}
                {r.role}
                <span className="small muted">
                  {" "}
                  {r.permissions.length} permissions{r.privileged.length ? `, ${r.privileged.length} privileged` : ""}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <p className="small muted" style={{ margin: 0 }}>Role changes take effect on the user's next request and are audited.</p>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit" disabled={!selected.length}>
            Save
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function AuditLog() {
  const { can, run, notify } = useApp();
  const [filters, setFilters] = useState({ actor: "", action: "", category: "" });
  const [applied, setApplied] = useState(filters);
  const events = useData(() => {
    const q = new URLSearchParams({ limit: "200" });
    for (const [k, v] of Object.entries(applied)) if (v) q.set(k, v);
    return get<AuditEvent[]>(`/audit-events?${q}`);
  }, [applied]);

  const verify = async () => {
    const out = await run(() => post<{ valid: boolean; first_broken_seq: number | null }>("/audit-events:verify"));
    if (out) notify(out.valid ? "Audit chain verified: no tampering detected" : `Audit chain broken at #${out.first_broken_seq}`, !out.valid);
  };

  return (
    <Section title="Audit log" actions={can("audit:verify") && <button onClick={verify}>Verify integrity</button>}>
      <form
        className="row"
        style={{ marginBottom: 12 }}
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(filters);
        }}
      >
        {(["actor", "action", "category"] as const).map((k) => (
          <label key={k} className="field inline small">
            {k}
            <input value={filters[k]} onChange={(e) => setFilters({ ...filters, [k]: e.target.value })} />
          </label>
        ))}
        <button type="submit">Search</button>
      </form>
      {(events.data ?? []).length === 0 ? (
        <Empty>No matching events.</Empty>
      ) : (
        <div className="table-wrap" style={{ maxHeight: 480 }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Category</th>
                <th>Target</th>
                <th>Outcome</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {(events.data ?? []).map((e) => (
                <tr key={e.seq}>
                  <td className="small">{e.seq}</td>
                  <td className="small">{time(e.at)}</td>
                  <td className="small">{e.actor}</td>
                  <td>{e.action}</td>
                  <td className="small">{e.category}</td>
                  <td className="small">{e.target ?? "—"}</td>
                  <td><StatusBadge status={e.outcome} /></td>
                  <td className="small muted" style={{ maxWidth: 320, overflowWrap: "anywhere" }}>
                    {Object.keys(e.data).length ? JSON.stringify(e.data) : ""}
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
