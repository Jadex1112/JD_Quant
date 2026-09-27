import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      // With stacked dialogs (e.g. the MFA step-up over a form) Escape closes only the topmost one.
      const dialogs = document.querySelectorAll(".dialog");
      if (e.key === "Escape" && dialogs[dialogs.length - 1] === ref.current) onClose();
    };
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Confirm({
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog title={title} onClose={onCancel}>
      <div className="stack">
        <div>{body}</div>
        <div className="row end">
          <button onClick={onCancel}>Cancel</button>
          <button className={danger ? "danger" : "primary"} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

export function Badge({ kind, children }: { kind?: string; children: ReactNode }) {
  return <span className={`badge ${kind ?? ""}`}>{children}</span>;
}

const GOOD = new Set(["FILLED", "RUNNING", "ACTIVE", "CONNECTED", "LIVE", "OPERATIONAL", "PRODUCTION", "NORMAL", "SUCCESS"]);
const BAD = new Set([
  "REJECTED", "RISK_REJECTED", "FAILED", "HALTED", "SUSPENDED", "AUTH_FAILED", "STALE", "DOWN", "DISABLED",
  "DENIED", "FAILURE", "SAFE",
]);
const WARN = new Set(["PARTIALLY_FILLED", "PAUSED", "PENDING_CANCEL", "PENDING_REPLACE", "UNKNOWN", "DEGRADED", "SHADOW", "RECOVERING", "STAGING"]);

export function StatusBadge({ status }: { status: string }) {
  const kind = GOOD.has(status) ? "good" : BAD.has(status) ? "bad" : WARN.has(status) ? "warn" : "";
  return <Badge kind={kind}>{status.replaceAll("_", " ")}</Badge>;
}

export function ModeBadge({ mode }: { mode: string }) {
  return <Badge kind={mode === "LIVE" ? "live" : "paper"}>{mode === "LIVE" ? "● LIVE" : "◌ PAPER"}</Badge>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Load data, reload on demand and optionally poll. */
export function useData<T>(loader: () => Promise<T>, deps: unknown[], pollMs?: number) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const load = useCallback(loader, deps);
  const reload = useCallback(async () => {
    try {
      setData(await load());
      setError(undefined);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [load]);
  useEffect(() => {
    reload();
    if (!pollMs) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, pollMs);
    return () => clearInterval(timer);
  }, [reload, pollMs]);
  return { data, error, loading, reload, setData };
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        {actions && <div className="row">{actions}</div>}
      </div>
      {children}
    </section>
  );
}
