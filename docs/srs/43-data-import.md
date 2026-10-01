# Chapter 43 – Data Import

## 43.1 Purpose

The Data Import module, part of the Data Ingestion Layer (DIL), brings external data into the platform: historical market data, alternative data, reference data, trade history from venues, and user-provided datasets. It provides validated, resumable, auditable import pipelines producing versioned datasets.

## 43.2 Import Sources and Formats

| Source Type | Examples |
|---|---|
| Venue historical APIs | Historical candles, trades, funding rates |
| Bulk data vendor files | Tick archives, end-of-day files |
| User uploads | CSV, JSON, Parquet, Excel |
| Object storage | Files in configured buckets |
| Venue account history | Past orders, fills, deposits/withdrawals, for reconciliation and onboarding |
| Alternative data | News, sentiment, economic calendars, fundamentals |

Supported formats: CSV (configurable delimiter, header, quoting), JSON / JSON Lines, Parquet, Excel (XLSX), and compressed variants (gzip, zstd, zip).

## 43.3 Domain Entities

### 43.3.1 ImportJob

| Attribute | Description |
|---|---|
| source | Source type and location |
| target_dataset | Dataset type (e.g. CANDLES, TRADES, FILLS, CUSTOM) and name |
| mapping | Column-to-field mapping with transformations (unit, timezone, scaling) |
| validation_rules | Built-in and custom |
| conflict_policy | SKIP_EXISTING, OVERWRITE_AS_NEW_VERSION, FAIL |
| status | PENDING, VALIDATING, IMPORTING, COMPLETED, COMPLETED_WITH_ERRORS, FAILED, CANCELED |
| stats | Rows read, imported, rejected, duplicates |
| checkpoint | Resume position |
| resulting_dataset_version | Reference (CON-121) |

## 43.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-43001 | The system shall import historical candles, trades, and funding rates from connected venues for user-selected instruments and ranges, respecting venue rate limits. | M | T |
| FR-43002 | The system shall import user files in the formats of 43.2 through upload (up to 5 GB per file) or object-storage reference. | M | T |
| FR-43003 | The system shall provide a mapping step with auto-detection of columns, preview of the first 100 parsed rows, and saved mapping templates. | M | D |
| FR-43004 | The system shall convert timestamps to UTC using a declared source timezone or offset (CON-023). | M | T |
| FR-43005 | The system shall validate imported market data using the rules of FR-20040 plus OHLC consistency (low ≤ open, close ≤ high; volume ≥ 0). | M | T |
| FR-43006 | The system shall write rejected rows to an error file with row number and reason, downloadable by the user. | M | T |
| FR-43007 | The system shall import in chunks with checkpoints so a failed or interrupted import resumes without duplicating rows. | M | T |
| FR-43008 | The system shall detect duplicates by natural key (instrument, timestamp, interval / trade id) and apply the conflict policy. | M | T |
| FR-43009 | The system shall produce a new immutable dataset version on successful import and never modify existing versions (CON-120, CON-121). | M | T |
| FR-43010 | The system shall import venue account history (orders, fills, transfers) to establish initial positions and history for newly connected accounts, flagged as IMPORTED. | M | T |
| FR-43011 | The system shall support custom datasets with user-defined schemas for alternative data, joinable to market data by instrument and timestamp. | S | T |
| FR-43012 | The system shall report import progress, throughput, and estimated completion. | M | D |
| FR-43013 | The system shall import at least 10 million rows per minute for CSV candle data on reference hardware. | S | T |
| FR-43014 | The system shall scan uploaded files for malware and reject executable content. | M | T |
| FR-43015 | The system shall record data lineage (source, import job, mapping version) for every dataset version. | M | T |

## 43.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-43001 | Given an import interrupted at 60%, when resumed, then the final dataset contains each source row exactly once. | FR-43007 |
| AC-43002 | Given a CSV with 3 rows where high < low, then those rows are rejected and listed in the error file. | FR-43005, FR-43006 |
| AC-43003 | Given a re-import with OVERWRITE_AS_NEW_VERSION, then the previous dataset version remains unchanged and queryable. | FR-43009 |

---

*End of Chapter 43 – Data Import*
