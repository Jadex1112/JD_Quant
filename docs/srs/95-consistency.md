# Chapter 95 – Consistency

## 95.1 Purpose

This chapter specifies data consistency guarantees and invariants that shall hold at all times (or at defined checkpoints) across the platform.

## 95.2 Consistency Levels

| Data | Guarantee |
|---|---|
| Order state, fills, positions, balances (internal) | Strong consistency within the owning partition |
| Risk pre-trade state | Read-your-writes with respect to orders and fills of the same account |
| Portfolio NAV, analytics | Eventual consistency, bounded staleness ≤ 1 s (real time) |
| Reports | Snapshot consistency (FR-37006) |
| Read replicas / search indexes | Eventual, bounded staleness ≤ 5 s |
| Venue vs internal state | Eventual, reconciled within reconciliation intervals |

## 95.3 System Invariants

| ID | Invariant |
|---|---|
| INV-01 | For every order: 0 ≤ filled_quantity ≤ quantity, except flagged venue overfills (FR-21043). |
| INV-02 | For every order: filled_quantity = Σ fill quantities. |
| INV-03 | For every (account, instrument, side): Σ deployment positions = net position from all fills and adjustments (BR-28-04). |
| INV-04 | After reconciliation: internal net positions = venue positions (CON-042). |
| INV-05 | Every order in state beyond PENDING_RISK has exactly one RMS decision with outcome APPROVE (live/paper). |
| INV-06 | Every live order was submitted while its deployment satisfied BR-19-02. |
| INV-07 | Σ child order quantities ≤ parent quantity (BR-22-05). |
| INV-08 | P&L attribution components sum to total P&L (FR-31024). |
| INV-09 | Audit hash chain verifies for every stream (FR-38002). |
| INV-10 | Every published trading-state event corresponds to a persisted state change and vice versa (API-82009). |

## 95.4 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-95001 | The platform shall provide the consistency guarantees of 95.2. | M | T |
| FR-95002 | The platform shall continuously or periodically (default every 5 min) verify invariants INV-01 to INV-10 and raise a Critical alert on violation. | M | T |
| FR-95003 | Invariant checks shall be included in automated tests, including property-based tests of OMS and Position Engine logic. | M | T |
| FR-95004 | User interfaces shall indicate when displayed data is stale beyond its staleness bound. | S | D |

*Part F – System Behaviour is complete.*

---

*End of Chapter 95 – Consistency*
