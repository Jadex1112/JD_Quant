import { NavLink } from "react-router-dom";
import { get, type Fill, type Order, type Position } from "../api";
import { useApp } from "../app-state";
import { FillsTable, OrdersTable, PositionsTable } from "../components/tables";
import { ModeBadge, Section, StatusBadge, useData } from "../components/ui";
import { signed, tone } from "../format";

export function Dashboard() {
  const { me, accounts, can } = useApp();
  const positions = useData(() => get<Position[]>("/positions"), [], 3000);
  const orders = useData(
    () => (can("order:view") ? get<Order[]>("/orders?working_only=true") : Promise.resolve([])),
    [],
    3000,
  );
  const fills = useData(() => (can("order:view") ? get<Fill[]>("/fills?limit=10") : Promise.resolve([])), [], 5000);

  const unrealized = (positions.data ?? []).reduce((sum, p) => sum + Number(p.unrealized_pnl ?? 0), 0);
  const realized = (positions.data ?? []).reduce((sum, p) => sum + Number(p.realized_pnl), 0);

  return (
    <div className="stack">
      <h1>Good to see you, {me.display_name.split(" ")[0]}</h1>
      <div className="grid three">
        <div className="card kpi">
          <span className="label">Open positions</span>
          <span className="value">{positions.data?.length ?? "—"}</span>
        </div>
        <div className="card kpi">
          <span className="label">Working orders</span>
          <span className="value">{orders.data?.length ?? "—"}</span>
        </div>
        <div className="card kpi">
          <span className="label">Unrealized P&amp;L (open positions)</span>
          <span className={`value ${tone(unrealized)}`}>{signed(unrealized)}</span>
        </div>
        <div className="card kpi">
          <span className="label">Realized P&amp;L (open positions)</span>
          <span className={`value ${tone(realized)}`}>{signed(realized)}</span>
        </div>
      </div>

      <Section title="Accounts">
        {accounts.length === 0 ? (
          <p className="muted">
            No accounts yet. {can("connection:create") && <NavLink to="/connections">Connect a venue</NavLink>}
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Venue</th>
                  <th>Mode</th>
                  <th>Currency</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.account_id}>
                    <td>{a.name} <span className="small muted">{a.account_id}</span></td>
                    <td>{a.venue}</td>
                    <td><ModeBadge mode={a.mode} /></td>
                    <td>{a.base_currency}</td>
                    <td><StatusBadge status={a.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Positions">
        <PositionsTable positions={positions.data ?? []} />
      </Section>
      {can("order:view") && (
        <div className="grid two">
          <Section title="Working orders" actions={<NavLink to="/trading">Open trading</NavLink>}>
            <OrdersTable orders={orders.data ?? []} onChange={orders.reload} compact />
          </Section>
          <Section title="Recent fills">
            <FillsTable fills={fills.data ?? []} />
          </Section>
        </div>
      )}
    </div>
  );
}
