# Chapter 38 – Audit

## 38.1 Purpose

The Audit module records every material action performed within JD Quant AI in an append-only, tamper-evident audit trail (CON-085). It enables reconstruction of who did what, when, from where, and why, including the complete decision chain behind every live order (QAS-05).

## 38.2 Audit Record Structure

| Field | Description |
|---|---|
| audit_id | Monotonic sequence number per audit stream plus UUID |
| timestamp | UTC, microseconds |
| actor | Principal (user, service account, API client, strategy deployment, system) |
| actor_context | IP address, user agent, session identifier, authentication method, impersonation flag |
| action | Stable action code (e.g. `order.submit`, `risk.limit.update`, `user.role.grant`) |
| category | AUTHENTICATION, AUTHORIZATION, TRADING, RISK, CONFIGURATION, USER_ADMIN, DATA_ACCESS, AI, SYSTEM, SECURITY |
| target | (entity type, entity identifier) |
| outcome | SUCCESS, FAILURE, DENIED |
| before / after | Changed fields (sensitive values masked) |
| reason | User-supplied justification when required |
| correlation_id | Link to logs and events |
| previous_hash | Hash of the previous record in the stream |
| record_hash | SHA-256 over record content and previous_hash |

## 38.3 Audited Actions (Minimum)

| Category | Actions |
|---|---|
| Authentication | Login success/failure, logout, MFA enrollment/challenge, password change/reset, session revocation, API key creation/revocation |
| Authorization | Role grant/revoke, permission change, access denied on protected resource, privilege elevation |
| Trading | Order submit/modify/cancel (manual and API), deployment create/approve/start/pause/stop/flatten, kill switch trigger/release, maintenance mode |
| Risk | Profile/limit create/update/activate, exception request/approval, breach acknowledgment |
| Configuration | Any configuration or setting change, connection create/update, credential rotation (without values) |
| User Admin | User create/update/suspend/delete, workspace membership changes |
| Data Access | Export, report generation, bulk queries of sensitive data, audit log access |
| AI | Model promotion/rollback, AI Copilot actions that change state, prompt template changes |
| System | Deployments, restarts, backups, restores, plugin install/uninstall |

Automated strategy orders are recorded in OMS records with full attribution; the audit trail records a per-order summary entry linking to them.

## 38.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-38001 | The system shall record an audit entry for every action listed in 38.3 synchronously with the action's commit; if the audit write fails, the action shall fail. | M | T |
| FR-38002 | The system shall hash-chain audit records per stream (record_hash = SHA-256(content ‖ previous_hash)). | M | T |
| FR-38003 | The system shall periodically (default hourly) anchor the latest stream hash to a separate write-once store and provide integrity verification of any range of records. | M | T |
| FR-38004 | The system shall prevent modification and deletion of audit records within retention by any role (CON-085), including through database-level controls. | M | T |
| FR-38005 | The system shall mask sensitive values in before/after fields while recording that a change occurred. | M | T |
| FR-38006 | The system shall provide audit search by time, actor, action, category, target, outcome, and correlation identifier, returning results within 5 s for a 30-day window. | M | T |
| FR-38007 | The system shall provide an order decision-chain view assembling: market data reference, signal, strategy version and parameters, model version and inference record, risk decision, automation policy, approving user, and execution reports. | M | T |
| FR-38008 | The system shall restrict audit access to Auditor, Compliance Officer, Security Administrator, and System Administrator roles; access to the audit trail shall itself be audited. | M | T |
| FR-38009 | The system shall export audit ranges with an integrity manifest enabling offline verification. | M | T |
| FR-38010 | The system shall retain audit records for the retention period of CON-062 (default 7 years) with automated archival to lower-cost storage while remaining verifiable. | M | T |
| FR-38011 | The system shall raise a Critical security alert when integrity verification fails. | M | T |
| FR-38012 | The system shall require a reason for designated sensitive actions (kill switch release, limit loosening, risk exception, role grant of administrative roles, live deployment approval). | M | T |

## 38.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-38001 | Given an audit record modified directly in storage, then integrity verification of its range fails and a Critical alert is raised. | FR-38002, FR-38011 |
| AC-38002 | Given a System Administrator attempting to delete an audit record through any interface, then the operation is denied and audited. | FR-38004 |
| AC-38003 | Given any live order, then the decision-chain view presents all elements listed in FR-38007 that apply to it. | FR-38007 |

---

*End of Chapter 38 – Audit*
