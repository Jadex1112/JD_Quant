# Chapter 63 – Feature Store

## 63.1 Purpose

The Feature Store Engine (FSE) defines, computes, stores, and serves features — derived numerical inputs for models and strategies — with identical definitions offline (training, backtesting) and online (live inference), guaranteeing point-in-time correctness (CON-122) and eliminating training-serving skew.

## 63.2 Domain Entities

### 63.2.1 FeatureDefinition

| Attribute | Description |
|---|---|
| name, version | Identity; immutable per version |
| entity | Key type: INSTRUMENT, INSTRUMENT_PAIR, PORTFOLIO, MARKET |
| inputs | Market data types, other features, external datasets |
| transformation | Declarative expression or registered function (e.g. rolling z-score of 1h returns over 24 windows) |
| frequency | Update frequency or event trigger (e.g. on 1m candle close) |
| lookback | Required history |
| value_type | FLOAT, INTEGER, BOOLEAN, CATEGORY, VECTOR |
| owner, description, tags | Metadata |

### 63.2.2 FeatureSet

| Attribute | Description |
|---|---|
| name, version | Identity |
| features | List of (feature name, version) |
| entity | Common entity key |

## 63.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| AI-63001 | The system shall allow users to define features and feature sets with the attributes of 63.2, including a built-in transformation library (returns, rolling statistics, indicators from FR-24009, cross-sectional ranks, lags, differences, ratios). | M | T |
| AI-63002 | The system shall compute features offline over historical ranges (materialization) as Task Engine jobs, producing versioned feature datasets. | M | T |
| AI-63003 | The system shall compute features online incrementally from live market data at their defined frequency and store the latest values in a low-latency online store. | M | T |
| AI-63004 | The system shall guarantee that online and offline computations of the same feature version produce identical values for identical inputs (tested by parity checks). | M | T |
| AI-63005 | The system shall provide point-in-time joins: for a set of (entity, timestamp) rows, return feature values as they were available at each timestamp, respecting each feature's availability delay. | M | T |
| AI-63006 | The system shall serve online feature vectors for a feature set and entity within 5 ms p99. | M | T |
| AI-63007 | The system shall track feature freshness and mark features STALE when not updated within 2× their frequency; inference consumers shall receive the staleness flag. | M | T |
| AI-63008 | The system shall compute and display feature statistics (distribution, missing rate, correlation with other features and with forward returns). | S | D |
| AI-63009 | The system shall record lineage from features to inputs and from models to feature sets. | M | T |
| AI-63010 | The system shall run scheduled online/offline parity checks and alert on divergence beyond tolerance (default 1e-9 relative). | M | T |
| AI-63011 | The system shall expose features to strategies through the context service (Chapter 24.4) with the same point-in-time semantics in backtests. | M | T |

## 63.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-63001 | Given a feature computed offline and online over the same day of data, then values match within 1e-9 relative tolerance. | AI-63004 |
| AC-63002 | Given a point-in-time join at T for a daily feature published at 00:05 UTC, then the value returned for T = 00:03 is the previous day's value. | AI-63005 |

*Part C – Functional Requirements is complete.*

---

*End of Chapter 63 – Feature Store*
