import { useState } from "react";
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
  const { can, run, refreshAccounts } = useApp();
  const connections = useData(() => get<Connection[]>("/connections"), [], 5000);
  const [creating, setCreating] = useState(false);
  const [rotating, setRotating] = useState<Connection | null>(null);
  const [watch, setWatch] = useState<Connection | null>(null);
  const [disabling, setDisabling] = useState<Connection | null>(null);
  const [balances, setBalances] = useState<{ c: Connection; rows: Balance[] } | null>(null);

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
                    <td>{c.venue}</td>
                    <td>{c.environment === "PRODUCTION" ? <StatusBadge status="LIVE" /> : c.environment}</td>
                    <td title={c.last_error ?? undefined}>
                      <StatusBadge status={c.status} />
                      {c.last_error && <div className="small muted">{c.last_error}</div>}
                    </td>
                    <td className="small">{c.account_id ?? "—"}</td>
                    <td className="num">{c.instrument_count}</td>
                    <td className="small">{c.watchlist.join(", ") || "—"}</td>
                    <td className="small">{time(c.last_tested_at)}</td>
                    <td>
                      <span className="row">
                        {can("connection:update") && c.status !== "DISABLED" && (
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
                        {can("connection:rotate") && (
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
          onClose={() => {
            setCreating(false);
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

function CreateConnection({ onClose }: { onClose: () => void }) {
  const { run } = useApp();
  const [name, setName] = useState("");
  const [venue, setVenue] = useState("BINANCE");
  const [environment, setEnvironment] = useState("TESTNET");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [baseCurrency, setBaseCurrency] = useState("USDT");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const created = await run(
      () =>
        post<Connection>("/connections", {
          name,
          venue,
          environment,
          api_key: apiKey || null,
          api_secret: apiSecret || null,
          base_currency: baseCurrency,
        }),
      "Connection added",
    );
    setBusy(false);
    if (created) onClose();
  };
  return (
    <Dialog title="Add connection" onClose={onClose}>
      <form className="stack" onSubmit={submit} autoComplete="off">
        <div className="form-grid">
          <label className="field">
            Name
            <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Binance testnet" />
          </label>
          <label className="field">
            Venue
            <select
              value={venue}
              onChange={(e) => {
                setVenue(e.target.value);
                setBaseCurrency(e.target.value === "ALPACA" ? "USD" : "USDT");
              }}
            >
              <option value="BINANCE">Binance Spot</option>
              <option value="ALPACA">Alpaca (US equities)</option>
            </select>
          </label>
          <label className="field">
            Environment
            <select value={environment} onChange={(e) => setEnvironment(e.target.value)}>
              <option value="TESTNET">Testnet / paper</option>
              <option value="PRODUCTION">Production (real money)</option>
            </select>
          </label>
          <label className="field">
            Base currency
            <input value={baseCurrency} onChange={(e) => setBaseCurrency(e.target.value)} />
          </label>
          <label className="field">
            API key <span className="small muted">(optional for market data only)</span>
            <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} spellCheck={false} />
          </label>
          <label className="field">
            API secret
            <input type="password" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} autoComplete="new-password" />
          </label>
        </div>
        {environment === "PRODUCTION" && (
          <div className="alert warn">
            Orders sent through a production connection trade real funds. Risk limits and kill switches still apply.
          </div>
        )}
        <div className="row end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Connecting…" : "Add and test"}
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
