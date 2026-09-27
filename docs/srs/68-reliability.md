# Chapter 68 – Reliability

## 68.1 Purpose

This chapter specifies requirements for correct operation over time, fault tolerance, and data integrity under failure.

## 68.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-68001 | The system shall create zero duplicate venue orders due to internal retries, timeouts, or restarts (QAS-01), verified by fault-injection tests of ≥ 10,000 injected faults. | M | T |
| NFR-68002 | The system shall lose zero venue-confirmed fills; every fill shall be reflected in OMS, positions, and balances after reconciliation. | M | T |
| NFR-68003 | Mean time between failures (MTBF) of trading engine processes shall exceed 30 days under the load model. | S | A |
| NFR-68004 | Mean time to recovery (MTTR) from F1 component crashes shall be ≤ 60 s (automatic restart and recovery sequence, FR-51002). | M | T |
| NFR-68005 | Financial calculations shall be exact to the decimal precision defined (CON-022); independent recomputation of P&L from fills shall match stored values exactly. | M | T |
| NFR-68006 | The message bus shall provide at-least-once delivery with consumer idempotency, yielding effectively-once processing for all state-changing consumers. | M | T |
| NFR-68007 | All external calls shall have timeouts; no thread or task shall wait indefinitely on an external dependency. | M | I |
| NFR-68008 | The system shall be verified by chaos testing covering: process kill, network partition, latency injection, dependency outage, disk full, and clock skew, at least before each minor release. | M | T |
| NFR-68009 | Strategy failures shall be isolated (FR-24020); a failing strategy shall not degrade other deployments' latency by more than 10%. | M | T |
| NFR-68010 | Data integrity checks (checksums, hash chains, reconciliation) shall run on schedule and report failures within 5 minutes of detection. | M | T |

---

*End of Chapter 68 – Reliability*
