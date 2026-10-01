# Chapter 82 – Message Bus

## 82.1 Purpose

This chapter specifies the Central Messaging Engine (CME), the event backbone through which engines communicate asynchronously (CON-002, Chapter 10.8).

## 82.2 Event Envelope

| Field | Description |
|---|---|
| event_id | UUID |
| event_type | Dot-notation name (4.14), e.g. `order.state.changed` |
| schema_version | Integer |
| occurred_at | Business time (simulated time in backtests) |
| published_at | Wall-clock publication time |
| producer | Component and instance |
| workspace_id | Tenant scope |
| partition_key | Ordering key (e.g. account_id, instrument_id) |
| sequence | Monotonic per partition |
| correlation_id / causation_id | Tracing (NFR-78003) |
| payload | Typed body per schema |

## 82.3 Topic Classes

| Class | Examples | Delivery | Retention |
|---|---|---|---|
| Trading state | `order.*`, `position.*`, `deployment.*`, `killswitch.*`, `risk.*` | At-least-once, ordered per partition, durable | ≥ 7 days on bus; persisted permanently in event store |
| Market data | `marketdata.*` | Low-latency, may be non-durable for real-time; ordered per instrument | Short (minutes) on bus; persisted by MDE |
| Operational | `job.*`, `health.*`, `connection.*` | At-least-once | 7 days |
| Notifications | `notification.*` | At-least-once | 7 days |
| AI | `model.*`, `signal.*`, `inference.*` | At-least-once | 7 days |

## 82.4 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-82001 | All events shall use the envelope of 82.2 and be validated against a registered schema at publish time. | M | T |
| API-82002 | The bus shall guarantee ordering per partition key and at-least-once delivery for durable topics; consumers shall be idempotent using event_id (NFR-68006). | M | T |
| API-82003 | Trading-state events shall be persisted to an append-only event store supporting replay by time range and partition (FR-51008). | M | T |
| API-82004 | The bus shall support consumer groups, independent consumer offsets, and replay from an offset or timestamp. | M | T |
| API-82005 | Consumer lag shall be monitored per consumer group (Chapter 60). | M | T |
| API-82006 | Poison messages that fail processing after N attempts (default 5) shall be moved to a dead-letter topic with error details and alerted. | M | T |
| API-82007 | In single-node (T1) deployments, the bus may be an embedded implementation providing the same semantics (CON-009). | M | T |
| API-82008 | Event schema evolution shall follow CON-182 and be enforced by the schema registry compatibility checks. | M | T |
| API-82009 | Publishing trading-state events shall be atomic with the corresponding state change (transactional outbox or equivalent), so that no state change occurs without its event and vice versa. | M | T |
| API-82010 | Workspace isolation shall be enforced on subscriptions exposed to plugins or external consumers. | M | T |

---

*End of Chapter 82 – Message Bus*
