# Chapter 91 – Failure Behaviour

## 91.1 Purpose

This chapter specifies how each critical component shall behave when it or its dependencies fail. The guiding rule is **fail safe**: when in doubt, do not open new risk; always preserve the ability to reduce risk.

## 91.2 Failure Behaviour Matrix

| Failure | Detection | Required Behaviour |
|---|---|---|
| RMS unavailable / timeout | Decision timeout (FR-21021) | Reject new orders with RISK_UNAVAILABLE; platform SAFE if persistent (> 5 s) |
| RMS internal error | Exception | Reject order (BR-27-01) |
| OMS crash | Liveness probe | No orders accepted; restart; recovery sequence (FR-51002) |
| EMS crash | Liveness probe | OMS marks in-flight SUBMITTED orders for resolution on restart; no resubmission (FR-21047) |
| Adapter disconnect | Heartbeat (FR-45005) | Block new orders to venue; reconnect; reconcile (SEQ-06); venue cancel-on-disconnect protection (FR-51006) |
| Market data stale | Staleness (FR-20044) | Pre-trade STALE_DATA check rejects opening orders (FR-27026); strategies notified; optional pause via automation |
| Order book invalid | Sequence gap | Book marked INVALID; consumers notified; resync (FR-20023) |
| Message bus down | Publish failure | Trading engines enter SAFE (FR-51004); outbox buffers events (API-82009) |
| Database unavailable | Connection failure | Reject state-changing operations; SAFE; no in-memory-only commits |
| Secret store unavailable | Fetch failure | Existing connections continue with cached credentials in memory; new connections fail; alert |
| Strategy exception | Callback error | Skip event; FAILED after threshold (FR-24022) |
| Strategy resource exhaustion | Limits | FAILED; other deployments unaffected (FR-24020) |
| Inference failure / timeout | Error / timeout | Error returned to strategy (AI-59009); strategy fallback; no default prediction |
| Clock offset exceeded | Monitoring (CON-165) | Alert; block live deployment start above critical threshold (DEP-010) |
| Disk full | Monitoring | Alert at 85%; at 95% stop non-critical writes (logs DEBUG, research outputs) to preserve transactional writes |
| Health Engine down | Probe from supervisors | Trading continues with local checks; no new deployment starts (FR-62009) |
| Notification failure | Delivery failure | Retry and fallback channel (FR-33007) |

## 91.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-91001 | Each component shall implement the failure behaviour of 91.2 for its failure modes. | M | T |
| FR-91002 | No failure mode shall result in an order being sent to a venue without an RMS APPROVE decision (CON-004). | M | T |
| FR-91003 | No failure mode shall block the cancellation of open orders when a path to the venue exists. | M | T |
| FR-91004 | Every failure mode in 91.2 shall have an automated fault-injection test (NFR-68008). | M | T |
| FR-91005 | Every failure shall produce an alert with severity per Chapter 17.2 criticality and an incident record for F2–F8 (FR-51009). | M | T |

---

*End of Chapter 91 – Failure Behaviour*
