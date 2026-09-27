# Chapter 81 – Internal APIs

## 81.1 Purpose

This chapter specifies requirements for synchronous interfaces between internal engines, including the streaming interface to clients. Detailed interface definitions are specified in Volume 7.

## 81.2 Interface Classes

| Class | Usage | Example |
|---|---|---|
| Synchronous request/response (internal RPC) | Operations requiring an immediate answer | OMS → RMS pre-trade check; SE → IPE inference; any → SAE authorization |
| Asynchronous events (Chapter 82) | State change notification, fan-out | `order.fill`, `position.updated` |
| Streaming (client-facing) | Real-time push to UI and API clients | Market data, order updates, notifications |
| In-process interfaces | Engines co-located in one process (T1) | Same contracts, direct invocation |

## 81.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-81001 | Every internal synchronous interface shall be defined in an interface definition language or schema with versioning, independent of transport (in-process or network) to satisfy CON-009. | M | I |
| API-81002 | Internal calls shall propagate authentication context (principal, workspace, roles) and correlation identifiers. | M | T |
| API-81003 | Internal calls shall declare timeouts; callers shall handle timeout, unavailability, and error responses explicitly (NFR-68007). | M | I |
| API-81004 | Critical-path internal interfaces (OMS↔RMS, OMS↔EMS) shall use binary serialization or in-process invocation to meet Chapter 65 budgets. | M | T |
| API-81005 | The client streaming interface shall use a WebSocket (or equivalent) protocol supporting authenticated connections, subscription management (subscribe/unsubscribe per channel and key), heartbeats, sequence numbers per channel, and snapshot-plus-delta resynchronization after reconnect. | M | T |
| API-81006 | Streaming channels shall include: `market.{type}.{instrument}`, `orders`, `fills`, `positions`, `balances`, `deployments`, `risk`, `notifications`, `jobs.{id}`, `copilot.{conversation}`. | M | T |
| API-81007 | The streaming interface shall enforce per-connection subscription limits (default 500) and per-connection outbound rate with conflation for market data. | M | T |
| API-81008 | Internal interfaces shall be covered by consumer-driven contract tests. | M | T |

---

*End of Chapter 81 – Internal APIs*
