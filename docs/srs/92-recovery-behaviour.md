# Chapter 92 – Recovery Behaviour

## 92.1 Purpose

This chapter specifies the behaviour of components when recovering from the failures of Chapter 91, detailing ordering, idempotency, and reconciliation rules.

## 92.2 Recovery Ordering

Upon platform (re)start, components shall become ready in the following dependency order:

1. Configuration (CCE), secrets, database, message bus, event store
2. SAE (authentication/authorization)
3. MDE reference data (instruments, calendars)
4. Position Engine and OMS state load
5. RMS state rebuild from positions, working orders, and daily counters
6. EMS and adapters (connect, reconcile)
7. Trading Engine (kill switch state, deployment restoration)
8. SE (deployments warm-up)
9. Non-critical services (analytics, reporting, AI assistants)

## 92.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-92001 | Components shall start in the order of 92.2, each waiting for readiness of its dependencies with a timeout and alert. | M | T |
| FR-92002 | The RMS shall rebuild daily P&L, order-rate counters, and drawdown peaks from persisted state such that limits are enforced identically before and after restart. | M | T |
| FR-92003 | Replayed events shall not produce duplicate side effects (NFR-68006); notifications for events already notified before the failure shall not be re-sent. | M | T |
| FR-92004 | Kill switches, maintenance mode, REDUCE_ONLY states, and HALTED deployments shall persist across recovery (FR-19048). | M | T |
| FR-92005 | After recovery, the system shall publish a recovery summary listing downtime, reconciled differences, and actions (FR-51003). | M | T |
| FR-92006 | Deployments shall resume only after all their required capabilities are OPERATIONAL (FR-62005). | M | T |
| FR-92007 | Strategies shall receive an `on_session`-style recovery notification including the duration of downtime so they can adapt (e.g. discard stale signals). | S | T |
| FR-92008 | Jobs interrupted by failure shall resume from checkpoints or restart per retry policy (FR-35006, FR-35011). | M | T |
| FR-92009 | Workflows shall resume from their last persisted step (FR-49004). | M | T |

---

*End of Chapter 92 – Recovery Behaviour*
