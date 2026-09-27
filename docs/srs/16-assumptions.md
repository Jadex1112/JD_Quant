# Chapter 16 – Assumptions

## 16.1 Purpose

This chapter records the assumptions upon which the requirements of this specification are based. An assumption is a condition believed to be true that, if proven false, may invalidate or alter one or more requirements.

Each assumption is assigned an identifier with the prefix `ASM-`, an owner responsible for validating it, and an impact statement describing the consequence of the assumption being false. Assumptions shall be reviewed at every major release and whenever an affected requirement changes.

## 16.2 Assumption Lifecycle

Every assumption shall hold one of the following states:

| State | Meaning |
|---|---|
| Open | Believed true but not yet validated |
| Validated | Confirmed true through evidence recorded in the traceability register |
| Invalidated | Proven false; affected requirements must be revised |
| Retired | No longer relevant due to scope change |

An invalidated assumption shall trigger a change request under the scope governance process of Chapter 2.12.

## 16.3 Market and Venue Assumptions

| ID | Assumption | Owner | Impact if False |
|---|---|---|---|
| ASM-001 | Supported venues and brokers provide documented programmatic APIs for market data, order entry, order status, account balances, and positions. | Product Management | Adapters for undocumented venues require reverse engineering; venue excluded from support. |
| ASM-002 | Venues publish instrument metadata (tick size, lot size, minimum notional, trading hours) through an API or a machine-readable file. | Data Engineering | Manual instrument configuration required; Chapter 20 metadata requirements extended. |
| ASM-003 | Venue-reported fills and balances are authoritative and eventually consistent within 60 seconds of the underlying event. | Trading Operations | Reconciliation windows in Chapters 21 and 28 must be lengthened. |
| ASM-004 | Venues provide either a streaming order-update channel or a polling interface capable of retrieving order status at least once every five seconds. | Engineering | Order state latency increases; OMS timeout values must be revised. |
| ASM-005 | Venue sandbox or testnet environments are available for integration testing of most supported venues. | QA | Integration testing must rely on recorded sessions and simulators only. |
| ASM-006 | Venue rate limits are published and stable between announced changes. | Engineering | Adaptive rate limiting becomes mandatory rather than optional. |

## 16.4 Data Assumptions

| ID | Assumption | Owner | Impact if False |
|---|---|---|---|
| ASM-020 | Historical market data of sufficient depth and quality can be licensed or collected for supported instruments. | Data Engineering | Backtesting capability limited for affected instruments. |
| ASM-021 | Data providers deliver timestamps in, or convertible to, UTC. | Data Engineering | Provider-specific time conversion rules required. |
| ASM-022 | Historical data volumes for the initial release do not exceed 50 TB of compressed storage per deployment. | Architecture | Storage tiering and archival strategy must be revised. |
| ASM-023 | Corporate action data is available for supported equity markets. | Data Engineering | Adjusted-price research is unavailable for affected markets. |

## 16.5 User and Organizational Assumptions

| ID | Assumption | Owner | Impact if False |
|---|---|---|---|
| ASM-040 | Users possess working knowledge of financial markets and quantitative trading concepts. | Product Management | Additional onboarding, tutorials, and guardrails required. |
| ASM-041 | Users are responsible for obtaining venue accounts, API credentials, and data licenses. | Product Management | Platform scope expands to account provisioning, which is out of scope (Chapter 2.9). |
| ASM-042 | Organizations deploying the platform define their own risk policies; the platform provides enforcement mechanisms and sensible defaults. | Risk | Platform must ship prescriptive policy templates. |
| ASM-043 | Initial deployments serve between 1 and 200 named users per installation. | Architecture | Identity and session scaling requirements must be revised. |
| ASM-044 | Single-user installations (one trader acting in all roles) are a supported and common deployment scenario. | Product Management | Four-eyes controls (CON-203) cannot be waived. |

## 16.6 Technology and Infrastructure Assumptions

| ID | Assumption | Owner | Impact if False |
|---|---|---|---|
| ASM-060 | Production hosts have reliable internet connectivity with round-trip latency to supported venues below 250 ms. | Operations | Latency-sensitive strategies unsupported from affected locations. |
| ASM-061 | A time source accurate to within 10 ms of UTC is available to production hosts. | Operations | Event ordering guarantees in Part F weakened. |
| ASM-062 | Commodity server hardware and GPU instances are available for AI training workloads. | AI Engineering | Training capability restricted to CPU with longer run times. |
| ASM-063 | Container orchestration or equivalent process supervision is available in production deployments. | Operations | Automatic restart and scaling requirements must be met by alternative means. |
| ASM-064 | Large language model inference is available either through an approved external service or a self-hosted model. | AI Engineering | AI Copilot, Prompt Engine, and Research Assistant are disabled. |

## 16.7 AI Assumptions

| ID | Assumption | Owner | Impact if False |
|---|---|---|---|
| ASM-080 | Predictive models trained on historical data can provide statistically significant, though non-guaranteed, information about future market behavior for some instruments and horizons. | Research | AI signal capabilities provide research value only. |
| ASM-081 | Model performance degrades over time due to regime change, making continuous monitoring and periodic retraining necessary. | AI Engineering | Drift monitoring requirements (Chapter 57) could be relaxed. |
| ASM-082 | Language models may produce incorrect output; all AI-generated content affecting trading or configuration therefore requires validation or human confirmation. | AI Engineering | None; this assumption is conservative by design. |

## 16.8 Regulatory Assumptions

| ID | Assumption | Owner | Impact if False |
|---|---|---|---|
| ASM-100 | Organizations using the platform for live trading hold any licenses required in their jurisdiction. | Compliance | Out of platform scope; documented in terms of use. |
| ASM-101 | A default record retention period of seven years satisfies the majority of target jurisdictions. | Compliance | Retention defaults (CON-062) must be revised. |

## 16.9 Assumption Governance

Assumptions shall be:

- Reviewed at every major release planning cycle.
- Linked to every requirement that depends on them in the traceability register (Chapter 96).
- Updated to "Validated" only when objective evidence is recorded.
- Escalated to the architecture governance board when invalidated.

## 16.10 Chapter Summary

This chapter documented the market, data, organizational, technology, AI, and regulatory assumptions on which this specification relies, together with their owners and the consequences of their invalidation.

---

*End of Chapter 16 – Assumptions*
