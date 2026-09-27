# Chapter 76 – Recovery (Non-Functional)

## 76.1 Purpose

This chapter specifies measurable recovery objectives complementing the functional recovery behavior of Chapter 51.

## 76.2 Recovery Objectives

| Scenario | RPO | RTO (T1) | RTO (T2) |
|---|---|---|---|
| Component crash (F1) | 0 | 60 s | 30 s |
| Host failure (F5) | 0 (T2) / last backup (T1) | 4 h | 30 s |
| Database failure | 0 (T2 synchronous replica) / ≤ 5 min (T1 log backup) | 2 h | 5 min |
| Data corruption (F6) | Point-in-time before corruption | 4 h | 2 h |
| Site disaster (F7) | Chapter 77 | Chapter 77 | Chapter 77 |

## 76.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-76001 | The system shall meet the RPO and RTO of 76.2 for its deployment tier. | M | T |
| OPS-76002 | After any recovery, the system shall complete venue reconciliation (FR-51002) before resuming trading, regardless of RPO. | M | T |
| OPS-76003 | Point-in-time recovery shall be possible to any second within the last 35 days for critical transactional data. | M | T |
| OPS-76004 | Recovery procedures shall be documented as runbooks with step-by-step instructions, owners, and verification steps. | M | I |
| OPS-76005 | Recovery time and data loss shall be measured during every recovery test and incident, and tracked against objectives. | M | T |

---

*End of Chapter 76 – Recovery (Non-Functional)*
