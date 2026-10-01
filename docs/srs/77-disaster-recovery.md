# Chapter 77 – Disaster Recovery

## 77.1 Purpose

This chapter specifies requirements for business continuity in the event of loss of an entire site or region (failure class F7), supporting the Disaster Recovery environment defined in Chapter 14.3 and Disaster Recovery Mode (Chapter 2.7).

## 77.2 Disaster Recovery Strategy

| Tier | Strategy |
|---|---|
| T1 | Backup and restore to new infrastructure from off-site backups |
| T2 | Warm standby in a secondary region with continuous asynchronous replication of transactional data and replicated artifacts |

## 77.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-77001 | T2 deployments shall maintain a warm standby in a separate region with RPO ≤ 1 minute and RTO ≤ 1 hour for the live trading capability. | M | T |
| OPS-77002 | T1 deployments shall be restorable from off-site backups to new infrastructure within 8 hours with RPO ≤ 24 hours (≤ 5 minutes where log shipping to off-site storage is configured). | M | T |
| OPS-77003 | Disaster Recovery Mode shall start with all deployments in PAUSED state and kill switches evaluated, requiring explicit operator activation of trading after reconciliation with all venues. | M | T |
| OPS-77004 | Failover to the DR region shall be an operator-initiated, documented procedure with a single approved command set; automatic cross-region failover of trading is prohibited to avoid split-brain trading. | M | I |
| OPS-77005 | The system shall prevent simultaneous trading from primary and DR sites (split-brain) through a fencing mechanism (e.g. lease or token) verified before trading starts. | M | T |
| OPS-77006 | DR procedures shall be exercised at least twice per year, including failover and failback, with results recorded. | M | I |
| OPS-77007 | Venue credentials, configurations, and IP allowlists required at the DR site shall be pre-provisioned and verified during exercises. | M | I |
| OPS-77008 | Communication templates for users during a disaster shall be prepared and deliverable through at least two independent channels. | S | I |

---

*End of Chapter 77 – Disaster Recovery*
