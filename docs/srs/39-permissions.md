# Chapter 39 – Permissions

## 39.1 Purpose

The Permissions module, part of the Security & Authentication Engine (SAE), implements authorization for every protected operation across all interfaces (CON-014, 13.20). It defines permissions, roles, resource scopes, and policy evaluation, including separation of duties and time-bound elevation.

## 39.2 Authorization Model

JD Quant AI uses Role-Based Access Control (RBAC) extended with resource scoping and attribute conditions:

```text
Principal ──has──▶ RoleBinding(role, scope) ──grants──▶ Permission(resource, action)
                                                          │
                                        optional Condition (attribute-based)
```

- **Permission**: `resource:action`, e.g. `order:create`, `risk.limit:approve`.
- **Role**: named set of permissions.
- **Scope**: WORKSPACE, PORTFOLIO, ACCOUNT, STRATEGY, or GLOBAL (platform-wide, administrators only).
- **Condition**: attribute constraints, e.g. `account.mode == PAPER`, `order.notional <= 10000`, `time within business hours`.

## 39.3 Resources and Actions

| Resource | Actions |
|---|---|
| account | view, create, update, suspend, delete |
| connection / credential | view, create, update, rotate, delete (credential values never viewable) |
| deployment | view, create, start, pause, stop, flatten, approve, retire |
| order | view, create, modify, cancel, cancel_all |
| position | view, transfer |
| killswitch | trigger, release |
| strategy | view, create, update, publish, promote, delete |
| backtest / optimization | view, run, cancel, delete |
| risk.profile / risk.limit | view, create, update, approve, disable |
| risk.exception | request, approve |
| portfolio | view, create, update, delete |
| marketdata | view, subscribe, import, manage_reference |
| model | view, train, evaluate, promote, rollback, delete |
| ai.copilot | use, execute_actions |
| report | view, generate, schedule, manage_templates |
| audit | view, export, verify |
| user / role | view, create, update, suspend, delete, assign |
| settings / configuration | view, update |
| plugin | view, install, configure, uninstall |
| workflow / schedule | view, create, update, run, delete |
| system | maintenance_mode, backup, restore, view_health |

## 39.4 Built-in Roles

| Role | Summary of Permissions |
|---|---|
| SYSTEM_ADMIN | All user/role/settings/configuration/plugin/system actions; no trading or risk approval by default |
| QUANT_RESEARCHER | strategy (all but promote to LIVE_APPROVED), backtest, optimization, marketdata view/subscribe, model view/train/evaluate, ai.copilot use, paper deployment and paper orders |
| QUANT_TRADER | deployment (all but approve own), order (all), position view, killswitch trigger, strategy view, portfolio view, ai.copilot use |
| PORTFOLIO_MANAGER | portfolio (all), report generate/schedule, deployment view, rebalance approval, order view |
| RISK_MANAGER | risk.* (all), killswitch trigger/release, deployment approve/pause/stop/flatten, order cancel_all, report generate |
| AI_ENGINEER | model (all), feature store, training/inference pipelines, ai.copilot use |
| DATA_ENGINEER | marketdata (all), data import/export, connection view |
| OPERATIONS_ENGINEER | system view_health, maintenance_mode, job management, logs, workflow run, deployment pause |
| SECURITY_ADMIN | credential rotate, user suspend, session revoke, audit view, security settings |
| COMPLIANCE_OFFICER | audit view/export/verify, report (compliance), read-only across trading records |
| VIEWER | view on dashboards and reports within scope |
| AUDITOR | audit view/export/verify, read-only configuration history |

## 39.5 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-39001 | The system shall evaluate authorization for every request at the application service boundary, for all interfaces (UI, REST, streaming, internal API for user-initiated operations). | M | T |
| FR-39002 | The system shall deny any action not explicitly granted (CON-083). | M | T |
| FR-39003 | The system shall provide the built-in roles of 39.4, which cannot be deleted but can be cloned and customized. | M | T |
| FR-39004 | The system shall allow administrators to create custom roles from the permission catalog. | M | T |
| FR-39005 | The system shall bind roles to principals at a scope; a binding at WORKSPACE scope applies to all resources in the workspace. | M | T |
| FR-39006 | The system shall support attribute-based conditions on role bindings as defined in 39.2. | S | T |
| FR-39007 | The system shall enforce separation of duties: the principal who created or last modified a strategy version, risk exception request, or live deployment cannot approve it (CON-203, with single-user waiver). | M | T |
| FR-39008 | The system shall define privileged actions (killswitch release, risk.limit approve, role assign of admin roles, credential create/rotate, system restore, live deployment approve, audit export) that require step-up MFA within the last 10 minutes (CON-086). | M | T |
| FR-39009 | The system shall support time-bound elevation: a user may request a role for a limited duration (max 24 h) subject to approval; the binding expires automatically. | S | T |
| FR-39010 | The system shall evaluate authorization decisions within 1 ms p99 using cached, invalidated-on-change policy data. | M | T |
| FR-39011 | The system shall propagate permission changes to all instances within 5 s. | M | T |
| FR-39012 | The system shall provide an effective-permissions view for any principal and a "who can perform action X on resource Y" query. | M | D |
| FR-39013 | The system shall hide or disable user interface controls for actions the user is not permitted to perform (13.22), while still enforcing authorization server-side. | M | D |
| FR-39014 | The system shall support periodic access reviews: generating a review campaign listing all role bindings for reviewer attestation, and revoking bindings not attested within the campaign window when configured. | S | T |
| FR-39015 | The system shall scope API keys to a subset of the owning user's permissions and optionally to IP allowlists. | M | T |
| FR-39016 | The system shall allow administrators to impersonate a user for support purposes only with the user's consent or a Security Administrator approval, in read-only mode, with every impersonated action audited. | C | T |

## 39.6 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-39001 | Given a QUANT_RESEARCHER, when they call the live order API directly, then the request is denied with 403 and audited. | FR-39001, FR-39002 |
| AC-39002 | Given a trader who created a live deployment, when they attempt to approve it in a multi-user workspace, then approval is denied. | FR-39007 |
| AC-39003 | Given a role revoked from a user, then within 5 s all instances deny the user's actions requiring it. | FR-39011 |

---

*End of Chapter 39 – Permissions*
