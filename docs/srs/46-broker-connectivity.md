# Chapter 46 – Broker Connectivity

## 46.1 Purpose

The Broker Connectivity module integrates brokerage platforms providing access to equities, ETFs, futures, options, and FX. Brokers differ from direct exchange access in session semantics, multi-venue routing performed by the broker, corporate actions, margin models, and account structures. Broker adapters implement the same adapter interface as exchanges (45.2) plus broker-specific extensions.

## 46.2 Broker-Specific Extensions

| Extension | Description |
|---|---|
| Session management | Broker login sessions, gateway processes, daily re-authentication |
| Contract resolution | Resolve instruments to broker contract identifiers (e.g. symbol + exchange + currency + security type) |
| Order routing destination | Broker smart routing or explicit exchange destination |
| Extended hours | Pre-market and post-market flags |
| Account models | Cash, margin (Reg-T or portfolio margin equivalents), multiple currencies |
| Corporate actions | Broker notifications of splits, dividends, symbol changes |
| Market data entitlements | Broker-provided data subscriptions per exchange |
| Pacing rules | Broker historical data request pacing |

## 46.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-46001 | The system shall provide at least one broker adapter supporting equities and ETFs for the initial release (45.4). | M | T |
| FR-46002 | The system shall manage broker sessions including scheduled re-authentication, gateway health, and session expiry warnings. | M | T |
| FR-46003 | The system shall resolve canonical instruments to broker contracts and cache mappings with validation. | M | T |
| FR-46004 | The system shall support broker order routing to SMART or explicit destinations as allowed by the broker. | S | T |
| FR-46005 | The system shall support extended-hours flags when the account is permitted (FR-19031). | S | T |
| FR-46006 | The system shall map broker margin data (initial, maintenance, excess liquidity, buying power) into the canonical margin model used by the RMS. | M | T |
| FR-46007 | The system shall ingest broker corporate action notifications and apply them via FR-28015. | S | T |
| FR-46008 | The system shall respect broker market data entitlements and pacing rules for historical requests. | M | T |
| FR-46009 | The system shall support multiple currencies per broker account, tracking cash per currency. | M | T |
| FR-46010 | The system shall support options chains retrieval and options order entry for brokers that support options, including multi-leg orders where supported. | C | T |
| FR-46011 | The system shall pass the adapter conformance suite (FR-45002) plus broker extension tests. | M | T |
| FR-46012 | The system shall detect and report broker-side order modifications (e.g. broker cancels at session end for DAY orders) through normal execution reports. | M | T |

## 46.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-46001 | Given a broker session expiring at 23:45, then the operator is warned 15 minutes before and re-authentication occurs per schedule without losing order state. | FR-46002 |
| AC-46002 | Given a 2:1 split notification, then open positions double in quantity and halve in average price. | FR-46007 |

---

*End of Chapter 46 – Broker Connectivity*
