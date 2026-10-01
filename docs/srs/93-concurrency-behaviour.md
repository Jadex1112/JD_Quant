# Chapter 93 – Concurrency Behaviour

## 93.1 Purpose

This chapter specifies how the platform behaves under concurrent operations to guarantee correctness of financial state.

## 93.2 Concurrency Model

| Domain | Model |
|---|---|
| Orders | Single writer per account partition; all state changes of an order are serialized |
| Positions | Updated by the same partition as the fills that affect them |
| Risk pre-trade | Per-account serialized evaluation to avoid concurrent orders jointly exceeding limits |
| Strategies | Single-threaded event delivery per deployment (FR-24023) |
| Configuration entities | Optimistic concurrency via version (C.6) |
| Jobs | Independent; shared resources via quotas |

## 93.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-93001 | Pre-trade risk evaluation shall be serialized per account (or per the broadest scope of any applicable limit) so that two concurrent orders cannot both pass a limit that they would jointly breach. | M | T |
| FR-93002 | Order state transitions shall be serialized per order; concurrent cancel and fill shall resolve per 21.4 (fill racing cancel). | M | T |
| FR-93003 | Updates to user-editable entities shall use optimistic concurrency; a stale version shall be rejected with 409/412 and the current version returned (80.2). | M | T |
| FR-93004 | Manual orders and strategy orders on the same account shall pass through the same serialized risk evaluation. | M | T |
| FR-93005 | Concurrent kill switch trigger and order submission: any order whose RMS decision completes after the kill switch is persisted shall be rejected. | M | T |
| FR-93006 | Concurrent modifications of the same deployment (e.g. start and stop) shall be serialized; the second command is evaluated against the resulting state. | M | T |
| FR-93007 | The platform shall be free of data races on shared state, verified by concurrency testing tools and stress tests. | M | T |
| FR-93008 | Distributed locks (e.g. for schedules, FR-34007) shall use leases with fencing tokens to prevent split-brain execution. | M | T |

---

*End of Chapter 93 – Concurrency Behaviour*
