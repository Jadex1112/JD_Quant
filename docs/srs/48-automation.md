# Chapter 48 – Automation

## 48.1 Purpose

The Automation module enables users to define event-driven rules ("when X happens, if Y holds, do Z") that automate repetitive operational responses: pausing strategies on conditions, rebalancing, triggering reports, starting retraining, and similar tasks. Automation complements scheduled triggers (Chapter 34) and multi-step workflows (Chapter 49). All automated trading actions are subject to automation policy (CON-064) and risk controls.

## 48.2 Domain Entities

### 48.2.1 AutomationRule

| Attribute | Description |
|---|---|
| name, description, owner | Identity |
| trigger | Event type pattern (e.g. `risk.warning.detected`, `marketdata.stale`, `order.fill`), schedule reference, or metric threshold (metric, operator, value, duration) |
| conditions | Boolean expression over event payload and platform state (e.g. `deployment.mode == LIVE AND portfolio.drawdown > 0.05`) |
| actions | Ordered list of actions from the action catalog (48.3) with parameters |
| execution_principal | Owner or designated service account |
| cooldown | Minimum time between executions (default 5 min) |
| max_executions_per_day | Default 20 |
| dry_run | Boolean: evaluate and log without acting |
| enabled | Boolean |
| status | ACTIVE, DISABLED, SUSPENDED (auto-suspended after failures) |

## 48.3 Action Catalog

| Action | Description | Trading Impact |
|---|---|---|
| NOTIFY | Send notification | None |
| PAUSE_DEPLOYMENT / RESUME_DEPLOYMENT | Lifecycle control | Reduces / resumes |
| STOP_DEPLOYMENT / FLATTEN_DEPLOYMENT | Lifecycle control | Reduces |
| TRIGGER_KILL_SWITCH | Kill switch at scope | Reduces |
| CANCEL_ORDERS | Cancel by filter | Reduces |
| SUBMIT_REBALANCE_PROPOSAL | Create proposal (FR-23043) | Requires approval unless policy |
| RUN_BACKTEST / RUN_REPORT / RUN_EXPORT | Submit job | None |
| START_TRAINING | Submit training job | None |
| RUN_WORKFLOW | Start workflow | Per workflow |
| WEBHOOK | Call external endpoint | None |
| SET_FEATURE_FLAG | Toggle a permitted flag | Per flag |

Actions are classified as risk-reducing (pause, stop, flatten, cancel, kill switch), neutral, or risk-increasing (resume, start, submit orders). Risk-increasing actions require an automation policy that explicitly allows them.

## 48.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-48001 | The system shall allow users to create automation rules with triggers, conditions, and actions per 48.2 and 48.3 through a form-based builder. | M | T |
| FR-48002 | The system shall validate at creation that the execution principal holds permission for every action. | M | T |
| FR-48003 | The system shall evaluate rules on matching events within 1 s and execute actions in order, stopping at the first failure unless the action is marked continue-on-error. | M | T |
| FR-48004 | The system shall enforce cooldown and daily execution limits per rule. | M | T |
| FR-48005 | The system shall require an automation policy explicitly permitting risk-increasing actions for a rule to include them. | M | T |
| FR-48006 | The system shall support dry-run mode logging what would have happened. | M | T |
| FR-48007 | The system shall record every evaluation that fired, with trigger payload, condition result, actions executed, and outcomes. | M | T |
| FR-48008 | The system shall auto-suspend a rule after N consecutive action failures (default 3) and notify the owner. | M | T |
| FR-48009 | The system shall detect potential rule loops (rule A's action triggers rule B whose action triggers A) at creation and warn; at runtime it shall limit chained executions to depth 5. | M | T |
| FR-48010 | The system shall provide built-in rule templates: pause strategy on stale data, flatten on drawdown, notify on large fill, retrain on drift, daily report after close. | S | D |

## 48.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-48001 | Given a rule "on marketdata.stale for BTC, pause deployments trading BTC", then affected deployments pause within 2 s of the event. | FR-48003 |
| AC-48002 | Given a rule containing RESUME_DEPLOYMENT without a policy allowing risk-increasing actions, then saving is rejected. | FR-48005 |
| AC-48003 | Given a rule loop, then execution stops at depth 5 and an alert is raised. | FR-48009 |

---

*End of Chapter 48 – Automation*
