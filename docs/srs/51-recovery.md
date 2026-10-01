# Chapter 51 – Recovery

## 51.1 Purpose

The Recovery module defines the functional behavior of JD Quant AI when failures occur: how state is preserved, how components restart, how trading state is reconciled with venues, and how operators are guided through recovery. Non-functional recovery targets (RTO, RPO) are specified in Part D; detailed failure behavior per component in Part F (Chapters 91–92).

## 51.2 Failure Classes

| Class | Examples | Required Response |
|---|---|---|
| F1 – Component crash | Engine process terminates | Automatic restart; state restoration; reconciliation |
| F2 – Venue connectivity loss | Stream or REST failures | Reconnect; reconcile; block new orders to venue while disconnected |
| F3 – Data feed failure | Stale or invalid data | Staleness policy; failover; strategy pause per configuration |
| F4 – Dependency failure | Database, message bus, secret store unavailable | Fail safe (no new orders); buffer where safe; alert |
| F5 – Host / zone failure | Node or availability zone lost | Failover to standby (HA deployments) |
| F6 – Data corruption | Integrity check failure | Isolate; restore from backup; replay events |
| F7 – Site disaster | Region loss | Disaster recovery procedure (Part D) |
| F8 – Logical error | Strategy bug causing losses | Kill switch; flatten; post-incident review |

## 51.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-51001 | The system shall persist all state necessary to resume trading (orders, fills, positions, deployment states, kill switch states, strategy checkpoints, risk counters) durably before acknowledging the operations that changed it. | M | T |
| FR-51002 | The system shall perform, on startup of trading engines, a recovery sequence: (1) load persisted state; (2) enter RECOVERING mode blocking new orders; (3) connect to venues; (4) reconcile orders, fills, balances, positions; (5) rebuild risk state; (6) resume deployments per restart policy (FR-19021); (7) exit RECOVERING. | M | T |
| FR-51003 | The system shall publish recovery progress and results, including every reconciliation difference and corrective action. | M | T |
| FR-51004 | The system shall enter a SAFE state when a Critical dependency (DEP-100–DEP-109) is unavailable: block new opening orders, allow cancellations, and allow reduce-only orders where the path to the venue is available. | M | T |
| FR-51005 | The system shall buffer outbound events for downstream non-critical consumers during message bus outages up to a configured limit and replay them in order upon recovery. | M | T |
| FR-51006 | The system shall support venue-side protective mechanisms where available (e.g. dead-man's switch / cancel-on-disconnect) and enable them by default for LIVE accounts with a configurable timeout (default 60 s). | M | T |
| FR-51007 | The system shall provide runbook-guided recovery screens for F2–F8 classes showing current state, recommended actions, and one-click execution of permitted safe actions (cancel all, flatten, pause all). | S | D |
| FR-51008 | The system shall support event replay: rebuilding derived state (positions, portfolio, analytics) from the persisted event log for a time range. | M | T |
| FR-51009 | The system shall record an incident entity for each F2–F8 occurrence with timeline, affected entities, actions, and resolution, supporting post-incident review. | M | T |
| FR-51010 | The system shall test recovery procedures automatically in non-production environments through scheduled fault-injection exercises. | S | T |

## 51.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-51001 | Given the OMS is killed with 10 open orders and 2 fills occur during downtime, then after restart all 10 orders have correct state, the 2 fills are applied, and no new orders were sent during RECOVERING. | FR-51002 |
| AC-51002 | Given the message bus is down, then no new opening orders are submitted and cancellations still reach the venue. | FR-51004 |
| AC-51003 | Given a LIVE account on a venue supporting cancel-on-disconnect, then the protection is active with the configured timeout. | FR-51006 |

---

*End of Chapter 51 – Recovery*
