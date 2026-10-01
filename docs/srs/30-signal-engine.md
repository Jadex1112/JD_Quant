# Chapter 30 – Signal Engine

## 30.1 Purpose

The Signal Engine manages trading signals: normalized, timestamped expressions of a trading view (direction, strength, horizon) produced by strategies, AI models, rules, external sources, or users. It decouples signal generation from order generation, enabling signals to be recorded, evaluated, combined, shared across strategies, and audited as part of the decision chain (QAS-05).

## 30.2 Domain Entities

### 30.2.1 SignalDefinition

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| source_type | Enum: STRATEGY, MODEL, RULE, EXTERNAL_WEBHOOK, MANUAL, COMPOSITE | Required |
| output_type | Enum: DIRECTION (−1, 0, +1), SCORE (continuous), PROBABILITY ([0, 1]), TARGET_WEIGHT, TARGET_POSITION, PRICE_TARGET | Required |
| horizon | Duration | Intended prediction horizon |
| instruments | Universe definition | Required |
| owner_id | Principal | Required |
| visibility | Enum: PRIVATE, WORKSPACE | Default PRIVATE |

### 30.2.2 Signal (instance)

| Attribute | Type | Description |
|---|---|---|
| signal_id | UUID | Unique |
| definition_id | Reference | Required |
| instrument_id | InstrumentId | Required |
| value | Decimal | Per output_type |
| confidence | Decimal [0, 1] | Optional |
| generated_at | Timestamp | Simulated or real clock time |
| valid_until | Timestamp | generated_at + horizon unless specified |
| inputs_ref | Reference | Features or market data snapshot reference |
| model_ref | (model_id, version) | For MODEL sources (CON-145) |
| explanation | Structured | Top contributing features (optional) |
| metadata | Map | Up to 50 keys |

### 30.2.3 CompositeSignal

| Attribute | Description |
|---|---|
| components | List of (definition_id, weight, transform) |
| combination | Enum: WEIGHTED_SUM, MAJORITY_VOTE, UNANIMOUS, RANK_AVERAGE |
| normalization | Enum: NONE, ZSCORE, RANK, MINMAX over rolling window |

## 30.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-30001 | The system shall allow users to register signal definitions with the attributes in 30.2.1. | M | T |
| FR-30002 | The system shall accept signal instances from strategies via the context service, from models via the Inference Pipeline, from rules, from authenticated external webhooks, and from manual entry. | M | T |
| FR-30003 | The system shall validate signal values against the output type and reject invalid values with `SIGNAL_INVALID`. | M | T |
| FR-30004 | The system shall persist every signal instance with full attributes. | M | T |
| FR-30005 | The system shall distribute signals to subscribing strategies through the on_signal callback in generated_at order. | M | T |
| FR-30006 | The system shall compute composite signals from components on each component update according to the combination rule. | S | T |
| FR-30007 | The system shall evaluate signal quality by computing, per definition and horizon: hit rate, information coefficient (rank correlation of signal value with forward return), IC decay across horizons, turnover, and coverage. | M | T |
| FR-30008 | The system shall present signal quality over time with configurable rolling windows. | M | D |
| FR-30009 | The system shall authenticate external webhook signals with per-source secrets and signatures, enforce rate limits, and reject signals older than a configurable age (default 30 s). | M | T |
| FR-30010 | The system shall support expiry: signals beyond valid_until are not delivered to strategies and are marked expired. | M | T |
| FR-30011 | The system shall link orders to the signal that triggered them (FR-24028) and provide a signal-to-execution view showing latency and outcome. | M | T |
| FR-30012 | The system shall support signal backfill: generating historical signal instances for a definition over a date range for research, stored separately from live signals. | S | T |
| FR-30013 | The system shall provide a signal feed view showing latest signals per instrument with value, confidence, age, and source. | M | D |

## 30.4 Business Rules

| ID | Rule |
|---|---|
| BR-30-01 | Information coefficient = Spearman rank correlation between signal values at time t and forward returns over the horizon, computed cross-sectionally where ≥ 5 instruments, otherwise time-series. |
| BR-30-02 | A signal does not by itself cause an order; a strategy must translate it (CON-140). |

## 30.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-30001 | Given a PROBABILITY signal with value 1.2, then it is rejected with SIGNAL_INVALID. | FR-30003 |
| AC-30002 | Given a webhook signal with an invalid signature, then it is rejected and a security event logged. | FR-30009 |
| AC-30003 | Given an order generated in response to a signal, then the order detail shows the signal and the signal view shows the order. | FR-30011 |

---

*End of Chapter 30 – Signal Engine*
