# Chapter 17 – Dependencies

## 17.1 Purpose

This chapter identifies the external and internal dependencies of JD Quant AI. A dependency is any system, service, dataset, component, or organizational input whose availability or behavior affects the platform's ability to satisfy its requirements.

Each dependency is assigned an identifier with the prefix `DEP-`, a criticality classification, and the required degradation behavior when the dependency is unavailable.

## 17.2 Criticality Classification

| Criticality | Definition |
|---|---|
| Critical | Unavailability prevents live trading or risks financial loss. Requires automated detection, alerting within 30 seconds, and a defined safe-state response. |
| High | Unavailability prevents a major capability but does not endanger open positions. Requires alerting within 5 minutes. |
| Medium | Unavailability degrades a capability. Requires alerting within 15 minutes. |
| Low | Unavailability affects convenience features only. Logged and reported. |

## 17.3 External Dependencies

| ID | Dependency | Consumer Engine(s) | Criticality | Required Degradation Behavior |
|---|---|---|---|---|
| DEP-001 | Exchange trading APIs | EMS, OMS | Critical | Stop new order submission to the affected venue; keep resting orders under watch; escalate per Chapter 51. |
| DEP-002 | Exchange market data feeds | MDE | Critical | Mark affected instruments STALE; suspend strategies depending on them per configured staleness policy. |
| DEP-003 | Broker APIs | EMS, OMS, PME | Critical | Same as DEP-001 for the affected broker accounts. |
| DEP-004 | Third-party market data providers | MDE, BTE | High | Fail over to secondary provider where configured; otherwise mark data STALE. |
| DEP-005 | News and economic calendar providers | SE, ARE | Medium | Strategies consuming news continue with a "news unavailable" context flag. |
| DEP-006 | Identity providers (SSO / OIDC) | SAE | High | Allow break-glass local administrator login only; deny new SSO sessions; existing sessions continue until expiry. |
| DEP-007 | Notification delivery services (email, SMS, chat, push) | NCE | Medium | Queue notifications for retry; fall back to secondary channel for critical alerts. |
| DEP-008 | External LLM inference services | AI Copilot, Prompt Engine, Research Assistant | Low | Disable AI assistant features with an explanatory message; no impact on trading. |
| DEP-009 | Cloud object storage | BTE, TPE, Data Export, Backup | High | Queue writes; pause jobs requiring storage; retain local buffers within configured limits. |
| DEP-010 | Time synchronization service | All | Critical | Raise alert when offset exceeds thresholds (CON-165); block live trading start while offset exceeds the critical threshold. |
| DEP-011 | Certificate authorities | SAE | High | Alert 30 days before certificate expiry; block connections with invalid certificates. |
| DEP-012 | Package and container registries | Build pipeline | Low (runtime) / High (release) | Releases blocked; running system unaffected. |

## 17.4 Internal Dependencies

The following table defines mandatory runtime dependencies between logical engines. An arrow "A → B" indicates that A requires B.

| ID | Dependency | Nature | Criticality |
|---|---|---|---|
| DEP-100 | SE → MDE | Market data subscription | Critical |
| DEP-101 | SE → OMS | Order intent submission | Critical |
| DEP-102 | OMS → RMS | Pre-trade risk decision | Critical |
| DEP-103 | OMS → EMS | Order routing and venue interaction | Critical |
| DEP-104 | EMS → Exchange/Broker adapters | Venue communication | Critical |
| DEP-105 | PME → OMS | Fill events | Critical |
| DEP-106 | RMS → PME | Positions and exposures | Critical |
| DEP-107 | All engines → CME | Event transport | Critical |
| DEP-108 | All engines → CCE | Configuration | High |
| DEP-109 | All engines → SAE | Authentication and authorization | Critical |
| DEP-110 | All engines → LME | Logging | Medium |
| DEP-111 | BTE → MDE (historical) | Historical data access | High |
| DEP-112 | IPE → MME | Model artifact retrieval | High |
| DEP-113 | TPE → FSE | Training features | High |
| DEP-114 | IPE → FSE | Online features | High |
| DEP-115 | AAE → PME, OMS | Analytics inputs | Medium |
| DEP-116 | NCE → CME | Alert events | High |
| DEP-117 | WFE → Task Engine | Job execution | High |

Circular runtime dependencies between engines are prohibited. Where bidirectional information flow is required, one direction shall be implemented through asynchronous events.

## 17.5 Data Dependencies

| ID | Dependency | Description |
|---|---|---|
| DEP-200 | Instrument reference data | Required before any market data, order, or position can be processed for an instrument. |
| DEP-201 | Venue trading calendars | Required for session-aware scheduling and order validation. |
| DEP-202 | FX conversion rates | Required for multi-currency portfolio valuation and reporting. |
| DEP-203 | Fee schedules | Required for accurate backtesting, paper trading, and P&L attribution. |
| DEP-204 | Corporate action data | Required for adjusted historical prices and equity position adjustments. |

## 17.6 Documentation Dependencies

| ID | Dependency | Description |
|---|---|---|
| DEP-300 | Volume 1 – PRD | Source of product intent for this SRS. |
| DEP-301 | Volume 3 – System Architecture | Consumes Parts C–F of this SRS. |
| DEP-302 | Volume 6 – Database Design | Consumes data entity definitions in Part C. |
| DEP-303 | Volume 7 – Internal API & Event Specification | Consumes Part E. |
| DEP-304 | Volume 9 – AI/ML Architecture | Consumes Chapters 52–59 and 63. |

## 17.7 Dependency Management Requirements

- Every external dependency shall be accessed through an adapter with timeouts, retry policy, and circuit breaker configured.
- Every Critical and High dependency shall expose a health indicator consumed by the Health Engine (Chapter 62).
- The dependency register shall record version, owner, license, and support status for every third-party component.
- Removal or replacement of a Critical dependency shall require an architecture decision record.

## 17.8 Chapter Summary

This chapter defined the external, internal, data, and documentation dependencies of JD Quant AI, their criticality, and the degradation behavior required when each dependency is unavailable.

---

*End of Chapter 17 – Dependencies*
