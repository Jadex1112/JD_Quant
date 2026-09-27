# Chapter 45 – Exchange Connectivity

## 45.1 Purpose

The Exchange Connectivity module provides adapters connecting JD Quant AI to exchanges for market data and trading. All exchanges are integrated through a standard adapter interface (CON-006) so that the OMS, EMS, MDE, and PME remain venue-agnostic.

## 45.2 Adapter Interface

Every exchange adapter shall implement the following capability groups. Capability support is declared in the adapter manifest.

| Capability Group | Operations |
|---|---|
| Lifecycle | connect, disconnect, health, capabilities |
| Reference Data | list instruments, instrument details, trading rules, fee schedule, server time |
| Market Data Streaming | subscribe/unsubscribe trades, quotes, book deltas, candles, mark/index price, funding |
| Market Data REST | historical candles, trades, book snapshot, funding history |
| Trading | place order, cancel order, cancel all, modify/replace order, batch place/cancel |
| Order Status | get order by client or venue id, list open orders, order history, fill history |
| Account | balances, positions, margin info, leverage settings, position mode |
| Account Streaming | order updates, fill updates, balance updates, position updates |
| Permissions | API key permissions (if exposed) |

## 45.3 Domain Entities

### 45.3.1 Connection

| Attribute | Type | Description |
|---|---|---|
| venue_id | Reference | Target venue |
| environment | Enum: PRODUCTION, TESTNET | Required |
| credential_ref | Secret reference | API key/secret/passphrase stored in secret store |
| endpoint_overrides | URLs | Optional (e.g. regional endpoints) |
| status | Enum: DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, AUTH_FAILED, DISABLED | System-managed |
| last_heartbeat_at | Timestamp | |
| latency_ms | Rolling p50/p99 | |
| clock_offset_ms | Local vs venue server time | |

## 45.4 Initial Venue Support

The first release shall include adapters for at least the following venue categories, with specific venues selected in Volume 3:

| Category | Minimum Count | Pri |
|---|---|---|
| Crypto spot exchanges | 2 | M |
| Crypto derivatives (perpetual/futures) exchanges | 2 | M |
| Equity/ETF exchange access via broker (Chapter 46) | 1 | M |
| Futures exchange access via broker | 1 | S |
| FX via broker | 1 | S |

## 45.5 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-45001 | The system shall provide adapters implementing the interface of 45.2 for the venues selected under 45.4. | M | T |
| FR-45002 | The system shall provide an adapter conformance test suite that every adapter must pass before release, covering all declared capabilities against a testnet or recorded session (QAS-07). | M | T |
| FR-45003 | The system shall allow users to create connections by selecting a venue, environment, and entering credentials; credentials shall be written directly to the secret store and never displayed again (CON-080). | M | T |
| FR-45004 | The system shall test a connection upon creation: authentication, permission inspection (FR-19003), server time offset, and a read-only account call. | M | T |
| FR-45005 | The system shall maintain streaming connections with heartbeat/ping per venue protocol and detect silent disconnection within 2× the heartbeat interval. | M | T |
| FR-45006 | The system shall reconnect automatically with backoff (FR-20026), re-authenticate, re-subscribe, and trigger order, fill, balance, and position reconciliation (FR-21083, FR-23010, FR-28009). | M | T |
| FR-45007 | The system shall monitor venue server time offset and warn when it exceeds a threshold (default 500 ms), correcting request timestamps where the venue requires signed timestamps. | M | T |
| FR-45008 | The system shall sign requests per venue specification (e.g. HMAC-SHA256, Ed25519, RSA) within the adapter. | M | T |
| FR-45009 | The system shall handle venue maintenance announcements and status endpoints, marking the connection DEGRADED and emitting an event during maintenance. | S | T |
| FR-45010 | The system shall support multiple connections per venue (e.g. several sub-accounts) with independent rate-limit tracking per venue key. | M | T |
| FR-45011 | The system shall record raw request/response metadata (not secrets) for trading calls for 30 days for diagnostics. | M | T |
| FR-45012 | The system shall expose connection status, latency, error rates, and rate-limit usage per connection on the connectivity dashboard. | M | D |
| FR-45013 | The system shall support testnet environments where available so that integration tests do not use production funds (ASM-005). | M | T |
| FR-45014 | The system shall allow credential rotation without disrupting active deployments: the new credential is validated, swapped atomically, and the old one discarded. | M | T |
| FR-45015 | The system shall emit `connection.state.changed` on every status transition. | M | T |

## 45.6 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-45001 | Given a new adapter, then it passes 100% of the conformance tests for its declared capabilities before release. | FR-45002 |
| AC-45002 | Given a network drop of 20 s, then the adapter reconnects, re-subscribes, reconciles, and the OMS reflects any fills that occurred during the outage. | FR-45006 |
| AC-45003 | Given a credential entered in the UI, then no API endpoint, log, or export returns its value. | FR-45003 |

---

*End of Chapter 45 – Exchange Connectivity*
