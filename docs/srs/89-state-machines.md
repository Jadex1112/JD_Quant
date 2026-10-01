# Chapter 89 – State Machines

## 89.1 Purpose

This chapter begins Part F – System Behaviour. It consolidates the state machines of the platform, defines common state machine requirements, and serves as the authoritative index of stateful entities. Detailed transition tables reside in the referenced Part C chapters.

## 89.2 State Machine Index

| Entity | States | Defined In |
|---|---|---|
| StrategyDeployment | DRAFT, READY, STARTING, RUNNING, PAUSED, STOPPING, STOPPED, FLATTENING, HALTED, FAILED, RETIRED | 19.4 |
| Order | CREATED, PENDING_RISK, RISK_REJECTED, PENDING_SUBMIT, SUBMITTED, OPEN, PARTIALLY_FILLED, PENDING_CANCEL, PENDING_REPLACE, FILLED, CANCELED, REJECTED, EXPIRED, UNKNOWN, TRIGGER_PENDING | 21.4 |
| KillSwitch | ARMED, TRIGGERED | 19.3.4 |
| TradingAccount | ACTIVE, SUSPENDED, DISABLED | 19.3.1 |
| Connection | DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, AUTH_FAILED, DISABLED | 45.3.1 |
| Market data feed | LIVE, DELAYED, STALE, DOWN | 20.6 |
| Order book | VALID, INVALID, SYNCING | 20.5 |
| Position | OPEN, CLOSED | 28.2.1 |
| StrategyVersion promotion | RESEARCH, BACKTESTED, PAPER_VALIDATED, LIVE_APPROVED, DEPRECATED | 24.3.2 |
| RiskProfile | DRAFT, ACTIVE, SUSPENDED | 27.3.1 |
| RiskBreach | OPEN, ACKNOWLEDGED, RESOLVED | 27.3.4 |
| Job | QUEUED, SCHEDULED, RUNNING, SUCCEEDED, FAILED, CANCELING, CANCELED, TIMED_OUT, RETRYING | 35.2.1 |
| WorkflowInstance | RUNNING, WAITING_APPROVAL, SUCCEEDED, FAILED, CANCELED, TIMED_OUT | 49.2.3 |
| ModelVersion stage | NONE, STAGING, SHADOW, PRODUCTION, ARCHIVED | 57.2.2 |
| User | INVITED, ACTIVE, SUSPENDED, DEACTIVATED, ARCHIVED | 40.2.1 |
| Notification | CREATED, DISPATCHED, DELIVERED, FAILED, ACKNOWLEDGED, EXPIRED | 33.2.1 |
| Component health | HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN | 62.2 |
| Platform mode | NORMAL, RECOVERING, SAFE, MAINTENANCE, DISASTER_RECOVERY | 51.3, 19.8, 77.3 |

## 89.3 Platform Mode State Machine

| From | To | Trigger |
|---|---|---|
| (start) | RECOVERING | Process start of trading engines |
| RECOVERING | NORMAL | Recovery sequence complete (FR-51002) |
| NORMAL | SAFE | Critical dependency unavailable (FR-51004) |
| SAFE | RECOVERING | Dependency restored |
| NORMAL, SAFE | MAINTENANCE | Administrator enables maintenance (FR-19050) |
| MAINTENANCE | RECOVERING | Administrator disables maintenance |
| any | DISASTER_RECOVERY | DR activation at standby site (OPS-77003) |
| DISASTER_RECOVERY | RECOVERING | Operator activates trading after reconciliation |

Order admission per mode:

| Mode | New opening orders | Reduce-only orders | Cancels |
|---|---|---|---|
| NORMAL | Allowed (subject to controls) | Allowed | Allowed |
| RECOVERING | Blocked | Blocked | Allowed after venue connection |
| SAFE | Blocked | Allowed if venue path available | Allowed if venue path available |
| MAINTENANCE | Blocked | Blocked (manual allowed with privileged permission) | Allowed |
| DISASTER_RECOVERY | Blocked until activation | Blocked until activation | Allowed |

## 89.4 Common Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-89001 | Every state machine shall be implemented from a single declarative definition of states and permitted transitions; transitions not defined shall be rejected (INVALID_STATE_TRANSITION). | M | I |
| FR-89002 | Every transition shall be persisted atomically with its causing change and published as an event (API-82009). | M | T |
| FR-89003 | Every transition record shall include from-state, to-state, trigger, actor, reason, and timestamp. | M | T |
| FR-89004 | Terminal states shall be immutable except for venue-driven corrections explicitly defined (21.4). | M | T |
| FR-89005 | State machines shall be covered by tests exercising every permitted transition and a sample of forbidden transitions. | M | T |
| FR-89006 | State machine diagrams shall be generated from the declarative definitions for documentation. | S | I |

---

*End of Chapter 89 – State Machines*
