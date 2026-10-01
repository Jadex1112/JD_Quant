# Chapter 20 – Market Data Engine

## 20.1 Purpose

The Market Data Engine (MDE), together with the Data Ingestion Layer (DIL) and Data Processing Layer (DPL), acquires, validates, normalizes, stores, aggregates, and distributes market data and instrument reference data to all platform consumers. It is the single source of market data for strategies, risk, portfolio valuation, backtesting, analytics, and AI pipelines.

## 20.2 Scope and Actors

### In Scope

- Instrument and venue reference data management
- Real-time market data ingestion (trades, quotes, order books, candles, funding rates, open interest, index prices, mark prices)
- Historical market data ingestion and storage
- Normalization to canonical formats
- Data validation and quality monitoring
- Candle (OHLCV) aggregation
- Subscription management and distribution
- Data replay for backtesting and diagnostics
- Staleness detection

### Actors

| Actor | Interaction |
|---|---|
| Data Engineer | Configures providers, monitors ingestion, resolves quality issues |
| Strategy Engine, RMS, PME, BTE, FSE | Subscribe to and query market data |
| Exchange / Provider adapters | Deliver raw data |

## 20.3 Domain Entities

### 20.3.1 Venue

| Attribute | Type | Constraints |
|---|---|---|
| code | String(32) | Unique, upper case (e.g. `BINANCE`, `NSE`, `CME`) |
| name | String(100) | Required |
| venue_type | Enum: EXCHANGE, BROKER, DATA_PROVIDER, OTC | Required |
| timezone | IANA timezone | Required |
| calendar_id | Reference → TradingCalendar | Required for EXCHANGE |
| capabilities | Set of Enum | e.g. SPOT, FUTURES, OPTIONS, ORDER_BOOK_L2, ORDER_BOOK_L3, TRADES, CANDLES |

### 20.3.2 Instrument

| Attribute | Type | Constraints |
|---|---|---|
| instrument_id | InstrumentId | Canonical, immutable (CON-123), format `{VENUE}:{SYMBOL}` |
| venue_id | Reference → Venue | Required |
| venue_symbol | String(64) | Venue-native symbol |
| asset_class | Enum: EQUITY, ETF, FUTURE, OPTION, FX, CRYPTO_SPOT, CRYPTO_PERPETUAL, CRYPTO_FUTURE, COMMODITY, FIXED_INCOME, INDEX | Required |
| base_asset | String(32) | Required |
| quote_asset | String(32) | Required |
| settlement_asset | String(32) | Required for derivatives |
| tick_size | Decimal | > 0 |
| lot_size | Decimal | > 0 |
| min_quantity | Decimal | ≥ lot_size |
| max_quantity | Decimal | Optional |
| min_notional | Decimal | Optional |
| price_precision | Integer | 0–18 |
| quantity_precision | Integer | 0–18 |
| contract_multiplier | Decimal | Default 1 |
| expiry | Timestamp | Required for dated derivatives |
| strike / option_type | Decimal / Enum CALL, PUT | Required for options |
| underlying_id | InstrumentId | Required for derivatives |
| status | Enum: ACTIVE, HALTED, SUSPENDED, DELISTED, EXPIRED | Required |
| aliases | List of (source, symbol) | Symbol mappings for providers |
| effective_from / effective_to | Timestamp | Validity interval for point-in-time metadata |

### 20.3.3 Market Data Record Types

| Type | Canonical Fields |
|---|---|
| Trade | instrument_id, exchange_ts, receive_ts, price, quantity, aggressor_side, trade_id |
| Quote (L1) | instrument_id, exchange_ts, receive_ts, bid_price, bid_size, ask_price, ask_size |
| OrderBookSnapshot | instrument_id, exchange_ts, receive_ts, sequence, bids[(price, size)], asks[(price, size)], depth |
| OrderBookDelta | instrument_id, exchange_ts, receive_ts, sequence, side, price, size (0 = remove) |
| Candle | instrument_id, interval, open_ts, close_ts, open, high, low, close, volume, quote_volume, trade_count, vwap, is_closed |
| FundingRate | instrument_id, exchange_ts, rate, next_funding_ts |
| MarkPrice / IndexPrice | instrument_id, exchange_ts, price |
| OpenInterest | instrument_id, exchange_ts, value |
| InstrumentStatus | instrument_id, exchange_ts, status |

Every record carries `source_id` (provider), `exchange_ts` (venue event time), and `receive_ts` (platform receipt time).

### 20.3.4 DataSubscription

| Attribute | Type | Constraints |
|---|---|---|
| subscriber_id | Principal or engine reference | Required |
| instrument_id | InstrumentId | Required |
| data_type | Enum (record types above) | Required |
| interval | Candle interval | Required for Candle |
| depth | Integer | Required for order books; 1–1,000 |
| delivery_mode | Enum: EVERY_UPDATE, CONFLATED | Default EVERY_UPDATE |
| conflation_interval | Duration | Required if CONFLATED; 10 ms – 60 s |

## 20.4 Functional Requirements – Reference Data

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20001 | The system shall maintain a master list of venues and instruments with the attributes defined in 20.3. | M | T |
| FR-20002 | The system shall synchronize instrument metadata from each connected venue at startup and at a configurable interval (default every 6 hours). | M | T |
| FR-20003 | The system shall detect changes to tick size, lot size, minimum notional, and status and shall emit `instrument.updated` with the prior and new values. | M | T |
| FR-20004 | The system shall retain the history of instrument metadata with effective intervals to allow point-in-time lookup (CON-122). | M | T |
| FR-20005 | The system shall resolve any provider symbol to the canonical InstrumentId through the alias table and shall reject unresolved symbols with `INSTRUMENT_NOT_FOUND`. | M | T |
| FR-20006 | The system shall allow a Data Engineer to create and edit instruments manually for venues that do not publish metadata; manual edits shall be audited. | M | T |
| FR-20007 | The system shall mark instruments as EXPIRED at expiry and DELISTED when the venue reports delisting, and shall notify owners of deployments trading them. | M | T |
| FR-20008 | The system shall maintain continuous-contract definitions for futures with configurable roll rules (calendar-based, volume-based, open-interest-based). | S | T |
| FR-20009 | The system shall store corporate actions (splits, dividends, symbol changes, mergers) and provide split- and dividend-adjusted price series as derived datasets (CON-120). | S | T |
| FR-20010 | The system shall provide instrument search by symbol, name, venue, asset class, base asset, and quote asset with results returned within 300 ms for a catalog of 1,000,000 instruments. | M | T |

## 20.5 Functional Requirements – Real-Time Ingestion

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20020 | The system shall ingest real-time trades, quotes, order book snapshots and deltas, candles, mark prices, index prices, funding rates, and open interest from connected venues according to venue capability. | M | T |
| FR-20021 | The system shall record `receive_ts` for every message at the earliest point of receipt within the adapter. | M | I |
| FR-20022 | The system shall normalize all messages to the canonical record types before distribution. | M | T |
| FR-20023 | The system shall maintain local order books from snapshots and deltas, verifying sequence continuity; upon a sequence gap the system shall discard the book, mark it INVALID, and request a fresh snapshot. | M | T |
| FR-20024 | The system shall validate that a maintained order book is not crossed (best bid < best ask); a crossed book shall be flagged and re-synchronized. | M | T |
| FR-20025 | The system shall subscribe to venue streams only for instruments with at least one active subscription, and shall unsubscribe after a configurable idle period (default 5 min) without subscribers. | M | T |
| FR-20026 | The system shall reconnect to disconnected streams with exponential backoff (initial 500 ms, max 30 s, jitter ±20%) and re-subscribe to all prior subscriptions. | M | T |
| FR-20027 | The system shall, after reconnection, backfill missed trades and candles from REST endpoints where the venue supports it, and flag intervals that could not be backfilled as GAP. | S | T |
| FR-20028 | The system shall support primary and secondary providers per instrument and shall fail over automatically when the primary is STALE for longer than the failover threshold (default 5 s). | S | T |

## 20.6 Functional Requirements – Validation and Quality

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20040 | The system shall validate every record for: non-negative sizes, positive prices, price alignment to tick size, timestamp monotonicity within a stream, and timestamps within tolerance of platform time (default ±5 s for real-time). | M | T |
| FR-20041 | The system shall flag price outliers deviating from the rolling median by more than a configurable threshold (default 10 standard deviations over a 100-tick window) without discarding them, and shall publish `marketdata.anomaly.detected`. | M | T |
| FR-20042 | The system shall reject records failing structural validation, retain them in a quarantine store with the failure reason, and increment the quality metric for the source. | M | T |
| FR-20043 | The system shall compute per instrument and per source: message rate, gap count, anomaly count, average and p99 exchange-to-receive latency, and staleness. | M | T |
| FR-20044 | The system shall classify each instrument feed as LIVE, DELAYED, STALE, or DOWN based on time since last update relative to the instrument's expected update frequency. | M | T |
| FR-20045 | The system shall publish `marketdata.stale` and `marketdata.recovered` events on classification changes. | M | T |
| FR-20046 | The system shall allow configuring the staleness threshold per instrument class (default 10 s for liquid instruments, 120 s for illiquid instruments). | M | T |
| FR-20047 | The system shall provide a data quality dashboard showing the metrics of FR-20043 per source and instrument. | M | D |

## 20.7 Functional Requirements – Aggregation

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20060 | The system shall build candles from trades for intervals: 1s, 5s, 15s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w, 1M. | M | T |
| FR-20061 | The system shall align candle boundaries to UTC for intraday intervals and to the venue's session for daily intervals of session-based venues. | M | T |
| FR-20062 | The system shall publish in-progress candle updates (is_closed = false) at a configurable rate and a final candle (is_closed = true) at interval close. | M | T |
| FR-20063 | The system shall close a candle with no trades using the previous close as open, high, low, and close with zero volume, unless configured to omit empty candles. | M | T |
| FR-20064 | The system shall compute VWAP, trade count, and quote volume for each candle. | M | T |
| FR-20065 | The system shall support custom bar types: tick bars, volume bars, dollar bars, and range bars. | S | T |
| FR-20066 | The system shall provide consolidated best bid and offer across venues for instruments with the same economic identity (for example the same crypto pair on multiple exchanges), with quotes converted to a common quote currency where requested. | S | T |

## 20.8 Functional Requirements – Distribution and Subscription

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20080 | The system shall allow internal consumers to subscribe to any record type for any instrument with EVERY_UPDATE or CONFLATED delivery. | M | T |
| FR-20081 | The system shall deliver records to each subscriber in exchange-timestamp order per instrument and data type. | M | T |
| FR-20082 | The system shall deliver the latest snapshot (last trade, quote, book, open candle) immediately on subscription. | M | T |
| FR-20083 | The system shall isolate slow subscribers such that a subscriber falling behind does not delay delivery to other subscribers; slow EVERY_UPDATE subscribers shall be notified with `SUBSCRIBER_LAGGING` and, beyond a configurable buffer (default 100,000 messages), disconnected. | M | T |
| FR-20084 | The system shall expose market data to user interfaces through a streaming interface with per-user rate limiting and conflation (Part E). | M | T |
| FR-20085 | The system shall enforce market data entitlements, preventing users and API clients from receiving data for sources they are not entitled to (CON-066). | M | T |

## 20.9 Functional Requirements – Historical Storage and Query

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20100 | The system shall persist real-time trades, candles, and configurable order book snapshots (default top 20 levels at 1 s) to the time-series store. | M | T |
| FR-20101 | The system shall persist raw messages unmodified for a configurable retention (default 30 days) to allow re-normalization (CON-120). | S | T |
| FR-20102 | The system shall provide historical queries by instrument, data type, interval, and time range with results streamed in time order. | M | T |
| FR-20103 | The system shall return one year of 1-minute candles for one instrument within 2 s. | M | T |
| FR-20104 | The system shall create immutable dataset versions (snapshot identifiers) covering a set of instruments, data types, and time range for use in backtests and training (CON-121). | M | T |
| FR-20105 | The system shall support point-in-time queries that return only data with `receive_ts` (or `exchange_ts` for historical imports) earlier than the query's as-of time. | M | T |
| FR-20106 | The system shall report data coverage per instrument showing available ranges and gaps per data type. | M | D |
| FR-20107 | The system shall support tiered storage, moving data older than a configurable age to lower-cost storage while remaining queryable. | S | T |

## 20.10 Functional Requirements – Replay

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-20120 | The system shall replay stored market data through the same distribution interface used for real-time data. | M | T |
| FR-20121 | The system shall support replay speeds of 1×, 2×, 10×, 100×, and maximum (as fast as consumers accept). | M | T |
| FR-20122 | The system shall drive the replay Clock (CON-010) from record timestamps so that consumers observe simulated time. | M | T |
| FR-20123 | The system shall support pause, resume, and seek operations during interactive replay. | S | T |

## 20.11 Business Rules

| ID | Rule |
|---|---|
| BR-20-01 | Exchange timestamp is authoritative for ordering; receive timestamp is used when exchange timestamp is missing and is flagged. |
| BR-20-02 | A market data record is never modified after persistence; corrections are stored as new records with a `corrects` reference. |
| BR-20-03 | Mid price = (best bid + best ask) / 2; spread = best ask − best bid; spread in bps = spread / mid × 10,000. |
| BR-20-04 | An instrument's last price for valuation purposes is: mark price if available (derivatives), else mid price if the book is valid, else last trade price. |
| BR-20-05 | Data classified STALE shall not be used for new pre-trade valuation without being flagged in the risk decision. |

## 20.12 Events

| Event | Description |
|---|---|
| `marketdata.trade` / `.quote` / `.book` / `.candle` / `.funding` / `.mark` | Streaming data (high-volume topics) |
| `instrument.created` / `instrument.updated` / `instrument.status.changed` | Reference data changes |
| `marketdata.stale` / `marketdata.recovered` | Feed classification changes |
| `marketdata.gap.detected` | Sequence or time gap |
| `marketdata.anomaly.detected` | Outlier detected |
| `dataset.version.created` | Immutable dataset snapshot created |

## 20.13 Configuration

| Key | Default | Description |
|---|---|---|
| `marketdata.reference.sync.interval` | 6 h | Instrument sync period |
| `marketdata.reconnect.initial` / `.max` | 500 ms / 30 s | Backoff bounds |
| `marketdata.stale.threshold.liquid` | 10 s | Staleness threshold |
| `marketdata.stale.threshold.illiquid` | 120 s | Staleness threshold |
| `marketdata.outlier.sigma` | 10 | Outlier threshold |
| `marketdata.book.persist.depth` / `.interval` | 20 / 1 s | Book snapshot persistence |
| `marketdata.raw.retention` | 30 d | Raw message retention |
| `marketdata.subscriber.buffer.max` | 100,000 | Slow subscriber buffer |

## 20.14 Error Conditions

| Code | Condition |
|---|---|
| `INSTRUMENT_NOT_FOUND` | Symbol or identifier cannot be resolved |
| `DATA_NOT_ENTITLED` | Subscriber lacks entitlement |
| `DATA_UNAVAILABLE` | Requested range not stored |
| `BOOK_INVALID` | Order book out of sync |
| `SUBSCRIBER_LAGGING` | Consumer behind delivery |
| `PROVIDER_UNAVAILABLE` | Source disconnected |

## 20.15 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-20001 | Given an order book stream, when a delta with a sequence gap arrives, then the book is marked INVALID, a snapshot is requested, and consumers receive the rebuilt book within 5 s. | FR-20023 |
| AC-20002 | Given a trade stream for 1 hour, then the 1m candles built by the system match candles recomputed offline from the same trades exactly. | FR-20060, FR-20064 |
| AC-20003 | Given no updates for an instrument for longer than its staleness threshold, then `marketdata.stale` is published within 1 s of threshold expiry. | FR-20044, FR-20045 |
| AC-20004 | Given a point-in-time query as of T, then no returned record has a timestamp later than T. | FR-20105 |
| AC-20005 | Given a slow subscriber, then delivery latency to other subscribers is unaffected (p99 within 10% of baseline). | FR-20083 |

---

*End of Chapter 20 – Market Data Engine*
