# Chapter 36 – Logging

## 36.1 Purpose

The Logging & Monitoring Engine (LME) collects, structures, stores, and makes searchable the operational logs emitted by every platform component and every hosted strategy. Logs support debugging, incident investigation, and operational analytics. Tamper-evident business audit records are specified separately in Chapter 38.

## 36.2 Log Record Structure

| Field | Type | Description |
|---|---|---|
| timestamp | Timestamp (μs, UTC) | Emission time |
| level | Enum: TRACE, DEBUG, INFO, WARN, ERROR, FATAL | Required |
| component | String | Engine / service name |
| instance_id | String | Process or container identifier |
| environment | Enum (Chapter 14.3) | Required |
| message | String | Human-readable |
| event_code | String | Stable machine-readable code |
| correlation_id | String | Propagated across requests and events |
| trace_id / span_id | String | Distributed tracing identifiers |
| principal_id | String | Acting user or service (if any) |
| workspace_id | UUID | Tenant (if any) |
| entity_refs | Map | e.g. order_id, deployment_id, job_id |
| attributes | Map | Structured context |
| error | (type, message, stack_ref) | For ERROR and FATAL |

## 36.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-36001 | The system shall emit all logs as structured records conforming to 36.2. | M | I |
| FR-36002 | The system shall propagate a correlation identifier from every inbound request or event through all resulting processing, events, jobs, and outbound venue requests. | M | T |
| FR-36003 | The system shall redact secrets, credentials, authentication tokens, and configured sensitive fields from log records before persistence (CON-080), using both field-name and pattern-based redaction. | M | T |
| FR-36004 | The system shall allow log levels to be changed at runtime per component and per deployment without restart; temporary level changes shall revert after a configurable duration (default 60 min). | M | T |
| FR-36005 | The system shall capture strategy log output (context.log) tagged with deployment identifier and make it viewable per deployment in near real time (< 2 s). | M | T |
| FR-36006 | The system shall provide log search by time range, level, component, correlation identifier, entity reference, and full-text message, returning results within 5 s for a 24-hour window. | M | T |
| FR-36007 | The system shall provide live log tailing with filters. | M | D |
| FR-36008 | The system shall retain logs per level: ERROR/FATAL 1 year, WARN/INFO 90 days, DEBUG/TRACE 7 days (configurable). | M | T |
| FR-36009 | The system shall protect log emission from blocking the critical trading path: logging on the critical path shall be asynchronous with bounded buffers; buffer overflow shall drop DEBUG/TRACE first and increment a dropped-log counter. | M | T |
| FR-36010 | The system shall export logs to external log platforms through standard protocols. | S | T |
| FR-36011 | The system shall restrict log access by role; strategy logs are visible to the strategy owner and users with deployment view permission. | M | T |
| FR-36012 | The system shall generate log-based metrics (error rate per component, warning rate) consumed by Monitoring (Chapter 60). | M | T |

## 36.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-36001 | Given an API request that results in an order and a venue call, then all related log records share the same correlation identifier. | FR-36002 |
| AC-36002 | Given a log statement including an API secret, then the persisted record contains the redaction marker instead of the secret. | FR-36003 |

---

*End of Chapter 36 – Logging*
