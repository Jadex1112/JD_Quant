# Chapter 85 – Broker APIs

## 85.1 Purpose

This chapter specifies interface requirements toward brokerage APIs, complementing Chapter 46.

## 85.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-85001 | Broker adapters shall support the broker's supported interface types, which may include REST, WebSocket, proprietary socket APIs via local gateway processes, and FIX. | M | T |
| API-85002 | Where a broker requires a locally running gateway, the platform shall supervise the gateway process: start, health check, restart, and scheduled re-authentication (FR-46002). | S | T |
| API-85003 | Broker adapters shall handle OAuth-based authorization flows, including refresh token management stored in the secret store. | M | T |
| API-85004 | Broker adapters shall map broker contract identifiers to canonical instruments (FR-46003) and broker order statuses to canonical states (21.4). | M | T |
| API-85005 | Broker adapters shall respect broker pacing rules and message limits (FR-46008). | M | T |
| API-85006 | Broker adapters shall retrieve account summaries (net liquidation, buying power, margin) and map them to the canonical margin model (FR-46006). | M | T |
| API-85007 | Broker adapters shall handle broker-specific session events (disconnections at scheduled maintenance, forced logouts) as connectivity events (FR-45015). | M | T |
| API-85008 | Broker adapters shall pass the conformance suite plus broker extension tests (FR-46011). | M | T |

---

*End of Chapter 85 – Broker APIs*
