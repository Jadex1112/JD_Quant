# Chapter 84 – Exchange APIs

## 84.1 Purpose

This chapter specifies interface requirements toward external exchange APIs, complementing the functional requirements of Chapter 45.

## 84.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-84001 | Adapters shall support the exchange's native protocols: REST/HTTPS, WebSocket, and FIX 4.2/4.4 where offered and required by the venue selection (Volume 3). | M (REST, WebSocket), S (FIX) | T |
| API-84002 | Adapters shall implement the exchange's authentication and request-signing scheme exactly per venue specification (FR-45008). | M | T |
| API-84003 | Adapters shall map every exchange field used to canonical types without precision loss, parsing numeric strings directly into decimals (CON-022). | M | T |
| API-84004 | Adapters shall use client order identifiers accepted by the exchange (respecting length and character constraints) and map them to OMS identifiers (FR-21002). | M | T |
| API-84005 | Adapters shall track and respect each exchange's rate-limit model (request weight, order count, per IP / per key) (CON-041, FR-22020). | M | T |
| API-84006 | Adapters shall handle exchange-specific error codes by mapping to normalized reasons (FR-22005) and classifying them as retryable or non-retryable. | M | T |
| API-84007 | Adapters shall pin exchange API versions and detect deprecation notices; API version changes shall be handled through adapter releases passing the conformance suite (FR-45002). | M | I |
| API-84008 | Adapters shall validate exchange TLS certificates and never disable certificate verification. | M | T |
| API-84009 | Adapters shall record exchange request/response metadata for diagnostics (FR-45011) with secrets and signatures excluded. | M | T |
| API-84010 | Adapters shall synchronize to exchange server time for signed requests (FR-45007). | M | T |

---

*End of Chapter 84 – Exchange APIs*
