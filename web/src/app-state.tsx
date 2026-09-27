import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, get, post, type Account, type Health, type Me } from "./api";
import { Dialog } from "./components/ui";

interface Toast {
  id: number;
  text: string;
  error?: boolean;
}

interface AppState {
  me: Me;
  health: Health | null;
  accounts: Account[];
  refreshAccounts: () => Promise<void>;
  can: (permission: string) => boolean;
  notify: (text: string, error?: boolean) => void;
  /** Run an action; reports errors, asks for an MFA step-up when required and retries once. */
  run: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const value = useContext(Ctx);
  if (!value) throw new Error("useApp outside provider");
  return value;
}

export function describe(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fieldErrors.map((f) => `${f.field}: ${f.message}`).join("; ");
    return fields ? `${error.detail} (${fields})` : `${error.detail || error.code}`;
  }
  return error instanceof Error ? error.message : String(error);
}

export function AppProvider({ me, onLogout, children }: { me: Me; onLogout: () => void; children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [stepUp, setStepUp] = useState<{ reason: string; resolve: (ok: boolean) => void } | null>(null);
  const counter = useRef(0);

  const notify = useCallback((text: string, error = false) => {
    const id = ++counter.current;
    setToasts((t) => [...t.slice(-3), { id, text, error }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 8000 : 4000);
  }, []);

  const can = useCallback((permission: string) => me.permissions.includes(permission), [me]);

  const refreshAccounts = useCallback(async () => {
    if (me.permissions.includes("account:view")) setAccounts(await get<Account[]>("/accounts"));
  }, [me]);

  useEffect(() => {
    const load = () => get<Health>("/health").then(setHealth).catch(() => setHealth(null));
    load();
    refreshAccounts().catch(() => undefined);
    const timer = setInterval(load, 10_000);
    return () => clearInterval(timer);
  }, [refreshAccounts]);

  const askStepUp = (reason: string) => new Promise<boolean>((resolve) => setStepUp({ reason, resolve }));

  const run = useCallback(
    async <T,>(action: () => Promise<T>, success?: string): Promise<T | undefined> => {
      try {
        const result = await action();
        if (success) notify(success);
        return result;
      } catch (error) {
        if (error instanceof ApiError && error.code === "STEP_UP_REQUIRED") {
          if (await askStepUp(error.detail)) {
            try {
              const result = await action();
              if (success) notify(success);
              return result;
            } catch (retryError) {
              notify(describe(retryError), true);
              return undefined;
            }
          }
          return undefined;
        }
        if (error instanceof ApiError && error.code === "MFA_ENROLLMENT_REQUIRED") {
          notify("This action requires multi-factor authentication. Enroll under My account first.", true);
          return undefined;
        }
        notify(describe(error), true);
        return undefined;
      }
    },
    [notify],
  );

  const logout = useCallback(async () => {
    await post("/auth/logout").catch(() => undefined);
    onLogout();
  }, [onLogout]);

  return (
    <Ctx.Provider value={{ me, health, accounts, refreshAccounts, can, notify, run, logout }}>
      {children}
      {stepUp && (
        <StepUpDialog
          reason={stepUp.reason}
          onDone={(ok) => {
            stepUp.resolve(ok);
            setStepUp(null);
          }}
        />
      )}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.error ? "error" : ""}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

function StepUpDialog({ reason, onDone }: { reason: string; onDone: (ok: boolean) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const submit = async () => {
    try {
      await post("/auth/step-up", { code });
      onDone(true);
    } catch (e) {
      setError(describe(e));
    }
  };
  return (
    <Dialog title="Confirm it's you" onClose={() => onDone(false)}>
      <p className="muted">{reason}. Enter the 6-digit code from your authenticator app.</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="field">
          One-time code
          <input autoFocus inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
        </label>
        {error && <div className="alert error">{error}</div>}
        <div className="row end">
          <button type="button" onClick={() => onDone(false)}>
            Cancel
          </button>
          <button className="primary" type="submit">
            Verify
          </button>
        </div>
      </form>
    </Dialog>
  );
}
