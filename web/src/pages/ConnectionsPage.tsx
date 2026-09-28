import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get, post, put, type Connection } from "../api";
import { useApp } from "../app-state";
import { Confirm, Dialog, Empty, Section, StatusBadge, useData } from "../components/ui";
import { num, time } from "../format";

interface Balance {
  asset: string;
  free: string;
  locked: string;
  total: string;
}

export function ConnectionsPage() {
  const { can, run, refreshAccounts, notify } = useApp();
  const connections = useData(() => get<Connection[]>("/connections"), [], 5000);
  const [creating, setCreating] = useState(false);
  const [rotating, setRotating] = useState<Connection | null>(null);
  const [watch, setWatch] = useState<Connection | null>(null);
  const [disabling, setDisabling] = useState<Connection | null>(null);
  const [balances, setBalances] = useState<{ c: Connection; rows: Balance[] } | null>(null);
  const [signIn, setSignIn] = useState<Connection | null>(null);
  const [pinFor, setPinFor] = useState<Connection | null>(null);
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    // The broker sends the browser back here after sign-in.
    const outcome = params.get("login");
    if (!outcome) return;
    if (outcome === "ok") notify("Signed in to the broker. The connection is live.");
    else notify(`Broker sign-in failed: ${params.get("reason") ?? "unknown error"}`, true);
    setParams({}, { replace: true });
    refreshAccounts().catch(() => undefined);
  }, [params, setParams, notify, refreshAccounts]);

  const done = () => {
    connections.reload();
    refreshAccounts().catch(() => undefined);
  };

  return (
    <div className="stack">
      <h1>Connections</h1>
      <Section
        title="Venues"
        actions={
          can("connection:create") && (
            <button className="primary" onClick={() => setCreating(true)}>
              Add connection
            </button>
          )
        }
      >
        {(connections.data ?? []).length === 0 ? (
          <Empty>
            No venue connections. Paper trading works without one; add Binance or Alpaca (testnet first) to trade for real.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Venue</th>
                  <th>Environment</th>
                  <th>Status</th>
                  <th>Account</th>
                  <th className="num">Instruments</th>
                  <th>Watchlist</th>
                  <th>Last tested</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {(connections.data ?? []).map((c) => (
                  <tr key={c.connection_id}>
                    <td>
                      <strong>{c.name}</strong>
                      {!c.has_credentials && <div className="small muted">market data only</div>}
                    </td>
                    <td>{brokerName(c.venue)}</td>
                    <td>{c.environment === "PRODUCTION" ? <StatusBadge status="LIVE" /> : c.environment}</td>
                    <td title={c.last_error ?? undefined}>
                      <StatusBadge status={c.status} />
                      {c.last_error && <div className="small muted">{c.last_error}</div>}
                    </td>
                    <td className="small">{c.account_id ?? "—"}</td>
                    <td className="num">{c.instrument_count}</td>
                    <td className="small">{c.watchlist.join(", ") || "—"}</td>
                    <td className="small">
                      {time(c.last_tested_at)}
                      {c.requires_login && c.session_expires_at && (
                        <div className="muted">session until {time(c.session_expires_at)}</div>
                      )}
                    </td>
                    <td>
                      <span className="row">
                        {c.requires_login && c.status !== "DISABLED" && can("connection:update") && (
                          <button className={`small ${c.status === "LOGIN_REQUIRED" ? "primary" : ""}`} onClick={() => setSignIn(c)}>
                            {c.status === "LOGIN_REQUIRED" ? "Sign in" : "Sign in again"}
                          </button>
                        )}
                        {c.venue === "FYERS" && c.account_id && can("connection:rotate") && (
                          <button className="small" onClick={() => setPinFor(c)}>PIN</button>
                        )}
                        {can("connection:update") && c.status !== "DISABLED" && c.status !== "LOGIN_REQUIRED" && (
                          <button
                            className="small"
                            onClick={async () => {
                              await run(() => post(`/connections/${c.connection_id}:test`), "Connection tested");
                              done();
                            }}
                          >
                            Test
                          </button>
                        )}
                        {can("connection:update") && c.status !== "DISABLED" && (
                          <button className="small" onClick={() => setWatch(c)}>Watchlist</button>
                        )}
                        {c.account_id && c.has_credentials && (
                          <button
                            className="small"
                            onClick={async () => {
                              const rows = await run(() => get<Balance[]>(`/accounts/${c.account_id}/balances`));
                              if (rows) setBalances({ c, rows });
                            }}
                          >
                            Balances
                          </button>
                        )}
                        {can("connection:rotate") && !c.requires_login && (
                          <button className="small" onClick={() => setRotating(c)}>Rotate keys</button>
                        )}
                        {can("connection:update") && c.status !== "DISABLED" && (
                          <button className="small danger" onClick={() => setDisabling(c)}>Disable</button>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted" style={{ marginBottom: 0 }}>
          API secrets are encrypted at rest and never shown again. Keys with withdrawal permission are refused — create
          trade-only keys at the venue.
        </p>
      </Section>

      {creating && (
        <CreateConnection
          onClose={(created) => {
            setCreating(false);
            done();
            if (created?.requires_login) setSignIn(created);
          }}
        />
      )}
      {signIn && <BrokerSignIn connection={signIn} onClose={() => setSignIn(null)} />}
      {pinFor && (
        <PinDialog
          connection={pinFor}
          onClose={() => {
            setPinFor(null);
            done();
          }}
        />
      )}
      {rotating && (
        <CredentialsDialog
          title={`Rotate keys for ${rotating.name}`}
          onClose={() => setRotating(null)}
          onSubmit={async (api_key, api_secret) => {
            const ok = await run(
              () => post(`/connections/${rotating.connection_id}:rotate-credentials`, { api_key, api_secret }),
              "Credentials rotated",
            );
            if (ok) {
              setRotating(null);
              done();
            }
          }}
        />
      )}
      {watch && (
        <WatchlistDialog
          connection={watch}
          onClose={() => {
            setWatch(null);
            done();
          }}
        />
      )}
      {disabling && (
        <Confirm
          title={`Disable ${disabling.name}?`}
          danger
          confirmLabel="Disable"
          body={<p>Stops polling and blocks new orders on this connection. Existing venue orders are not cancelled.</p>}
          onCancel={() => setDisabling(null)}
          onConfirm={async () => {
            const c = disabling;
            setDisabling(null);
            await run(() => post(`/connections/${c.connection_id}:disable`), "Connection disabled");
            done();
          }}
        />
      )}
      {balances && (
        <Dialog title={`Balances · ${balances.c.name}`} onClose={() => setBalances(null)}>
          {balances.rows.length === 0 ? (
            <Empty>No balances reported.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Asset</th>
                  <th className="num">Free</th>
                  <th className="num">Locked</th>
                  <th className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                {balances.rows.map((b) => (
                  <tr key={b.asset}>
                    <td>{b.asset}</td>
                    <td className="num">{num(b.free)}</td>
                    <td className="num">{num(b.locked)}</td>
                    <td className="num">{num(b.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row end" style={{ marginTop: 12 }}>
            <button onClick={() => setBalances(null)}>Close</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

interface BrokerSpec {
  label: string;
  markets: string;
  region: "India" | "Global";
  currency: string;
  keyLabel: string;
  secretLabel?: string;
  keyHint?: string;
  login?: boolean; // browser sign-in after saving
  product?: boolean; // delivery or intraday
  environments?: [string, string][]; // [value, label], first is the default
  totp?: boolean; // Angel One: client code, PIN and TOTP secret
  optionalKeys?: boolean;
  help: string;
  warn?: string;
}

export const BROKERS: Record<string, BrokerSpec> = {
  FYERS: {
    label: "Fyers", markets: "NSE stocks & ETFs, MCX, currency futures", region: "India", currency: "INR",
    keyLabel: "App ID", keyHint: "e.g. XA1234-100", secretLabel: "Secret key", login: true, product: true,
    help: "Create an app at myapi.fyers.in, then sign in on the next step. With your PIN stored, sessions renew by themselves.",
  },
  KITE: {
    label: "Zerodha Kite", markets: "NSE stocks & ETFs", region: "India", currency: "INR",
    keyLabel: "API key", secretLabel: "API secret", login: true, product: true,
    help: "Create a Kite Connect app at developers.kite.trade and set its redirect URL to this app's callback (shown on the next step). Kite sessions end at 06:00 IST, so sign in each trading day.",
  },
  UPSTOX: {
    label: "Upstox", markets: "NSE stocks & ETFs", region: "India", currency: "INR",
    keyLabel: "API key", secretLabel: "API secret", login: true, product: true,
    help: "Create an app at account.upstox.com/developer/apps with this app's callback as the redirect URL. Upstox sessions end at 03:30 IST, so sign in each trading day.",
  },
  ANGELONE: {
    label: "Angel One", markets: "NSE stocks & ETFs", region: "India", currency: "INR",
    keyLabel: "SmartAPI key", totp: true, product: true,
    help: "Create a SmartAPI app at smartapi.angelone.in and enable TOTP for your account. With the client code, PIN and TOTP secret (all stored encrypted), the platform signs in by itself every day.",
  },
  DHAN: {
    label: "Dhan", markets: "NSE stocks & ETFs", region: "India", currency: "INR",
    keyLabel: "Client ID", secretLabel: "Access token", product: true,
    help: "On web.dhan.co open Profile → DhanHQ Trading APIs and generate an access token. It is valid for 24 hours; paste a new one with Rotate credentials when it expires.",
  },
  OANDA: {
    label: "OANDA", markets: "Spot gold XAU/USD, silver, forex", region: "Global", currency: "USD",
    keyLabel: "API token", keyHint: "Manage API Access in your OANDA hub", secretLabel: "Account ID",
    environments: [["TESTNET", "Practice account (virtual money)"], ["PRODUCTION", "Live account (real money)"]],
    help: "Generate a token under Manage API Access in the OANDA hub; the account ID looks like 101-001-1234567-001.",
    warn: "Indian residents: RBI rules allow forex trading only in INR pairs and EUR/USD, GBP/USD and USD/JPY on recognised Indian exchanges; leveraged forex or gold trading with overseas brokers is not permitted under FEMA. A practice account uses virtual money with live prices — use it to research and paper trade XAU/USD and forex. For real money, use NSE currency futures or MCX gold through Fyers.",
  },
  DELTA: {
    label: "Delta Exchange India", markets: "Crypto perpetual futures (BTC, ETH…)", region: "India", currency: "USD",
    keyLabel: "API key", secretLabel: "API secret",
    environments: [["TESTNET", "Testnet (test funds)"], ["PRODUCTION", "Live (real money)"]],
    help: "Create an API key with trading permission (never withdrawals) at india.delta.exchange → API keys; the testnet has its own keys.",
    warn: "Perpetual futures are leveraged and can be liquidated. The platform caps crypto leverage at 10× and applies your risk limits.",
  },
  BINANCE: {
    label: "Binance", markets: "Crypto spot, tokenized gold (PAXG)", region: "Global", currency: "USDT",
    keyLabel: "API key", secretLabel: "API secret", optionalKeys: true,
    environments: [["TESTNET", "Testnet"], ["PRODUCTION", "Production (real money)"]],
    help: "Keys are optional for market data only. Keys with withdrawal permission are refused.",
  },
  ALPACA: {
    label: "Alpaca", markets: "US stocks", region: "Global", currency: "USD",
    keyLabel: "API key", secretLabel: "API secret",
    environments: [["TESTNET", "Paper"], ["PRODUCTION", "Live (real money)"]],
    help: "Create keys in the Alpaca dashboard (paper and live keys differ).",
  },
};

export function brokerName(venue: string): string {
  return BROKERS[venue]?.label ?? venue;
}

function CreateConnection({ onClose }: { onClose: (created?: Connection) => void }) {
  const { run } = useApp();
  const [venue, setVenue] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState("PRODUCTION");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [clientCode, setClientCode] = useState("");
  const [pin, setPin] = useState("");
  const [totp, setTotp] = useState("");
  const [product, setProduct] = useState("CNC");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<"All" | "India" | "Global">("All");
  const spec = venue ? BROKERS[venue] : null;

  const pick = (v: string) => {
    const b = BROKERS[v];
    setVenue(v);
    setName(b.label);
    setEnvironment(b.environments ? b.environments[0][0] : "PRODUCTION");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!venue || !spec) return;
    setBusy(true);
    const secret = spec.totp ? JSON.stringify({ client_code: clientCode, pin, totp_secret: totp.replaceAll(" ", "") }) : apiSecret;
    const created = await run(
      () =>
        post<Connection>("/connections", {
          name,
          venue,
          environment,
          api_key: apiKey || null,
          api_secret: secret || null,
          base_currency: spec.currency,
          settings: spec.product ? { product } : {},
        }),
      spec.login ? `${spec.label} app saved — sign in to finish` : "Connection added",
    );
    setBusy(false);
    if (created) onClose(created);
  };

  if (!venue || !spec) {
    const shown = Object.entries(BROKERS).filter(([, b]) => filter === "All" || b.region === filter);
    return (
      <Dialog title="Connect a broker" onClose={() => onClose()}>
        <div className="stack">
          <div className="segmented" role="group" aria-label="Region" style={{ alignSelf: "flex-start" }}>
            {(["All", "India", "Global"] as const).map((r) => (
              <button key={r} className="small" aria-pressed={filter === r} onClick={() => setFilter(r)}>
                {r}
              </button>
            ))}
          </div>
          <div className="grid two">
            {shown.map(([v, b]) => (
              <button key={v} className="card" style={{ textAlign: "left", cursor: "pointer" }} onClick={() => pick(v)}>
                <strong>{b.label}</strong>
                <div className="small muted">{b.markets}</div>
                <div className="small muted">{b.login ? "sign in with the broker" : b.totp ? "signs in by itself" : "API keys"}</div>
              </button>
            ))}
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title={`Connect ${spec.label}`} onClose={() => onClose()}>
      <form className="stack" onSubmit={submit} autoComplete="off">
        <div className="row">
          <button type="button" className="link small" onClick={() => setVenue(null)}>← All brokers</button>
          <span className="small muted">{spec.markets}</span>
        </div>
        <div className="form-grid">
          <label className="field">
            Name
            <input required value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          {spec.environments && (
            <label className="field">
              Environment
              <select value={environment} onChange={(e) => setEnvironment(e.target.value)}>
                {spec.environments.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
          )}
          {spec.product && (
            <label className="field">
              Product
              <select value={product} onChange={(e) => setProduct(e.target.value)}>
                <option value="CNC">Delivery (CNC)</option>
                <option value="INTRADAY">Intraday</option>
              </select>
            </label>
          )}
          <label className="field">
            {spec.keyLabel} {spec.keyHint && <span className="small muted">({spec.keyHint})</span>}
            <input
              required={!spec.optionalKeys}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              spellCheck={false}
              autoComplete="off"
              type={venue === "OANDA" ? "password" : "text"}
            />
          </label>
          {spec.totp ? (
            <>
              <label className="field">
                Client code
                <input required value={clientCode} onChange={(e) => setClientCode(e.target.value)} spellCheck={false} />
              </label>
              <label className="field">
                PIN
                <input required type="password" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} autoComplete="new-password" />
              </label>
              <label className="field">
                TOTP secret <span className="small muted">(the key shown when you enabled TOTP)</span>
                <input required type="password" value={totp} onChange={(e) => setTotp(e.target.value)} autoComplete="new-password" spellCheck={false} />
              </label>
            </>
          ) : (
            spec.secretLabel && (
              <label className="field">
                {spec.secretLabel}
                <input
                  required={!spec.optionalKeys}
                  type={venue === "OANDA" ? "text" : "password"}
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  autoComplete="new-password"
                  spellCheck={false}
                />
              </label>
            )
          )}
        </div>
        <div className="alert small">{spec.help}</div>
        {spec.warn && <div className="alert warn small">{spec.warn}</div>}
        {environment === "PRODUCTION" && !spec.login && spec.environments && (
          <div className="alert warn small">Orders through a live connection trade real money. Risk limits and kill switches still apply.</div>
        )}
        <div className="row end">
          <button type="button" onClick={() => onClose()}>Cancel</button>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Connecting…" : spec.login ? "Save and continue" : "Add and test"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function CredentialsDialog({
  title,
  onClose,
  onSubmit,
}: {
  title: string;
  onClose: () => void;
  onSubmit: (key: string, secret: string) => void;
}) {
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  return (
    <Dialog title={title} onClose={onClose}>
      <form
        className="stack"
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(key, secret);
        }}
      >
        <label className="field">
          New API key
          <input required value={key} onChange={(e) => setKey(e.target.value)} spellCheck={false} />
        </label>
        <label className="field">
          New API secret
          <input required type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
        </label>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Rotate</button>
        </div>
      </form>
    </Dialog>
  );
}

function WatchlistDialog({ connection, onClose }: { connection: Connection; onClose: () => void }) {
  const { run } = useApp();
  const [text, setText] = useState(connection.watchlist.join(", "));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const instruments = text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    const ok = await run(() => put(`/connections/${connection.connection_id}/watchlist`, { instruments }), "Watchlist saved");
    if (ok) onClose();
  };
  return (
    <Dialog title={`Watchlist · ${connection.name}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          Instruments to poll for quotes (instrument ids, comma-separated)
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={`${connection.venue}:BTCUSDT`} />
        </label>
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit">Save</button>
        </div>
      </form>
    </Dialog>
  );
}

function BrokerSignIn({ connection, onClose }: { connection: Connection; onClose: () => void }) {
  const { run } = useApp();
  const [login, setLogin] = useState<{ login_url: string; redirect_uri: string } | null>(null);
  const started = useRef(false);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (started.current) return; // one sign-in link per dialog, however often the page re-renders
    started.current = true;
    run(() => post<{ login_url: string; redirect_uri: string }>(`/connections/${connection.connection_id}:login`)).then((out) => {
      if (out) setLogin(out);
      else close.current();
    });
  }, [connection.connection_id, run]);
  return (
    <Dialog title={`Sign in to ${brokerName(connection.venue)}`} onClose={onClose}>
      {!login ? (
        <p className="muted">Preparing sign-in…</p>
      ) : (
        <div className="stack">
          <p style={{ margin: 0 }}>
            In your {brokerName(connection.venue)} app settings, the <strong>Redirect URL</strong> must be exactly:
          </p>
          <code style={{ display: "block", padding: 8, background: "var(--surface-2)", borderRadius: 6, overflowWrap: "anywhere" }}>
            {login.redirect_uri}
          </code>
          <p className="small muted" style={{ margin: 0 }}>
            You will sign in on {brokerName(connection.venue)}&apos;s site and come back here automatically. The link works once and
            expires in 15 minutes.
          </p>
          <div className="row end">
            <button type="button" onClick={onClose}>Cancel</button>
            <a className="button primary" href={login.login_url}>Continue to {brokerName(connection.venue)}</a>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function PinDialog({ connection, onClose }: { connection: Connection; onClose: () => void }) {
  const { run } = useApp();
  const [pin, setPin] = useState("");
  const save = async (value: string | null) => {
    const ok = await run(
      () => put(`/connections/${connection.connection_id}/pin`, { pin: value }),
      value ? "PIN saved — sessions renew automatically" : "PIN removed",
    );
    if (ok) onClose();
  };
  return (
    <Dialog title="Unattended session renewal" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          save(pin);
        }}
      >
        <p className="small" style={{ margin: 0 }}>
          Fyers sessions end every day. With your 4-digit Fyers PIN stored (encrypted), the platform renews the session itself for up to
          15 days, so the autopilot keeps trading without you signing in each morning. Without it, you sign in daily.
        </p>
        <label className="field">
          Fyers PIN
          <input required type="password" inputMode="numeric" pattern="[0-9]{4,6}" value={pin} onChange={(e) => setPin(e.target.value)}
            autoComplete="off" />
        </label>
        <div className="row end">
          <button type="button" onClick={() => save(null)}>Remove stored PIN</button>
          <button className="primary" type="submit">Save PIN</button>
        </div>
      </form>
    </Dialog>
  );
}
