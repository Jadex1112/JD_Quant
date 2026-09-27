# Chapter 18 – Quality Attributes

## 18.1 Purpose

This chapter defines the quality attributes that JD Quant AI shall exhibit and establishes their relative priority. Quality attributes describe how well the system performs its functions. Measurable thresholds for each attribute are specified as Non-Functional Requirements in Part D; this chapter provides the quality model, priority ordering, and quality attribute scenarios that inform architectural trade-offs.

The quality model is aligned with ISO/IEC 25010.

## 18.2 Quality Attribute Priority

When quality attributes conflict, architects shall resolve trade-offs according to the following priority:

| Priority | Attribute | Justification |
|---|---|---|
| 1 | Correctness & Integrity | Incorrect orders, positions, or P&L cause direct financial loss. |
| 2 | Safety (Risk Containment) | Uncontrolled exposure must be structurally prevented. |
| 3 | Security | Credential or account compromise leads to loss of funds and data. |
| 4 | Reliability & Recoverability | Trading must continue or fail safe under component failure. |
| 5 | Auditability | Every decision must be reconstructable. |
| 6 | Performance & Latency | Execution quality depends on timeliness. |
| 7 | Availability | Operational continuity during market hours. |
| 8 | Observability | Operators must understand system state at all times. |
| 9 | Scalability | Growth in users, strategies, and data. |
| 10 | Maintainability & Extensibility | Long-term evolution of the platform. |
| 11 | Usability & Accessibility | Productivity of all user classes. |
| 12 | Portability | Deployment flexibility. |

## 18.3 Quality Attribute Scenarios

Each scenario follows the structure: Source, Stimulus, Environment, Artifact, Response, Response Measure.

### QAS-01 — Duplicate Order Prevention (Correctness)

| Element | Value |
|---|---|
| Source | Execution Engine |
| Stimulus | Network timeout after sending a new order to a venue |
| Environment | Live trading, normal operation |
| Artifact | OMS, EMS |
| Response | The order enters UNKNOWN state; the EMS queries the venue using the client order identifier before any resubmission. |
| Response Measure | Zero duplicate venue orders created as a result of timeouts across 10,000 injected timeout tests. |

### QAS-02 — Risk Limit Breach (Safety)

| Element | Value |
|---|---|
| Source | Strategy |
| Stimulus | Order intent whose fill would exceed the account's maximum position limit |
| Environment | Live trading |
| Artifact | RMS |
| Response | The order is rejected before reaching the venue with reason code RISK_POSITION_LIMIT; an alert is raised. |
| Response Measure | 100% of limit-breaching intents rejected; rejection decision within 1 ms p99 of receipt by RMS. |

### QAS-03 — Venue Disconnection (Reliability)

| Element | Value |
|---|---|
| Source | Exchange |
| Stimulus | Trading connection drops |
| Environment | Live trading with open orders |
| Artifact | EMS, Exchange Connectivity |
| Response | Reconnection with exponential backoff; order and position reconciliation on reconnect; strategies notified of connectivity state. |
| Response Measure | Reconnection attempted within 1 s; reconciliation complete within 30 s of reconnection; zero lost fills. |

### QAS-04 — Credential Theft Attempt (Security)

| Element | Value |
|---|---|
| Source | External attacker |
| Stimulus | Attempt to read venue API secrets through the user interface, API, logs, or exports |
| Environment | Production |
| Artifact | SAE, Secret Store |
| Response | Secrets are never returned after entry; access attempts are logged and alerted. |
| Response Measure | Zero secret values present in any API response, log, or export in automated secret-scanning tests. |

### QAS-05 — Decision Reconstruction (Auditability)

| Element | Value |
|---|---|
| Source | Compliance Officer |
| Stimulus | Request to explain why a specific live order was placed |
| Environment | Production, up to seven years after the event |
| Artifact | Audit, OMS, SE, IPE |
| Response | The system presents the triggering market data reference, signal, strategy version, model version (if any), risk decision, and approving automation policy. |
| Response Measure | Complete decision chain available for 100% of sampled orders within 10 s query time. |

### QAS-06 — Market Data Burst (Performance)

| Element | Value |
|---|---|
| Source | Exchange feed |
| Stimulus | Market data rate spikes to 10× normal load for 60 s |
| Environment | Live trading |
| Artifact | MDE, SE |
| Response | Data processed without loss; conflation applied only to consumers configured for conflation. |
| Response Measure | p99 internal propagation latency remains below 25 ms; zero dropped messages for non-conflated subscribers. |

### QAS-07 — New Venue Integration (Extensibility)

| Element | Value |
|---|---|
| Source | Developer |
| Stimulus | Requirement to add support for a new exchange |
| Environment | Development |
| Artifact | Exchange Connectivity |
| Response | A new adapter is implemented against the standard interface; no change to OMS, EMS core, RMS, or SE. |
| Response Measure | Zero modifications to non-adapter modules; adapter passes the conformance test suite. |

### QAS-08 — Operator Situational Awareness (Observability)

| Element | Value |
|---|---|
| Source | Operations Engineer |
| Stimulus | A strategy stops generating orders unexpectedly |
| Environment | Production |
| Artifact | Health Engine, Monitoring, Diagnostics |
| Response | The dashboard shows strategy state, last evaluation time, data freshness, and blocking reasons. |
| Response Measure | Root cause category identifiable within 2 minutes from the dashboard without log access. |

### QAS-09 — Backtest Reproducibility (Correctness)

| Element | Value |
|---|---|
| Source | Researcher |
| Stimulus | Re-run of a backtest completed six months earlier |
| Environment | Research |
| Artifact | BTE |
| Response | Identical dataset version, strategy version, configuration, and random seed are restored. |
| Response Measure | Bit-identical trade list and performance metrics. |

### QAS-10 — Role Onboarding (Usability)

| Element | Value |
|---|---|
| Source | New Quantitative Trader |
| Stimulus | First login |
| Environment | Production |
| Artifact | UI |
| Response | Role-appropriate workspace is shown with guided tour available. |
| Response Measure | User completes a paper trade from a template strategy within 15 minutes without assistance. |

## 18.4 Quality Attribute Definitions

| Attribute | Definition within JD Quant AI |
|---|---|
| Correctness | Outputs (orders, fills, positions, P&L, metrics) exactly match the specified calculations and venue-confirmed facts. |
| Integrity | Data cannot be modified without authorization and every modification is traceable. |
| Safety | The system prevents, limits, and contains financial exposure beyond configured policies, including under failure. |
| Security | Confidentiality, integrity, and availability of assets, credentials, and data are protected against threats. |
| Reliability | The system performs its functions under stated conditions for a stated period. |
| Recoverability | The system restores a consistent state after failure without data loss for committed transactions. |
| Auditability | Every material action is attributable to an actor and reconstructable after the fact. |
| Performance | The system meets latency and throughput targets under defined load. |
| Availability | The system is operational and accessible when required. |
| Observability | Internal state can be inferred from external outputs (metrics, logs, traces, events). |
| Scalability | Capacity increases proportionally with resources added. |
| Maintainability | The system can be modified with low effort and low risk of regression. |
| Extensibility | New capabilities can be added through defined extension points. |
| Usability | Users achieve goals effectively, efficiently, and with satisfaction. |
| Accessibility | The interface is usable by people with a wide range of abilities (WCAG 2.2 AA). |
| Portability | The system can be deployed across supported environments without modification. |

## 18.5 Quality Assurance Approach

Quality attributes shall be verified through:

- Automated unit, integration, contract, and end-to-end tests.
- Performance and load testing against Part D thresholds.
- Chaos and fault-injection testing for reliability scenarios.
- Security testing including static analysis, dependency scanning, and penetration testing.
- Accessibility audits against WCAG 2.2 AA.
- Architectural fitness functions enforcing Chapter 15 constraints.

## 18.6 Chapter Summary

This chapter established the quality model, attribute priority, and representative quality attribute scenarios for JD Quant AI. Part D specifies measurable thresholds for each attribute.

*Part B – Overall Description is complete.*

---

*End of Chapter 18 – Quality Attributes*
