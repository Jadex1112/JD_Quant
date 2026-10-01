# Chapter 61 – Diagnostics

## 61.1 Purpose

The Diagnostics module helps operators, traders, and support engineers determine why the platform or a strategy behaves as it does. It provides targeted inspection tools, automated diagnostic checks, "why not trading" explanations, and diagnostic bundles for support (QAS-08).

## 61.2 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-61001 | The system shall provide a "Why is this deployment not trading?" diagnostic evaluating every eligibility condition (BR-19-02), data freshness, strategy errors, risk state, automation policy, rate limits, and last signal/order times, presenting pass/fail per condition with remediation hints. | M | T |
| FR-61002 | The system shall provide an order diagnostic showing the full timeline of an order across components with latencies (FR-22061), risk checks, venue requests/responses (FR-45011), and reconciliation actions. | M | T |
| FR-61003 | The system shall provide a connection diagnostic that runs connectivity tests (DNS, TLS, authentication, time offset, latency sample, permission check) on demand. | M | T |
| FR-61004 | The system shall provide a market data diagnostic for an instrument: current feed status, last messages, gap history, book validity, and comparison across sources. | M | T |
| FR-61005 | The system shall provide a strategy inspector for RUNNING deployments showing parameters, current indicator values, state variables (as exposed by the strategy), event queue, and callback latency. | S | T |
| FR-61006 | The system shall run scheduled self-checks (configuration validity, secret accessibility, storage capacity, clock sync, certificate validity, backup freshness) and report results on the health dashboard. | M | T |
| FR-61007 | The system shall generate a diagnostic bundle (logs for a time range, configuration with secrets masked, metrics snapshot, component versions, recent incidents) downloadable by authorized users for support. | M | T |
| FR-61008 | The system shall ensure diagnostic bundles contain no secrets or credentials (CON-080), verified by automated scanning before download. | M | T |
| FR-61009 | The system shall provide a replay-to-debug facility: replaying market data and events of a live time window into a sandbox copy of a deployment to reproduce behavior. | S | T |
| FR-61010 | The system shall record diagnostics usage in the audit trail. | M | T |

## 61.3 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-61001 | Given a deployment whose instrument feed is STALE, then the diagnostic reports data freshness as the failing condition. | FR-61001 |
| AC-61002 | Given a diagnostic bundle, then automated scanning finds no credential patterns. | FR-61008 |

---

*End of Chapter 61 – Diagnostics*
