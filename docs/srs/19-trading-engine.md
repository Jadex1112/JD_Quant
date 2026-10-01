# Chapter 19 – Trading Engine

## 19.1 Purpose

The Trading Engine (Live Trading Engine, LTE) orchestrates the operation of trading strategies against trading accounts. It is responsible for binding strategies to accounts and execution modes, controlling the trading lifecycle of each deployment, enforcing trading sessions, coordinating global trading controls such as kill switches and trading halts, and maintaining the authoritative operational state of all active trading activity.

The Trading Engine does not generate trading decisions (Strategy Framework, Chapter 24), manage order state (Order Management, Chapter 21), route orders to venues (Execution Engine, Chapter 22), or evaluate risk (Risk Engine, Chapter 27). It coordinates these engines.

## 19.2 Scope and Actors

### In Scope

- Trading account registration and mode binding
- Strategy deployment lifecycle (deploy, start, pause, resume, stop, flatten, retire)
- Execution mode management (backtest, paper, live) for deployments
- Trading session calendars and session-aware start/stop
- Global, account-level, and strategy-level kill switches
- Trading halt and maintenance mode
- Automation policy enforcement for unattended trading
- Deployment health supervision

### Out of Scope

- Signal generation, order state, venue communication, risk calculations (delegated to the engines named above)

### Actors

| Actor | Interaction |
|---|---|
| Quantitative Trader | Starts, pauses, stops, and flattens deployments |
| Risk Manager | Activates kill switches, approves automation policies |
| System Administrator | Registers trading accounts, sets maintenance mode |
| Strategy Engine | Hosts strategy instances under Trading Engine control |
| OMS / EMS / RMS | Receive lifecycle commands and report state |
| Workflow Engine | Triggers scheduled start/stop actions |

## 19.3 Domain Entities

### 19.3.1 TradingAccount

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| venue_type | Enum: EXCHANGE, BROKER, SIMULATED | Required |
| connection_id | Reference → Connection (Ch. 45/46) | Required unless SIMULATED |
| account_mode | Enum: LIVE, PAPER | Required; immutable after first order |
| base_currency | Currency code | Required |
| status | Enum: ACTIVE, SUSPENDED, DISABLED | Default ACTIVE |
| trading_permissions | Set of Enum: SPOT, MARGIN, FUTURES, OPTIONS, SHORT | Must be subset of venue capabilities |
| jurisdiction | Country code | Required for LIVE (CON-065) |
| kill_switch_state | Enum: ARMED, TRIGGERED | Default ARMED |

### 19.3.2 StrategyDeployment

| Attribute | Type | Constraints |
|---|---|---|
| strategy_id | Reference → Strategy | Required |
| strategy_version | Semantic version | Required; immutable |
| account_id | Reference → TradingAccount | Required |
| mode | Enum: PAPER, LIVE | Must match account_mode |
| parameters | Structured document | Validated against the strategy parameter schema |
| instrument_universe | List of InstrumentId | 1–5,000 entries |
| capital_allocation | Money | > 0; ≤ account available capital unless overcommit permitted |
| automation_policy_id | Reference → AutomationPolicy | Required for LIVE |
| state | Enum (see 19.4) | System-managed |
| state_reason | String(500) | Required for PAUSED, HALTED, FAILED |
| approved_by | Principal reference | Required for LIVE (CON-203) |
| started_at / stopped_at | Timestamp | System-managed |

### 19.3.3 AutomationPolicy

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| allowed_hours | Session schedule | Default: instrument trading sessions |
| max_orders_per_minute | Integer | 1–10,000 |
| max_daily_notional | Money | > 0 |
| requires_supervision | Boolean | If true, a supervising user must have an active session |
| supervision_timeout | Duration | Default 30 min; deployment pauses if no supervisor heartbeat within timeout |
| approved_by | Principal | Risk Manager or authorized role |
| valid_until | Timestamp | Optional; policy expires after this instant |

### 19.3.4 KillSwitch

| Attribute | Type | Constraints |
|---|---|---|
| scope | Enum: GLOBAL, WORKSPACE, ACCOUNT, STRATEGY, INSTRUMENT | Required |
| target_id | UUID | Required unless GLOBAL |
| action | Enum: BLOCK_NEW, CANCEL_OPEN, FLATTEN | Required |
| triggered_by | Principal or rule reference | Required |
| triggered_at | Timestamp | Required |
| reason | String(500) | Required |
| released_by / released_at | Principal / Timestamp | Set on release |

### 19.3.5 TradingCalendar

| Attribute | Type | Constraints |
|---|---|---|
| venue_id | Reference → Venue | Required |
| timezone | IANA timezone | Required |
| sessions | List of (day_of_week, open_time, close_time, session_type) | session_type ∈ PRE, REGULAR, POST, CONTINUOUS |
| holidays | List of Date | Maintained from reference data |
| early_closes | List of (Date, close_time) | Optional |

## 19.4 Deployment State Model

| State | Description |
|---|---|
| DRAFT | Deployment defined but not validated |
| READY | Validated and approved; not running |
| STARTING | Warm-up in progress (data subscription, state restoration, indicator warm-up) |
| RUNNING | Strategy evaluating and permitted to submit orders |
| PAUSED | Strategy evaluates data but order submission is blocked; open orders remain |
| STOPPING | Graceful stop in progress (cancelling open orders per stop policy) |
| STOPPED | Not evaluating; may hold positions |
| FLATTENING | Closing all positions attributed to the deployment |
| HALTED | Stopped by a kill switch or risk breach; requires explicit release |
| FAILED | Unrecoverable error; requires operator intervention |
| RETIRED | Permanently decommissioned; read-only |

Permitted transitions:

| From | To | Trigger |
|---|---|---|
| DRAFT | READY | Validation passes and required approvals recorded |
| READY | STARTING | Start command or scheduled start |
| STARTING | RUNNING | Warm-up complete |
| STARTING | FAILED | Warm-up error or timeout |
| RUNNING | PAUSED | Pause command, supervision timeout, session close (if configured) |
| PAUSED | RUNNING | Resume command, session open (if configured) |
| RUNNING, PAUSED | STOPPING | Stop command |
| STOPPING | STOPPED | All open orders in terminal state or stop timeout reached |
| RUNNING, PAUSED, STOPPED | FLATTENING | Flatten command |
| FLATTENING | STOPPED | All attributed positions closed |
| RUNNING, PAUSED, STARTING, FLATTENING | HALTED | Kill switch or risk breach |
| HALTED | STOPPED | Kill switch released and operator acknowledgment |
| Any non-terminal | FAILED | Unrecoverable strategy error |
| FAILED | STOPPED | Operator acknowledgment after investigation |
| READY, STOPPED | RETIRED | Retire command |
| STOPPED | STARTING | Start command |

Any transition not listed shall be rejected with error `INVALID_STATE_TRANSITION`.

## 19.5 Functional Requirements – Trading Accounts

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-19001 | The system shall allow an authorized user to register a trading account by specifying name, venue connection, account mode, base currency, jurisdiction, and trading permissions. | M | T |
| FR-19002 | The system shall verify, upon registration of a LIVE account, that the associated connection authenticates successfully and that the venue reports the account as tradable. | M | T |
| FR-19003 | The system shall reject registration of a LIVE account whose credentials grant withdrawal permission where the venue exposes permission metadata (CON-082). | M | T |
| FR-19004 | The system shall prevent an account's mode from being changed after the first order has been submitted through that account. | M | T |
| FR-19005 | The system shall allow an authorized user to suspend an account; a suspended account shall reject all new orders and shall leave existing open orders untouched unless the suspension request specifies cancellation. | M | T |
| FR-19006 | The system shall display, for each account, the current status, connection state, kill switch state, number of running deployments, open order count, and last reconciliation time. | M | D |
| FR-19007 | The system shall support multiple trading accounts per venue connection where the venue supports sub-accounts. | S | T |

## 19.6 Functional Requirements – Deployment Lifecycle

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-19010 | The system shall allow a user to create a deployment by selecting an approved strategy version, a trading account, parameter values, an instrument universe, a capital allocation, and (for LIVE) an automation policy. | M | T |
| FR-19011 | The system shall validate deployment parameters against the parameter schema declared by the strategy version and shall report every validation failure with the parameter name and violated constraint. | M | T |
| FR-19012 | The system shall reject a LIVE deployment whose strategy version has not completed the promotion workflow (backtest and paper trading evidence) defined in Chapter 24. | M | T |
| FR-19013 | The system shall require approval of a LIVE deployment by a user other than the strategy author, subject to the single-user waiver of CON-203. | M | T |
| FR-19014 | The system shall execute the STARTING warm-up sequence in the following order: (1) verify account and connection health, (2) verify no kill switch applies, (3) load strategy artifact, (4) restore persisted strategy state if present, (5) subscribe to required market data, (6) load historical data for the declared warm-up window, (7) reconcile attributed positions and open orders with OMS, (8) transition to RUNNING. | M | T |
| FR-19015 | The system shall transition a deployment to FAILED if warm-up does not complete within the configured warm-up timeout (default 300 s) and shall report the step at which warm-up stalled. | M | T |
| FR-19016 | The system shall, on a pause command, block new order submissions from the deployment within 100 ms while continuing to deliver market data and fills to the strategy. | M | T |
| FR-19017 | The system shall, on a stop command, apply the deployment's stop policy: CANCEL_OPEN (default) cancels all open orders; LEAVE_OPEN leaves them working. | M | T |
| FR-19018 | The system shall, on a flatten command, cancel all open orders attributed to the deployment and submit closing orders for every attributed position using the configured flatten order type (default MARKET, alternatively aggressive LIMIT with configurable offset). | M | T |
| FR-19019 | The system shall report flatten progress, including remaining positions and any rejected closing orders, until all positions are closed or the operator aborts the flatten. | M | D |
| FR-19020 | The system shall persist deployment state transitions with timestamp, actor, previous state, new state, and reason. | M | T |
| FR-19021 | The system shall restore RUNNING and PAUSED deployments to their prior state after a platform restart, re-executing the warm-up sequence, unless the operator has configured "restart into PAUSED". | M | T |
| FR-19022 | The system shall prevent two deployments from trading the same instrument on the same account unless both deployments are members of the same coordinated deployment group. | M | T |
| FR-19023 | The system shall support coordinated deployment groups in which net position per instrument is aggregated across member deployments for risk purposes. | S | T |
| FR-19024 | The system shall allow the parameters of a RUNNING deployment to be changed only through a hot-reload operation that is declared as supported by the strategy; otherwise a stop and restart shall be required. | S | T |
| FR-19025 | The system shall retain retired deployments and their history for the retention period defined in CON-062. | M | I |

## 19.7 Functional Requirements – Trading Sessions

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-19030 | The system shall maintain a trading calendar per venue including regular sessions, extended sessions, holidays, and early closes. | M | T |
| FR-19031 | The system shall block order submission for instruments outside their trading session unless the account is enabled for extended-hours trading and the session type is PRE or POST (CON-043). | M | T |
| FR-19032 | The system shall allow deployments to be configured to automatically transition to PAUSED at session close and to RUNNING at session open. | M | T |
| FR-19033 | The system shall allow deployments to be configured to flatten positions a configurable duration before session close (default disabled). | S | T |
| FR-19034 | The system shall treat continuous (24×7) venues as always in session except during announced venue maintenance windows. | M | T |
| FR-19035 | The system shall emit `session.opening`, `session.opened`, `session.closing`, and `session.closed` events per venue at the configured offsets. | M | T |

## 19.8 Functional Requirements – Kill Switches and Halts

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-19040 | The system shall provide kill switches at GLOBAL, WORKSPACE, ACCOUNT, STRATEGY, and INSTRUMENT scope. | M | T |
| FR-19041 | The system shall, when a kill switch with action BLOCK_NEW is triggered, reject every new order within the switch scope within 50 ms of the trigger. | M | T |
| FR-19042 | The system shall, when a kill switch with action CANCEL_OPEN is triggered, additionally request cancellation of every open order within scope and report any order that could not be cancelled. | M | T |
| FR-19043 | The system shall, when a kill switch with action FLATTEN is triggered, additionally flatten all positions within scope. | M | T |
| FR-19044 | The system shall transition every deployment within the scope of a triggered kill switch to HALTED. | M | T |
| FR-19045 | The system shall make the kill switch control reachable from every primary screen of the trading user interface in no more than two interactions. | M | D |
| FR-19046 | The system shall require the reason and the multi-factor step-up authentication (CON-086) to release a kill switch, and shall not require step-up authentication to trigger one. | M | T |
| FR-19047 | The system shall allow risk rules (Chapter 27) to trigger kill switches automatically. | M | T |
| FR-19048 | The system shall persist kill switch state such that a triggered kill switch remains triggered across platform restarts. | M | T |
| FR-19049 | The system shall notify all users holding Trader, Risk Manager, or Operations roles in the affected workspace when a kill switch is triggered or released. | M | T |
| FR-19050 | The system shall support a platform-wide maintenance mode in which no deployments may start and all live order submission is blocked, while read-only functions remain available. | M | T |

## 19.9 Functional Requirements – Automation Policy and Supervision

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-19060 | The system shall require an approved, unexpired automation policy for every LIVE deployment (CON-064). | M | T |
| FR-19061 | The system shall pause a LIVE deployment whose automation policy expires or is revoked, with state reason `AUTOMATION_POLICY_INVALID`. | M | T |
| FR-19062 | The system shall enforce the policy's `max_orders_per_minute` and `max_daily_notional` independently of RMS limits and shall reject excess orders with reason `AUTOMATION_POLICY_LIMIT`. | M | T |
| FR-19063 | The system shall, for policies with `requires_supervision = true`, pause deployments when no supervising user has sent a heartbeat within the supervision timeout. | S | T |
| FR-19064 | The system shall restrict order submission to the policy's `allowed_hours`. | M | T |

## 19.10 Business Rules

| ID | Rule |
|---|---|
| BR-19-01 | A kill switch at a broader scope overrides any narrower scope; releasing a narrower switch does not release a broader one. |
| BR-19-02 | A deployment is eligible to submit orders only if: state = RUNNING, account status = ACTIVE, no applicable kill switch is TRIGGERED, platform is not in maintenance mode, the instrument is in session, and the automation policy (LIVE) is valid. |
| BR-19-03 | Positions and orders created by a deployment are attributed to that deployment for the lifetime of the position, including after the deployment stops. |
| BR-19-04 | Manual orders entered by a trader are attributed to a system-generated "MANUAL" pseudo-deployment per account. |
| BR-19-05 | Capital allocation is a soft budget enforced by RMS; the sum of allocations on an account may exceed account equity only if the account's overcommit flag is enabled. |

## 19.11 Events

| Event | Published When | Key Payload |
|---|---|---|
| `deployment.created` | Deployment created | deployment_id, strategy_version, account_id, mode |
| `deployment.state.changed` | Any state transition | deployment_id, from, to, reason, actor |
| `killswitch.triggered` | Kill switch triggered | scope, target_id, action, reason, actor |
| `killswitch.released` | Kill switch released | scope, target_id, actor |
| `session.opened` / `session.closed` | Venue session boundaries | venue_id, session_type |
| `account.status.changed` | Account status change | account_id, from, to |
| `maintenance.mode.changed` | Maintenance mode toggled | enabled, actor |

Consumed events: `risk.breach.detected` (Ch. 27), `connection.state.changed` (Ch. 45/46), `strategy.error` (Ch. 24).

## 19.12 Configuration

| Key | Default | Description |
|---|---|---|
| `trading.deployment.warmup.timeout` | 300 s | Maximum warm-up duration |
| `trading.deployment.stop.timeout` | 60 s | Maximum wait for order cancellation during stop |
| `trading.deployment.restart.policy` | RESTORE | RESTORE or PAUSED |
| `trading.flatten.order.type` | MARKET | MARKET or LIMIT_AGGRESSIVE |
| `trading.flatten.limit.offset.bps` | 20 | Offset for aggressive limit flatten |
| `trading.killswitch.notify.roles` | TRADER, RISK_MANAGER, OPERATIONS | Roles notified |
| `trading.session.event.offsets` | -5 min, 0, -5 min, 0 | Offsets for session events |

## 19.13 Error Conditions

| Code | Condition | Handling |
|---|---|---|
| `INVALID_STATE_TRANSITION` | Requested transition not permitted | Reject command; no side effects |
| `DEPLOYMENT_NOT_APPROVED` | LIVE start without approval | Reject |
| `STRATEGY_NOT_PROMOTED` | Strategy version lacks promotion evidence | Reject |
| `ACCOUNT_NOT_ACTIVE` | Account suspended or disabled | Reject |
| `KILL_SWITCH_ACTIVE` | Applicable kill switch triggered | Reject |
| `INSTRUMENT_CONFLICT` | Another deployment trades the instrument on the account | Reject |
| `WARMUP_TIMEOUT` | Warm-up exceeded timeout | Transition to FAILED |
| `AUTOMATION_POLICY_INVALID` | Missing, expired, or revoked policy | Reject start / pause running deployment |

## 19.14 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-19001 | Given a RUNNING live deployment, when a Risk Manager triggers an ACCOUNT kill switch with CANCEL_OPEN, then no new order for that account reaches the EMS after 50 ms, all open orders receive cancel requests, and the deployment state becomes HALTED. | FR-19041, FR-19042, FR-19044 |
| AC-19002 | Given a triggered kill switch, when the platform restarts, then the kill switch remains TRIGGERED and affected deployments remain HALTED. | FR-19048 |
| AC-19003 | Given a strategy version without paper trading evidence, when a user attempts a LIVE deployment, then the request is rejected with STRATEGY_NOT_PROMOTED. | FR-19012 |
| AC-19004 | Given a deployment with 3 open positions, when flatten is commanded, then closing orders are submitted for all 3 and the deployment becomes STOPPED once positions are zero. | FR-19018 |
| AC-19005 | Given an automation policy with max 10 orders/minute, when the strategy produces 15 orders in one minute, then 5 are rejected with AUTOMATION_POLICY_LIMIT. | FR-19062 |
| AC-19006 | Given an equity instrument after regular close and an account without extended-hours permission, when an order is submitted, then it is rejected before reaching the venue. | FR-19031 |

---

*End of Chapter 19 – Trading Engine*
