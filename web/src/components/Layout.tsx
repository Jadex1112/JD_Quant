import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { get, post, type KillSwitch } from "../api";
import { useApp } from "../app-state";
import { CopilotPanel } from "./CopilotPanel";
import { Dialog, StatusBadge, useData } from "./ui";

const NAV: { group: string; items: { to: string; label: string; permission: string }[] }[] = [
  {
    group: "Trade",
    items: [
      { to: "/", label: "Dashboard", permission: "position:view" },
      { to: "/automation", label: "Bot control", permission: "deployment:view" },
      { to: "/autopilot", label: "AI Autopilot", permission: "autopilot:view" },
      { to: "/trading", label: "Trading", permission: "order:view" },
      { to: "/markets", label: "Markets", permission: "marketdata:view" },
      { to: "/intelligence", label: "Market intelligence", permission: "marketdata:view" },
      { to: "/strategies", label: "Strategies", permission: "deployment:view" },
    ],
  },
  {
    group: "Research",
    items: [
      { to: "/lab", label: "Strategy lab", permission: "backtest:run" },
      { to: "/pipeline", label: "Strategy pipeline", permission: "deployment:view" },
      { to: "/research", label: "Backtests", permission: "backtest:run" },
      { to: "/ai", label: "AI models", permission: "model:view" },
    ],
  },
  {
    group: "Control",
    items: [
      { to: "/risk", label: "Risk", permission: "risk.profile:view" },
      { to: "/connections", label: "Connections", permission: "connection:view" },
      { to: "/admin", label: "Users & audit", permission: "user:view" },
    ],
  },
];

export function Layout({ children }: { children: ReactNode }) {
  const { me, health, can, logout } = useApp();
  const [killOpen, setKillOpen] = useState(false);
  const [copilot, setCopilot] = useState(false);
  const switches = useData(
    () => (can("killswitch:view") ? get<KillSwitch[]>("/kill-switches?active_only=true") : Promise.resolve([])),
    [],
    5000,
  );
  const active = switches.data ?? [];

  const toggleTheme = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("jq-theme", next);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className={`shell ${copilot ? "with-copilot" : ""}`}>
      <header className="topbar">
        <span className="brand">JD Quant AI</span>
        {health ? (
          <span className="row small" title={`API version ${health.version}`}>
            <StatusBadge status={health.mode} />
            {health.maintenance_mode && <StatusBadge status="MAINTENANCE" />}
          </span>
        ) : (
          <StatusBadge status="DOWN" />
        )}
        <span className="spacer" />
        {can("killswitch:trigger") && (
          <button className="danger" onClick={() => setKillOpen(true)} aria-haspopup="dialog">
            ⏻ Kill switch
          </button>
        )}
        {can("ai.copilot:use") && (
          <button onClick={() => setCopilot((v) => !v)} aria-pressed={copilot}>
            ✦ Copilot
          </button>
        )}
        <button onClick={toggleTheme} aria-label="Toggle light and dark theme">◐</button>
        <NavLink to="/account" className="button" style={{ textDecoration: "none" }}>
          {me.display_name}
        </NavLink>
        <button onClick={logout}>Sign out</button>
      </header>
      {active.length > 0 && (
        <div className="banner-ks" role="alert">
          {active.length} kill switch{active.length > 1 ? "es" : ""} active — new orders are blocked in scope.{" "}
          <NavLink to="/risk" style={{ color: "inherit" }}>
            Review
          </NavLink>
        </div>
      )}
      <nav className="sidebar" aria-label="Main">
        {NAV.map((g) => {
          const items = g.items.filter((i) => can(i.permission));
          if (!items.length) return null;
          return (
            <div key={g.group}>
              <div className="group">{g.group}</div>
              {items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === "/"}>
                  {i.label}
                </NavLink>
              ))}
            </div>
          );
        })}
      </nav>
      <main id="main">{children}</main>
      {copilot && <CopilotPanel onClose={() => setCopilot(false)} />}
      {killOpen && <KillSwitchDialog onClose={() => { setKillOpen(false); switches.reload(); }} />}
    </div>
  );
}

function KillSwitchDialog({ onClose }: { onClose: () => void }) {
  const { run, accounts } = useApp();
  const [scope, setScope] = useState("GLOBAL");
  const [action, setAction] = useState("CANCEL_OPEN");
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const done = await run(
      () => post("/kill-switches", { scope, action, reason, target_id: scope === "GLOBAL" ? null : target }),
      "Kill switch triggered",
    );
    if (done) onClose();
  };
  return (
    <Dialog title="Trigger kill switch" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="muted" style={{ margin: 0 }}>
          Takes effect immediately. Releasing it later requires a reason and a fresh MFA check.
        </p>
        <div className="form-grid">
          <label className="field">
            Scope
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="GLOBAL">Everything</option>
              <option value="ACCOUNT">One account</option>
              <option value="STRATEGY">One deployment</option>
              <option value="INSTRUMENT">One instrument</option>
            </select>
          </label>
          <label className="field">
            Action
            <select value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="BLOCK_NEW">Block new orders</option>
              <option value="CANCEL_OPEN">Block and cancel open orders</option>
              <option value="FLATTEN">Block, cancel and flatten positions</option>
            </select>
          </label>
        </div>
        {scope !== "GLOBAL" && (
          <label className="field">
            Target ({scope === "ACCOUNT" ? "account id" : scope === "STRATEGY" ? "deployment id" : "instrument id"})
            {scope === "ACCOUNT" ? (
              <select required value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Choose…</option>
                {accounts.map((a) => (
                  <option key={a.account_id} value={a.account_id}>
                    {a.name} ({a.mode})
                  </option>
                ))}
              </select>
            ) : (
              <input required value={target} onChange={(e) => setTarget(e.target.value)} />
            )}
          </label>
        )}
        <label className="field">
          Reason
          <input required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. unexpected behaviour on BTC strategy" />
        </label>
        <div className="row end">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="danger" type="submit">
            Trigger now
          </button>
        </div>
      </form>
    </Dialog>
  );
}
