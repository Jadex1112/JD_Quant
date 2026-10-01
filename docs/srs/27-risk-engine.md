# Chapter 27 – Risk Engine

## 27.1 Purpose

The Risk Management System (RMS) prevents, detects, and responds to exposure that exceeds configured policies. It performs synchronous pre-trade checks on every order (CON-004), continuous post-trade monitoring of positions, P&L, and exposures, computes portfolio risk measures, runs stress tests, and triggers automated protective actions including kill switches.

## 27.2 Scope and Actors

### In Scope

- Risk profiles and limit definitions at multiple scopes
- Pre-trade checks
- Real-time post-trade monitoring
- Drawdown and loss controls
- Margin and liquidation monitoring
- Risk measures (VaR, CVaR, volatility, beta, correlation, concentration)
- Stress testing (delegated scenario execution in Chapter 50)
- Automated risk actions and escalation
- Limit override and exception workflow
- Risk dashboards and reports

### Actors

| Actor | Interaction |
|---|---|
| Risk Manager | Defines profiles and limits, approves exceptions, monitors |
| OMS | Requests pre-trade decisions |
| PME / Position Engine | Provides positions and exposures |
| Trading Engine | Receives kill-switch triggers |
| NCE | Delivers risk alerts |

## 27.3 Domain Entities

### 27.3.1 RiskProfile

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| scope | Enum: WORKSPACE, PORTFOLIO, ACCOUNT, DEPLOYMENT, INSTRUMENT, USER | Required |
| target_id | UUID | Required unless WORKSPACE |
| limits | List of RiskLimit | ≥ 1 |
| status | Enum: DRAFT, ACTIVE, SUSPENDED | Activation requires Risk Manager approval |
| effective_from / effective_to | Timestamp | Optional |

### 27.3.2 RiskLimit

| Attribute | Type | Description |
|---|---|---|
| limit_type | Enum (27.4) | Required |
| threshold | Decimal + unit | Required |
| warning_threshold | Percentage of threshold | Default 80% |
| check_phase | Enum: PRE_TRADE, POST_TRADE, BOTH | Default per type |
| action_on_breach | Enum: REJECT, WARN, REDUCE_ONLY, PAUSE_DEPLOYMENT, KILL_SWITCH_BLOCK_NEW, KILL_SWITCH_CANCEL, KILL_SWITCH_FLATTEN | Required |
| window | Duration or Enum DAY, WEEK, MONTH, ROLLING_N | For time-windowed limits |
| enabled | Boolean | Default true |

### 27.3.3 RiskDecision

| Attribute | Description |
|---|---|
| decision_id | Unique identifier |
| order_id | Evaluated order |
| outcome | APPROVE, REJECT |
| checks | List of (limit_id, limit_type, current_value, projected_value, threshold, result: PASS/WARN/FAIL) |
| reason_code | First failing check code |
| data_quality_flags | e.g. STALE_PRICE used |
| evaluated_at / latency_us | Timing |

### 27.3.4 RiskBreach

| Attribute | Description |
|---|---|
| limit_id, scope, target_id | Breached limit |
| value / threshold | Values at detection |
| severity | WARNING or BREACH |
| action_taken | Action executed |
| status | OPEN, ACKNOWLEDGED, RESOLVED |
| acknowledged_by / resolved_by | Principals |

### 27.3.5 RiskException

| Attribute | Description |
|---|---|
| limit_id | Limit temporarily relaxed |
| override_threshold | Temporary threshold |
| valid_from / valid_to | Maximum duration 7 days |
| justification | Required |
| requested_by / approved_by | Must be different principals (CON-203 waiver applies) |

## 27.4 Limit Types

| Limit Type | Measure | Default Phase |
|---|---|---|
| MAX_ORDER_QUANTITY | Order quantity | PRE_TRADE |
| MAX_ORDER_NOTIONAL | Order notional (BR-21-04) | PRE_TRADE |
| PRICE_DEVIATION | \|limit price − reference price\| / reference price | PRE_TRADE |
| MAX_POSITION_QUANTITY | Projected absolute position per instrument | PRE_TRADE |
| MAX_POSITION_NOTIONAL | Projected absolute position notional per instrument | BOTH |
| MAX_GROSS_EXPOSURE | Projected Σ\|notional\| | BOTH |
| MAX_NET_EXPOSURE | Projected \|Σ signed notional\| | BOTH |
| MAX_LEVERAGE | Projected gross exposure / equity | BOTH |
| MAX_CONCENTRATION | Largest holding weight | BOTH |
| MAX_SECTOR_EXPOSURE / MAX_ASSET_CLASS_EXPOSURE | Exposure by dimension | BOTH |
| MAX_OPEN_ORDERS | Working order count | PRE_TRADE |
| MAX_ORDER_RATE | Orders per second / minute | PRE_TRADE |
| MAX_DAILY_LOSS | Realized + unrealized P&L since day start | POST_TRADE (blocks PRE_TRADE when breached) |
| MAX_DRAWDOWN | Peak-to-trough NAV decline over window | POST_TRADE |
| MAX_CONSECUTIVE_LOSSES | Consecutive losing round trips | POST_TRADE |
| MIN_MARGIN_RATIO | Margin ratio | POST_TRADE |
| MAX_VAR | 1-day VaR at confidence level | POST_TRADE |
| MAX_DAILY_TURNOVER | Traded notional per day | PRE_TRADE |
| RESTRICTED_INSTRUMENT | Instrument on restricted list | PRE_TRADE |
| SELF_TRADE | Order would cross own resting order (CON-046) | PRE_TRADE |
| STALE_DATA | Reference price stale | PRE_TRADE |

## 27.5 Functional Requirements – Risk Profiles

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-27001 | The system shall allow Risk Managers to create risk profiles at every scope listed in 27.3.1 with any combination of the limit types in 27.4. | M | T |
| FR-27002 | The system shall apply all ACTIVE profiles whose scope contains the order or position (workspace, portfolio, account, deployment, instrument, user); the most restrictive applicable limit of each type determines the outcome. | M | T |
| FR-27003 | The system shall require Risk Manager approval to activate, loosen, or disable a limit, and shall allow tightening by any user with risk-configure permission without approval. | M | T |
| FR-27004 | The system shall version risk profiles and retain the full history of limit changes with actor and justification. | M | T |
| FR-27005 | The system shall ship a default workspace risk profile applied to all new accounts, including at least: MAX_ORDER_NOTIONAL, PRICE_DEVIATION (default 5%), MAX_OPEN_ORDERS (default 200), MAX_ORDER_RATE (default 20/s), MAX_DAILY_LOSS, and STALE_DATA. | M | T |
| FR-27006 | The system shall maintain restricted instrument lists at workspace and account scope. | M | T |

## 27.6 Functional Requirements – Pre-Trade Checks

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-27020 | The system shall evaluate every applicable PRE_TRADE limit for every order and return a RiskDecision to the OMS. | M | T |
| FR-27021 | The system shall compute projected values assuming the order fills completely, including all working orders on the same side (conservative projection). | M | T |
| FR-27022 | The system shall return a decision within 1 ms p99 and 250 μs p50 for accounts with up to 1,000 positions and 1,000 working orders. | M | T |
| FR-27023 | The system shall evaluate all checks and report every failing check, using the first failing check in 27.4 order as the reason code. | M | T |
| FR-27024 | The system shall allow orders that strictly reduce absolute position (closing orders) when a limit other than RESTRICTED_INSTRUMENT, PRICE_DEVIATION, or MAX_ORDER_RATE is breached, unless the profile specifies otherwise. | M | T |
| FR-27025 | The system shall reject all non-reducing orders for a scope in REDUCE_ONLY state. | M | T |
| FR-27026 | The system shall reject orders with `RISK_STALE_DATA` when the reference price is STALE, except reduce-only orders when configured. | M | T |
| FR-27027 | The system shall persist every RiskDecision and link it to the order (FR-21022). | M | T |
| FR-27028 | The system shall maintain pre-trade state (positions, working orders, daily P&L, counters) in memory, updated from OMS and Position Engine events, so that pre-trade checks require no synchronous external calls (CON-100). | M | I |

## 27.7 Functional Requirements – Post-Trade Monitoring

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-27040 | The system shall evaluate POST_TRADE limits on every fill, every NAV update, and at a configurable interval (default 1 s). | M | T |
| FR-27041 | The system shall compute daily P&L from the configured day-start time (default 00:00 UTC, configurable per profile) including realized P&L, unrealized P&L change, fees, and funding. | M | T |
| FR-27042 | The system shall compute drawdown as (peak NAV − current NAV) / peak NAV, with the peak tracked over the configured window. | M | T |
| FR-27043 | The system shall raise a WARNING when a value reaches the warning threshold and a BREACH when it reaches the threshold, each at most once per crossing (hysteresis: re-arm when the value falls 5% of threshold below). | M | T |
| FR-27044 | The system shall execute the configured action on breach within 100 ms of detection. | M | T |
| FR-27045 | The system shall monitor margin ratio for margin and derivatives accounts and raise escalating alerts at configurable levels (default 150%, 125%, 110% of maintenance) and execute the configured action at the lowest level. | M | T |
| FR-27046 | The system shall compute a liquidation price per derivatives position and display distance to liquidation. | M | T |
| FR-27047 | The system shall require acknowledgment of every BREACH by an authorized user; unacknowledged breaches shall be re-notified at a configurable interval (default 15 min). | M | T |

## 27.8 Functional Requirements – Risk Measures

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-27060 | The system shall compute portfolio Value at Risk at 95% and 99% confidence for 1-day and 10-day horizons using historical simulation (default 250-day lookback) and parametric methods. | M | T |
| FR-27061 | The system shall compute Conditional VaR (Expected Shortfall) at the same levels. | M | T |
| FR-27062 | The system shall compute realized volatility, beta to benchmark, and the correlation matrix of holdings over configurable lookbacks. | M | T |
| FR-27063 | The system shall compute marginal and component VaR per holding and per strategy. | S | T |
| FR-27064 | The system shall recompute risk measures at least every 5 minutes intraday and at end of day. | M | T |
| FR-27065 | The system shall backtest VaR models by counting exceptions over the trailing 250 days and flag models whose exception count falls in the red zone of the traffic-light test. | S | T |

## 27.9 Functional Requirements – Exceptions, Dashboards, and Reports

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-27080 | The system shall support temporary risk exceptions per 27.3.5, automatically reverting at expiry and notifying the requester 1 hour before expiry. | M | T |
| FR-27081 | The system shall provide a real-time risk dashboard showing, per scope, each limit's current utilization (value / threshold) with color coding (green < warning, amber ≥ warning, red ≥ threshold). | M | D |
| FR-27082 | The system shall provide a breach log with filtering by scope, limit type, severity, and status. | M | T |
| FR-27083 | The system shall generate a daily risk report per workspace (Chapter 37). | M | T |
| FR-27084 | The system shall provide a what-if pre-trade check that evaluates a hypothetical order without submitting it. | M | T |

## 27.10 Business Rules

| ID | Rule |
|---|---|
| BR-27-01 | The RMS fails closed: if the RMS cannot evaluate an order (internal error, missing data other than handled staleness), the order is rejected with `RISK_EVALUATION_ERROR`. |
| BR-27-02 | Limits are evaluated in the account's base currency; cross-currency values are converted at the latest FX rate. |
| BR-27-03 | Breached MAX_DAILY_LOSS places the scope in REDUCE_ONLY until the next day start or a Risk Manager release. |
| BR-27-04 | A risk exception can never raise a limit above the workspace's hard ceiling configured by the System Administrator. |
| BR-27-05 | Historical VaR = − percentile(portfolio P&L scenarios, 1 − confidence), where scenarios apply historical daily returns to current holdings. |

## 27.11 Events

| Event | Description |
|---|---|
| `risk.decision` | Every pre-trade decision (high volume; sampled for UI) |
| `risk.warning.detected` | Warning threshold reached |
| `risk.breach.detected` | Breach threshold reached, with action taken |
| `risk.breach.acknowledged` / `.resolved` | Breach lifecycle |
| `risk.profile.changed` | Profile or limit changed |
| `risk.measures.updated` | VaR and related measures recomputed |

## 27.12 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-27001 | Given a MAX_POSITION_QUANTITY of 10 and a current position of 8 with a working buy of 1, when a buy of 2 is submitted, then it is rejected (projected 11). | FR-27021 |
| AC-27002 | Given a MAX_DAILY_LOSS of 500 and a realized loss of 510, then the account enters REDUCE_ONLY, a BREACH is raised, and new opening orders are rejected. | FR-27041, BR-27-03 |
| AC-27003 | Given 10,000 pre-trade checks at 1,000/s, then p99 decision latency is ≤ 1 ms. | FR-27022 |
| AC-27004 | Given the RMS throws an internal error while evaluating an order, then the order is rejected. | BR-27-01 |
| AC-27005 | Given a drawdown limit with action KILL_SWITCH_FLATTEN, when breached, then the kill switch is triggered and positions are flattened. | FR-27044, FR-19043 |

---

*End of Chapter 27 – Risk Engine*
