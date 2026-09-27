import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ApiError, get, onUnauthenticated, post, type Me } from "./api";
import { AppProvider, describe } from "./app-state";
import { Layout } from "./components/Layout";
import { AccountPage } from "./pages/AccountPage";
import { AdminPage } from "./pages/AdminPage";
import { AiPage } from "./pages/AiPage";
import { AutopilotPage } from "./pages/AutopilotPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { Dashboard } from "./pages/Dashboard";
import { MarketsPage } from "./pages/MarketsPage";
import { LabPage } from "./pages/LabPage";
import { ResearchPage } from "./pages/ResearchPage";
import { RiskPage } from "./pages/RiskPage";
import { StrategiesPage } from "./pages/StrategiesPage";
import { TradingPage } from "./pages/TradingPage";

type AuthState = { kind: "loading" } | { kind: "setup" } | { kind: "login"; message?: string } | { kind: "in"; me: Me };

export function App() {
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });

  const check = useCallback(async () => {
    try {
      setAuth({ kind: "in", me: await get<Me>("/me") });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        const setup = await get<{ setup_required: boolean }>("/setup").catch(() => ({ setup_required: false }));
        setAuth(setup.setup_required ? { kind: "setup" } : { kind: "login" });
      } else {
        setAuth({ kind: "login", message: describe(e) });
      }
    }
  }, []);

  useEffect(() => {
    check();
    return onUnauthenticated(() => setAuth({ kind: "login", message: "Your session ended. Please sign in again." }));
  }, [check]);

  if (auth.kind === "loading") return <div className="auth-page muted">Loading…</div>;
  if (auth.kind === "setup") return <SetupPage onDone={() => setAuth({ kind: "login", message: "Owner account created. Sign in." })} />;
  if (auth.kind === "login") return <LoginPage message={auth.message} onDone={check} />;

  return (
    <AppProvider me={auth.me} onLogout={() => setAuth({ kind: "login" })}>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/autopilot" element={<AutopilotPage />} />
          <Route path="/trading" element={<TradingPage />} />
          <Route path="/markets" element={<MarketsPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/lab" element={<LabPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/ai" element={<AiPage />} />
          <Route path="/connections" element={<ConnectionsPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </AppProvider>
  );
}

function LoginPage({ message, onDone }: { message?: string; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await post("/auth/login", { email, password, code: needsCode ? code : null });
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.code === "MFA_REQUIRED") {
        setNeedsCode(true);
      } else {
        setError(describe(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <form className="card auth-card stack" onSubmit={submit} aria-labelledby="login-title">
        <div>
          <h1 id="login-title">JD Quant AI</h1>
          <p className="muted" style={{ margin: 0 }}>Sign in to continue</p>
        </div>
        {message && <div className="alert">{message}</div>}
        <label className="field">
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {needsCode && (
          <label className="field">
            One-time code (or a recovery code)
            <input autoFocus autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
        )}
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function SetupPage({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ email: "", display_name: "", password: "", confirm: "" });
  const [error, setError] = useState("");
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (form.password !== form.confirm) return setError("Passwords do not match.");
    try {
      await post("/setup", { email: form.email, display_name: form.display_name, password: form.password });
      onDone();
    } catch (err) {
      setError(describe(err));
    }
  };
  return (
    <div className="auth-page">
      <form className="card auth-card stack" onSubmit={submit} aria-labelledby="setup-title">
        <div>
          <h1 id="setup-title">Welcome to JD Quant AI</h1>
          <p className="muted" style={{ margin: 0 }}>
            Create the owner account. It receives every role; you can add other users later.
          </p>
        </div>
        <label className="field">
          Name
          <input required value={form.display_name} onChange={set("display_name")} />
        </label>
        <label className="field">
          Email
          <input type="email" required autoComplete="username" value={form.email} onChange={set("email")} />
        </label>
        <label className="field">
          Password (12+ characters)
          <input type="password" required minLength={12} autoComplete="new-password" value={form.password} onChange={set("password")} />
        </label>
        <label className="field">
          Confirm password
          <input type="password" required autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />
        </label>
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="primary" type="submit">Create owner account</button>
      </form>
    </div>
  );
}
