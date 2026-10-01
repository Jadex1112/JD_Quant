# Chapter 22 – Execution Engine

## 22.1 Purpose

The Execution Management System (EMS) transforms risk-approved orders into venue-specific requests, routes them to the correct venue adapter, manages execution algorithms, enforces rate limits, and translates venue responses into normalized execution reports for the OMS. The EMS owns the "how and where" of execution; the OMS owns the "what" and its state.

## 22.2 Scope and Actors

### In Scope

- Venue routing and order translation
- Rate limiting and request pacing
- Execution algorithms (TWAP, VWAP, POV, Iceberg, Sniper/Limit-chasing)
- Smart order routing across venues (Should)
- Execution report normalization
- Order status resolution for UNKNOWN orders
- Transaction cost capture for execution quality analysis
- Simulated execution for paper trading and backtesting (execution simulator interface)

### Actors

| Actor | Interaction |
|---|---|
| OMS | Hands approved orders; receives execution reports |
| Exchange / Broker adapters | Venue communication (Chapters 45, 46) |
| Quantitative Trader | Selects execution algorithms and parameters |
| Execution Optimizer | Recommends algorithm parameters (Chapter 56) |

## 22.3 Domain Entities

### 22.3.1 ExecutionInstruction

| Attribute | Type | Constraints |
|---|---|---|
| order_id | Reference → Order | Required |
| algorithm | Enum: DIRECT, TWAP, VWAP, POV, ICEBERG, LIMIT_CHASE, SOR | Default DIRECT |
| algorithm_params | Structured document | Validated per algorithm (22.6) |
| venue_preference | List of Venue | For SOR |
| urgency | Enum: LOW, MEDIUM, HIGH | Default MEDIUM |
| start_at / end_at | Timestamp | Algorithm window |

### 22.3.2 ExecutionReport (normalized)

| Attribute | Type | Description |
|---|---|---|
| report_type | Enum: ACK, REJECT, FILL, PARTIAL_FILL, CANCELED, REPLACED, CANCEL_REJECT, REPLACE_REJECT, EXPIRED, STATUS | Required |
| client_order_id / venue_order_id | String | Identifiers |
| fill details | price, quantity, fee, liquidity, venue_trade_id | For fills |
| venue_status | String | Raw venue status for diagnostics |
| reason | Enum + text | Normalized reject reason |
| exchange_ts / receive_ts | Timestamp | Timing |
| sequence | Integer | Venue sequence if available |

### 22.3.3 RateLimitPolicy

| Attribute | Type | Description |
|---|---|---|
| venue_id / account_id | References | Scope |
| limits | List of (bucket, capacity, refill_rate, weight_per_request_type) | Token-bucket definitions mirroring venue limits (CON-041) |
| safety_margin | Percentage | Default 10%; effective capacity = venue limit × (1 − margin) |

### 22.3.4 ExecutionQualityRecord

| Attribute | Description |
|---|---|
| order_id | Parent or direct order |
| arrival_price | Reference price when the order reached the EMS (BR-20-04) |
| decision_price | Reference price when the strategy produced the signal |
| average_fill_price | From OMS |
| implementation_shortfall_bps | See BR-22-02 |
| slippage_bps | See BR-22-03 |
| participation_rate | Filled volume / market volume during the execution window |
| latency_breakdown | signal→OMS, OMS→RMS, RMS→EMS, EMS→venue ack, ack→first fill |

## 22.4 Functional Requirements – Routing and Translation

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-22001 | The system shall route each approved order to the adapter of the venue associated with the order's account. | M | T |
| FR-22002 | The system shall translate canonical orders into venue-specific requests, mapping order type, time in force, flags, and identifiers; unsupported combinations shall be rejected with `ORDER_TYPE_UNSUPPORTED` unless emulation is available (FR-21062). | M | T |
| FR-22003 | The system shall format prices and quantities to the venue's required precision and representation without changing their value. | M | T |
| FR-22004 | The system shall normalize all venue responses into ExecutionReports and deliver them to the OMS in venue sequence order. | M | T |
| FR-22005 | The system shall map venue-specific reject codes to normalized reasons: INSUFFICIENT_BALANCE, PRICE_OUT_OF_BAND, INVALID_QUANTITY, MARKET_CLOSED, RATE_LIMITED, POST_ONLY_WOULD_TAKE, REDUCE_ONLY_VIOLATION, DUPLICATE_ORDER, UNKNOWN_INSTRUMENT, VENUE_ERROR, OTHER; the raw venue message shall be retained. | M | T |
| FR-22006 | The system shall add no more than 100 μs p99 of internal processing latency between receiving an approved DIRECT order and handing it to the adapter's transport. | M | T |

## 22.5 Functional Requirements – Rate Limiting and Resilience

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-22020 | The system shall enforce per-venue and per-account token-bucket rate limits configured from venue documentation with a safety margin. | M | T |
| FR-22021 | The system shall prioritize requests when throttled: cancel requests first, then reduce-only and closing orders, then modifications, then new orders. | M | T |
| FR-22022 | The system shall queue throttled new orders for at most a configurable maximum delay (default 2 s) and reject them with `RATE_LIMITED` afterwards; cancels shall never be dropped. | M | T |
| FR-22023 | The system shall read venue-reported rate limit usage headers where available and adapt local limits dynamically. | S | T |
| FR-22024 | The system shall, upon receiving a venue rate-limit or ban response, suspend new order submission to that venue for the venue-specified back-off period and raise a High alert. | M | T |
| FR-22025 | The system shall resolve orders in UNKNOWN state by querying the venue by client order identifier with retries (1 s, 2 s, 4 s, 8 s, 16 s); if unresolved after all retries the order shall remain UNKNOWN and a Critical alert shall be raised. | M | T |
| FR-22026 | The system shall apply a circuit breaker per venue: after N consecutive transport failures (default 5) within 30 s the venue is marked DEGRADED and new orders are rejected with `VENUE_UNAVAILABLE` until a successful health probe. | M | T |

## 22.6 Functional Requirements – Execution Algorithms

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-22040 | The system shall support DIRECT execution, sending the order to the venue unchanged. | M | T |
| FR-22041 | The system shall support TWAP execution: parameters start_at, end_at, slice_interval (≥ 1 s), randomization (0–50%), limit_price (optional), max_participation (optional). Child order quantities shall be distributed uniformly over the window with the configured randomization. | M | T |
| FR-22042 | The system shall support VWAP execution targeting a historical intraday volume profile (default: 20-day average profile at the slice interval). | S | T |
| FR-22043 | The system shall support POV (percentage of volume) execution with target participation 1–50%, measured over a rolling window, with min and max child sizes. | S | T |
| FR-22044 | The system shall support ICEBERG execution displaying a configurable visible quantity with optional variance, replenishing after each visible slice fills. | M | T |
| FR-22045 | The system shall support LIMIT_CHASE execution: a passive limit order is repriced toward the touch at a configured interval, up to a maximum number of reprices or a maximum distance from arrival price, then optionally crosses the spread. | M | T |
| FR-22046 | The system shall submit every algorithm child order as a separate OMS order with parent_order_id set; each child order shall obtain its own RMS decision. | M | T |
| FR-22047 | The system shall stop an algorithm and cancel working children when the parent is canceled, the deployment is paused, or a kill switch applies. | M | T |
| FR-22048 | The system shall report algorithm progress: filled quantity, percentage complete, schedule deviation, average price, and projected completion. | M | D |
| FR-22049 | The system shall support smart order routing (SOR) across multiple accounts on different venues for the same economic instrument, splitting orders by available liquidity and fees; SOR shall only use accounts the order owner is permitted to use. | C | T |

## 22.7 Functional Requirements – Execution Quality

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-22060 | The system shall record an ExecutionQualityRecord for every completed direct or parent order. | M | T |
| FR-22061 | The system shall record latency timestamps at each hop of the order path (signal generated, OMS received, RMS decided, EMS received, sent to venue, venue ack, first fill). | M | T |
| FR-22062 | The system shall publish execution quality metrics to the Analytics engine (Chapter 31). | M | T |

## 22.8 Functional Requirements – Simulated Execution

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-22080 | The system shall provide an execution simulator implementing the same adapter interface as live venues, used by paper trading and backtesting (CON-005). | M | T |
| FR-22081 | The simulator shall support fill models: IMMEDIATE (fill at reference price), BOOK (walk the order book), QUEUE (limit orders fill when price trades through, with configurable queue-position estimation), and PROBABILISTIC (fill probability as function of distance to touch). | M | T |
| FR-22082 | The simulator shall apply configurable latency (fixed, uniform, or empirical distribution) to order acknowledgment and fills. | M | T |
| FR-22083 | The simulator shall apply fees from the account's fee schedule, distinguishing maker and taker. | M | T |
| FR-22084 | The simulator shall apply slippage models: NONE, FIXED_BPS, VOLUME_IMPACT (square-root impact model with configurable coefficient). | M | T |
| FR-22085 | The simulator shall reproduce venue validation rules (tick size, lot size, min notional, post_only rejection, reduce_only rejection). | M | T |
| FR-22086 | The simulator shall be deterministic for a given seed, dataset, and configuration (QAS-09). | M | T |

## 22.9 Business Rules

| ID | Rule |
|---|---|
| BR-22-01 | Arrival price is the reference price (BR-20-04) at the instant the EMS receives the order. |
| BR-22-02 | Implementation shortfall (bps) = side_sign × (average_fill_price − decision_price) / decision_price × 10,000, where side_sign = +1 for BUY and −1 for SELL. Positive values indicate cost. |
| BR-22-03 | Slippage (bps) = side_sign × (average_fill_price − arrival_price) / arrival_price × 10,000. |
| BR-22-04 | Algorithm child orders inherit account, deployment, instrument, side, and tags from the parent. |
| BR-22-05 | The sum of child order quantities (working + filled) shall never exceed the parent's quantity. |

## 22.10 Events

| Event | Description |
|---|---|
| `execution.report` | Normalized ExecutionReport (internal, OMS consumer) |
| `execution.algo.progress` | Algorithm progress update |
| `execution.algo.completed` | Algorithm finished with quality summary |
| `execution.venue.degraded` / `.recovered` | Circuit breaker transitions |
| `execution.ratelimit.exceeded` | Venue rate limit response received |

## 22.11 Configuration

| Key | Default | Description |
|---|---|---|
| `ems.ratelimit.safety.margin` | 10% | Rate limit safety margin |
| `ems.throttle.max.delay` | 2 s | Max queue delay for new orders |
| `ems.circuit.failures` / `.window` | 5 / 30 s | Circuit breaker thresholds |
| `ems.unknown.retry.schedule` | 1,2,4,8,16 s | UNKNOWN resolution retries |
| `ems.sim.fill.model` | QUEUE | Default simulator fill model |
| `ems.sim.latency.ms` | 50 | Default simulated latency |

## 22.12 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-22001 | Given a venue limit of 10 orders/s and 30 new orders plus 5 cancels submitted in 1 s, then cancels are sent first, at most 9 requests/s are sent, and excess new orders wait up to 2 s before rejection. | FR-22020 – FR-22022 |
| AC-22002 | Given a TWAP of 1,000 units over 10 minutes with 1-minute slices and 0% randomization, then 10 child orders of 100 units are created at 1-minute intervals. | FR-22041 |
| AC-22003 | Given the same backtest executed twice with the same seed, then simulated fills are identical. | FR-22086 |
| AC-22004 | Given 5 consecutive transport failures, then the venue is DEGRADED and new orders are rejected with VENUE_UNAVAILABLE until a health probe succeeds. | FR-22026 |

---

*End of Chapter 22 – Execution Engine*
